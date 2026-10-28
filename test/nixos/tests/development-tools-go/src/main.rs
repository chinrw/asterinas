// SPDX-License-Identifier: MPL-2.0

//! The test suite for Go standard library on Asterinas NixOS.
//!
//! Supplements the `Go` coverage in `development-tools`.
//!
//! See `test/nixos/README.md#documentation-maintenance` for sync requirements
//! between this test suite and the corresponding "Verified Usage" book section.

use nixos_test_framework::*;

nixos_test_main!();

#[nixos_test]
fn go_stdlib(nixos_shell: &mut Session) -> Result<(), Error> {
    // `go test -short <pkg>` prints one summary line per package.
    // From Go Source: src/cmd/go/internal/test/test.go. Fields are TAB-separated.
    //
    //   ok  \t<pkg>\t<time>[\s<coverage>][\s[no tests to run]]
    //   ok  \t<pkg>\t(cached)
    //   ?   \t<pkg>\t[no test files]
    //   FAIL\t<pkg>\t<time>                        (test failure; preceded
    //                                               by `--- FAIL: TestFoo`)
    //   FAIL\t<pkg> [build failed]                 (compile error; space,
    //                                               not tab, before `[`)
    //
    // Each invocation runs `go test -short <pkg>` for exactly one package,
    // so the test command prints only that package's result. A successful
    // run ends with an `ok` or `?` summary line; a failed run prints `FAIL`
    // and may include `--- FAIL:` details, but it does not also emit an
    // `ok` / `?` summary for the same invocation. Because of that,
    // matching the success summary line is sufficient here without also
    // anchoring the package name.
    let success_regex = Regex::new(r"(?m)^(?:ok|\?)\s+").map_err(|err| Error::Pty(err.into()))?;

    let testcases =
        std::fs::read_to_string("src/testcases.txt").map_err(|err| Error::Pty(err.into()))?;
    let mut failed_tests = Vec::new();
    for testcase in testcases
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
    {
        let cmd = format!("go test -short {testcase}");
        if let Err(err) = nixos_shell.run_cmd_and_expect_regex(&cmd, &success_regex) {
            failed_tests.push((testcase.to_string(), err));
        }
    }

    if !failed_tests.is_empty() {
        println!("=== Failed Go stdlib tests ===");
        for (test, _) in &failed_tests {
            println!("  - {test}");
        }
        println!("==============================");

        return Err(Error::Aggregated {
            summary: "Selected Go stdlib tests failed!".to_string(),
            collections: failed_tests,
        });
    }

    Ok(())
}

// Measure host-observed command completion, including the exit status and barrier.
#[nixos_test]
fn notingo_host_sample(shell: &mut Session) -> Result<(), Error> {
    use std::{
        sync::{
            Arc,
            atomic::{AtomicBool, AtomicU8, Ordering},
            mpsc,
        },
        time::{Duration, Instant},
    };

    let name = std::env::var("PROBE_HOST_SAMPLE").expect("Sample helper");
    assert!(matches!(name.as_str(), "minimal" | "sleep20" | "sleep1000"));
    let sample_id: u64 = std::env::var("PROBE_SAMPLE_ID").unwrap().parse().unwrap();
    let marker = std::env::var("PROBE_HOST_MARKER").unwrap();
    let owner = std::env::var("PROBE_QEMU_OWNER").unwrap();
    let killer = std::env::var("PROBE_QEMU_CONTROL").unwrap();
    let digest = std::env::var("PROBE_HELPER_SHA256").unwrap();
    let check = Regex::new(&format!(r"(?m)^{}\s+", digest)).unwrap();
    shell.run_cmd_and_expect_regex(&format!("sha256sum /etc/notingo/{name}"), &check)?;
    let identity = std::fs::read_to_string(&owner).map_err(|e| Error::Pty(e.into()))?;
    let fields: Vec<_> = identity.split_whitespace().collect();
    assert_eq!(fields.len(), 3);
    let pid: u32 = fields[0].parse().unwrap();
    let process_start: u64 = fields[1].parse().unwrap();
    assert_eq!(fields[2].parse::<u64>().unwrap(), sample_id);
    std::fs::write(format!("{marker}.started"), "started\n").map_err(|e| Error::Pty(e.into()))?;

    // One state transition arbitrates completion against the absolute deadline.
    let state = Arc::new(AtomicU8::new(0));
    let watchdog_state = state.clone();
    let kill_ok = Arc::new(AtomicBool::new(true));
    let watchdog_kill_ok = kill_ok.clone();
    let (finished_tx, finished_rx) = mpsc::channel();
    let started = Instant::now();
    let deadline = started + Duration::from_secs(3);
    let watchdog = std::thread::spawn(move || {
        if finished_rx
            .recv_timeout(deadline.saturating_duration_since(Instant::now()))
            .is_err()
            && watchdog_state
                .compare_exchange(0, 2, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
        {
            let stopped = std::process::Command::new("python3")
                .args([&killer, "--stop", &owner])
                .status()
                .is_ok_and(|s| s.success());
            watchdog_kill_ok.store(stopped, Ordering::SeqCst);
        }
    });
    let barrier = Regex::new(r"(?m)^BARRIER_OK\s*$").unwrap();
    let result =
        shell.run_cmd_and_expect_regex(&format!("GOMAXPROCS=1 /etc/notingo/{name} poll"), &barrier);
    let elapsed = started.elapsed();
    let completed = state
        .compare_exchange(0, 1, Ordering::SeqCst, Ordering::SeqCst)
        .is_ok();
    let _ = finished_tx.send(());
    let joined = watchdog.join().is_ok();
    let outcome = if !joined || !kill_ok.load(Ordering::SeqCst) {
        "watchdog_error"
    } else if elapsed > Duration::from_secs(3) {
        "deadline"
    } else if !completed {
        "deadline_race"
    } else {
        match &result {
            Ok(()) => "pass",
            Err(Error::NonZeroExit { .. } | Error::UnexpectedOutput { .. }) => "helper_failure",
            Err(_) => "guest_error",
        }
    };
    let (error_kind, exit_status) = match &result {
        Ok(()) => ("none", "null".to_string()),
        Err(Error::NonZeroExit { exit_status, .. }) => ("nonzero_exit", exit_status.to_string()),
        Err(Error::UnexpectedOutput { .. }) => ("missing_barrier", "null".to_string()),
        Err(Error::Timeout { .. }) => ("framework_timeout", "null".to_string()),
        Err(Error::Protocol { .. }) => ("protocol", "null".to_string()),
        Err(Error::Pty(_)) => ("pty", "null".to_string()),
        Err(Error::Aggregated { .. }) => ("aggregate", "null".to_string()),
    };
    let record = format!(
        "{{\"sample_id\":{sample_id},\"helper\":\"{name}\",\"outcome\":\"{outcome}\",\"helper_sha256\":\"{digest}\",\"error_kind\":\"{error_kind}\",\"exit_status\":{exit_status},\"deadline_ms\":3000,\"elapsed_ns\":{},\"qemu_pid\":{pid},\"qemu_start_ticks\":{process_start},\"endpoint\":\"command_exit_and_barrier\"}}\n",
        elapsed.as_nanos()
    );
    let temporary = format!("{marker}.tmp");
    std::fs::write(&temporary, &record).map_err(|e| Error::Pty(e.into()))?;
    std::fs::rename(temporary, &marker).map_err(|e| Error::Pty(e.into()))?;
    println!("HOST_SAMPLE_RECORD {}", record.trim());
    if outcome == "pass" {
        result
    } else {
        Err(Error::UnexpectedOutput {
            expected: "Command and barrier within the host deadline".into(),
            got: format!("{outcome}: {result:?}"),
        })
    }
}
