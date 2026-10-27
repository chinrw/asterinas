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
        let cmd = if testcase == "runtime" || testcase.starts_with("runtime ") {
            format!(
                "GOFLAGS=-overlay=/tmp/notingo-probe-src/overlay.json go test -short -v -count=1 -timeout=9m {testcase}"
            )
        } else {
            format!("go test -short {testcase}")
        };
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

// Run the original helper with native thread-creation failures made explicit.
#[nixos_test]
fn notingo_native_startup(nixos_shell: &mut Session) -> Result<(), Error> {
    const PROBE_RUNS: usize = 1000;
    const PROGRESS_INTERVAL: usize = 100;
    let built = Regex::new(r"(?m)^PROBE_BUILD_OK\s*$").map_err(|err| Error::Pty(err.into()))?;
    let ready = Regex::new(r"(?m)^OK\s*$").map_err(|err| Error::Pty(err.into()))?;
    nixos_shell.run_cmd_and_expect_regex("python3 /etc/notingo/build.py", &built)?;
    for name in ["NotInGoMetricCgoCall", "NotInGoMetricCgoCallback"] {
        let command = format!("timeout --signal=QUIT --kill-after=2s 5s /tmp/notingo-probe {name}");
        nixos_shell.run_cmd_and_expect_regex(&command, &ready)?;
    }
    for iteration in 1..=PROBE_RUNS {
        nixos_shell.run_cmd_and_expect_regex(
            "timeout --signal=QUIT --kill-after=2s 5s /tmp/notingo-probe NotInGoMetricCgoCallAndCallback",
            &ready,
        )?;
        if iteration % PROGRESS_INTERVAL == 0 {
            println!("Completed {iteration} mixed C/Go startup probes");
        }
    }
    Ok(())
}

// Keep the full preceding workload while tracing native thread-creation errors.
#[nixos_test]
fn notingo_instrumented_suite(nixos_shell: &mut Session) -> Result<(), Error> {
    let ready =
        Regex::new(r"(?m)^PROBE_OVERLAY_READY\s*$").map_err(|err| Error::Pty(err.into()))?;
    nixos_shell.run_cmd_and_expect_regex("python3 /etc/notingo/build.py --prepare-only", &ready)?;
    go_stdlib(nixos_shell)
}

// Compare the helper modes on the current guest kernel.
#[nixos_test]
fn notingo_same_vm_controls(nixos_shell: &mut Session) -> Result<(), Error> {
    nixos_shell.run_cmd("uname -a")?;
    if std::env::var("PROBE_LINUX_GUEST").as_deref() == Ok("1") {
        nixos_shell.run_cmd("stty -onlcr")?;
        let confirmed =
            Regex::new(r"(?m)^LINUX_KERNEL_CONFIRMED\s*$").map_err(|err| Error::Pty(err.into()))?;
        nixos_shell.run_cmd_and_expect_regex(
            "test \"$(uname -r)\" = \"$(cat /etc/notingo/linux-version)\" && echo LINUX_KERNEL_CONFIRMED",
            &confirmed,
        )?;
    }
    let built_original =
        Regex::new(r"(?m)^PROBE_ORIGINAL_BUILD_OK\s*$").map_err(|err| Error::Pty(err.into()))?;
    let ready = Regex::new(r"(?m)^OK\s*$").map_err(|err| Error::Pty(err.into()))?;
    let mut failures = Vec::new();
    nixos_shell
        .run_cmd_and_expect_regex("python3 /etc/notingo/build.py --original", &built_original)?;
    for (mode, binary, procs, debug) in [
        ("original-p1", "/tmp/notingo-original", 1, ""),
        (
            "original-p1-nosleep",
            "/tmp/notingo-original",
            1,
            "GODEBUG=schedtrace=1000000",
        ),
        ("original-p2", "/tmp/notingo-original", 2, ""),
        ("original-p1-repeat", "/tmp/notingo-original", 1, ""),
        ("shared-p1", "/etc/notingo/shared", 1, ""),
    ] {
        if mode == "shared-p1" {
            let clock_output =
                Regex::new(r"(?m)^CLOCK_PROBE ").map_err(|err| Error::Pty(err.into()))?;
            let started = std::time::Instant::now();
            nixos_shell.run_cmd_and_expect_regex(
                "timeout --kill-after=2s 15s /etc/notingo/clock-probe",
                &clock_output,
            )?;
            println!(
                "CLOCK_PROBE_HOST_SECONDS {:.3}",
                started.elapsed().as_secs_f64()
            );
            let expected =
                std::fs::read_to_string("../../../../target/notingo-native-controls/sha256.txt")
                    .map_err(|err| Error::Pty(err.into()))?;
            nixos_shell.run_cmd_and_expect("sha256sum /etc/notingo/shared", expected.trim())?;
        }
        for iteration in 1..=10 {
            let started = std::time::Instant::now();
            let command = format!(
                "GOMAXPROCS={procs} {debug} timeout --signal=QUIT --kill-after=2s 10s {binary} NotInGoMetricCgoCallAndCallback"
            );
            match nixos_shell.run_cmd_and_expect_regex(&command, &ready) {
                Ok(()) => println!(
                    "PROBE_RESULT {mode} {iteration} OK {:.3}s",
                    started.elapsed().as_secs_f64()
                ),
                Err(error @ Error::NonZeroExit { .. }) => {
                    println!(
                        "PROBE_RESULT {mode} {iteration} ERROR {:.3}s {error:?}",
                        started.elapsed().as_secs_f64()
                    );
                    failures.push((mode.to_string(), error));
                    break;
                }
                Err(error) => return Err(error),
            }
        }
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(Error::Aggregated {
            summary: "Native helper controls failed".to_string(),
            collections: failures,
        })
    }
}

// Read live scheduler state after a helper has already exceeded normal startup time.
#[nixos_test]
fn notingo_live_controls(shell: &mut Session) -> Result<(), Error> {
    let built =
        Regex::new(r"(?m)^PROBE_ORIGINAL_BUILD_OK\s*$").map_err(|e| Error::Pty(e.into()))?;
    let ok = Regex::new(r"(?m)^OK\s*$").map_err(|e| Error::Pty(e.into()))?;
    shell.run_cmd_and_expect_regex("python3 /etc/notingo/build.py --original", &built)?;
    let mut failures = Vec::new();
    for (mode, binary, debug) in [
        ("original", "/tmp/notingo-original", ""),
        ("shared", "/etc/notingo/shared", ""),
        (
            "noasync",
            "/etc/notingo/shared",
            "GODEBUG=asyncpreemptoff=1",
        ),
        (
            "noavx",
            "/etc/notingo/shared",
            "GODEBUG=cpu.avx=off,cpu.avx2=off",
        ),
    ] {
        for iteration in 1..=5 {
            println!("LIVE_GO_BEGIN {mode} {iteration}");
            let start = std::time::Instant::now();
            let result = shell.run_cmd_and_expect_regex(&format!("GOMAXPROCS=1 {debug} timeout --signal=QUIT --kill-after=2s 10s {binary} NotInGoMetricCgoCallAndCallback"), &ok);
            println!(
                "LIVE_GO_END {mode} {iteration} {:.3}s {result:?}",
                start.elapsed().as_secs_f64()
            );
            match result {
                Ok(()) => {}
                Err(error @ Error::NonZeroExit { .. }) => {
                    failures.push((mode.to_string(), error));
                    break;
                }
                Err(error) => return Err(error),
            }
        }
    }
    let built_c = Regex::new(r"(?m)^C_READY\s*$").map_err(|e| Error::Pty(e.into()))?;
    shell.run_cmd_and_expect_regex("gcc -O2 -pthread /etc/notingo/wait.c -o /tmp/wait-probe && gcc -O2 -pthread /etc/notingo/signal.c -o /tmp/signal-probe && echo C_READY", &built_c)?;
    for (mode, command, expected) in [
        ("sleep-idle", "/tmp/wait-probe 0 1000", "WAIT_PROBE_PASS"),
        ("sleep-busy", "/tmp/wait-probe 20 100", "WAIT_PROBE_PASS"),
        ("signal", "/tmp/signal-probe 100000 1 1", "PING_DONE"),
    ] {
        println!("LIVE_C_BEGIN {mode} 1");
        let result = shell.run_cmd_and_expect(command, expected);
        println!("LIVE_C_END {mode} 1 {result:?}");
        result?;
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(Error::Aggregated {
            summary: "Live progress controls failed".into(),
            collections: failures,
        })
    }
}

#[nixos_test]
fn notingo_short_sleep_controls(shell: &mut Session) -> Result<(), Error> {
    let ok = Regex::new(r"(?m)^OK\s*$").map_err(|e| Error::Pty(e.into()))?;
    let barrier = Regex::new(r"(?m)^BARRIER_OK\s*$").map_err(|e| Error::Pty(e.into()))?;
    let mut failures = Vec::new();
    for iteration in 1..=5 {
        println!("LIVE_GO_BEGIN shared {iteration}");
        let start = std::time::Instant::now();
        let result = shell.run_cmd_and_expect_regex("GOMAXPROCS=1 timeout --signal=QUIT --kill-after=2s 10s /etc/notingo/shared NotInGoMetricCgoCallAndCallback", &ok);
        println!(
            "LIVE_GO_END shared {iteration} {:.3}s {result:?}",
            start.elapsed().as_secs_f64()
        );
        match result {
            Ok(()) => {}
            Err(error @ Error::NonZeroExit { .. }) => {
                failures.push(("original".to_string(), error));
                break;
            }
            Err(error) => return Err(error),
        }
    }
    let ready = Regex::new(r"(?m)^ALLOC_READY\s*$").map_err(|e| Error::Pty(e.into()))?;
    let allocated = Regex::new(r"(?m)^ALLOC_PASS ").map_err(|e| Error::Pty(e.into()))?;
    shell.run_cmd_and_expect_regex(
        "gcc -O2 -pthread /etc/notingo/allocation.c -o /tmp/allocation-probe && echo ALLOC_READY",
        &ready,
    )?;
    for (mode, sleepers, spinners, ns) in [
        ("idle", 0, 0, 1000),
        ("spin", 0, 2, 1000),
        ("sleep20us", 9, 2, 20000),
        ("sleep1us", 9, 2, 1000),
    ] {
        println!("LIVE_C_BEGIN {mode} 1");
        let start = std::time::Instant::now();
        let result = shell.run_cmd_and_expect_regex(
            &format!(
                "timeout --signal=KILL 30s /tmp/allocation-probe {sleepers} {spinners} {ns} 1000"
            ),
            &allocated,
        );
        println!(
            "LIVE_C_END {mode} 1 {:.3}s {result:?}",
            start.elapsed().as_secs_f64()
        );
        match result {
            Ok(()) => {}
            Err(error @ Error::NonZeroExit { .. }) => failures.push((mode.to_string(), error)),
            Err(error) => return Err(error),
        }
    }
    for mode in ["poll", "yield"] {
        for iteration in 1..=5 {
            println!("SHORT_GO_BEGIN {mode} {iteration}");
            let start = std::time::Instant::now();
            let result = shell.run_cmd_and_expect_regex(
                &format!("GOMAXPROCS=1 timeout --signal=KILL 10s /etc/notingo/minimal {mode}"),
                &barrier,
            );
            println!(
                "SHORT_GO_END {mode} {iteration} {:.3}s {result:?}",
                start.elapsed().as_secs_f64()
            );
            match result {
                Ok(()) => {}
                Err(error @ Error::NonZeroExit { .. }) => {
                    failures.push((mode.to_string(), error));
                    break;
                }
                Err(error) => return Err(error),
            }
        }
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(Error::Aggregated {
            summary: "Short-sleep and startup controls failed".into(),
            collections: failures,
        })
    }
}

#[nixos_test]
fn notingo_minimal_live_controls(shell: &mut Session) -> Result<(), Error> {
    let digest = std::env::var("PROBE_MINIMAL_SHA256").expect("Host minimal helper checksum");
    let checksum = Regex::new(&format!(r"(?m)^{}  /etc/notingo/minimal\s*$", digest))
        .map_err(|e| Error::Pty(e.into()))?;
    shell.run_cmd_and_expect_regex("sha256sum /etc/notingo/minimal", &checksum)?;
    let state = Regex::new(r"(?m)^PROCESS_STATE_END\s*$").map_err(|e| Error::Pty(e.into()))?;
    let barrier = Regex::new(r"(?m)^BARRIER_OK\s*$").map_err(|e| Error::Pty(e.into()))?;
    let mut failures = Vec::new();
    for mode in ["poll", "yield"] {
        for iteration in 1..=8 {
            if iteration == 1 {
                shell.run_cmd_and_expect_regex("echo PROCESS_STATE_BEGIN && ps -eLo pid,tid,ppid,stat,comm && cat /proc/meminfo && echo PROCESS_STATE_END",&state)?;
            }
            println!("LIVE_GO_BEGIN {mode} {iteration}");
            let start = std::time::Instant::now();
            let result=shell.run_cmd_and_expect_regex(&format!("GOMAXPROCS=1 timeout --signal=QUIT --kill-after=2s 10s /etc/notingo/minimal {mode}"),&barrier);
            println!(
                "LIVE_GO_END {mode} {iteration} {:.3}s {result:?}",
                start.elapsed().as_secs_f64()
            );
            match result {
                Ok(()) => {}
                Err(error @ Error::NonZeroExit { .. }) => {
                    failures.push((mode.to_string(), error));
                    break;
                }
                Err(error) => return Err(error),
            }
        }
    }
    shell.run_cmd_and_expect_regex("echo PROCESS_STATE_BEGIN && ps -eLo pid,tid,ppid,stat,comm && cat /proc/meminfo && echo PROCESS_STATE_END",&state)?;
    let ready = Regex::new(r"(?m)^ALLOC_READY\s*$").map_err(|e| Error::Pty(e.into()))?;
    let allocated = Regex::new(r"(?m)^ALLOC_PASS ").map_err(|e| Error::Pty(e.into()))?;
    shell.run_cmd_and_expect_regex(
        "gcc -O2 -pthread /etc/notingo/allocation.c -o /tmp/allocation-probe && echo ALLOC_READY",
        &ready,
    )?;
    for (mode, ns) in [("ten-spin-20us", 20000), ("ten-spin-1us", 1000)] {
        println!("LIVE_C_BEGIN {mode} 1");
        let start = std::time::Instant::now();
        let result = shell.run_cmd_and_expect_regex(
            &format!("timeout --signal=KILL 30s /tmp/allocation-probe 9 10 {ns} 1000"),
            &allocated,
        );
        println!(
            "LIVE_C_END {mode} 1 {:.3}s {result:?}",
            start.elapsed().as_secs_f64()
        );
        match result {
            Ok(()) => {}
            Err(error @ Error::NonZeroExit { .. }) => failures.push((mode.to_string(), error)),
            Err(error) => return Err(error),
        }
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(Error::Aggregated {
            summary: "Standalone cgo progress failed".into(),
            collections: failures,
        })
    }
}

#[nixos_test]
fn notingo_wait_interval_controls(shell: &mut Session) -> Result<(), Error> {
    let digest = std::env::var("PROBE_MINIMAL_SHA256").expect("Original minimal checksum");
    let mut expected = vec![("minimal".to_string(), digest)];
    for line in std::env::var("PROBE_FOREIGN_WAIT_DIGESTS")
        .expect("Variant checksums")
        .lines()
    {
        let mut fields = line.split_whitespace();
        let name = fields.next().expect("Variant name").to_string();
        let digest = fields.next().expect("Variant checksum").to_string();
        expected.push((name, digest));
    }
    for (name, digest) in expected {
        let checksum = Regex::new(&format!(r"(?m)^{}  /etc/notingo/{}\s*$", digest, name))
            .map_err(|e| Error::Pty(e.into()))?;
        shell.run_cmd_and_expect_regex(&format!("sha256sum /etc/notingo/{name}"), &checksum)?;
    }
    let state = Regex::new(r"(?m)^PROCESS_STATE_END\s*$").map_err(|e| Error::Pty(e.into()))?;
    let barrier = Regex::new(r"(?m)^BARRIER_OK\s*$").map_err(|e| Error::Pty(e.into()))?;
    let mut failures = Vec::new();
    for (mode, binary, alternate) in [
        ("base-before", "minimal", false),
        ("sleep20", "sleep20", true),
        ("base-middle", "minimal", false),
        ("sleep1000", "sleep1000", true),
        ("base-after", "minimal", false),
    ] {
        shell.run_cmd_and_expect_regex("echo PROCESS_STATE_BEGIN && ps -eLo pid,tid,ppid,stat,comm && cat /proc/meminfo && echo PROCESS_STATE_END",&state)?;
        let prefix = if alternate { "ALT_GO" } else { "LIVE_GO" };
        for iteration in 1..=if alternate { 10 } else { 8 } {
            println!("{prefix}_BEGIN {mode} {iteration}");
            let start = std::time::Instant::now();
            let result=shell.run_cmd_and_expect_regex(&format!("GOMAXPROCS=1 timeout --signal=QUIT --kill-after=2s 10s /etc/notingo/{binary} poll"),&barrier);
            println!(
                "{prefix}_END {mode} {iteration} {:.3}s {result:?}",
                start.elapsed().as_secs_f64()
            );
            match result {
                Ok(()) => {}
                Err(error @ Error::NonZeroExit { .. }) => {
                    failures.push((mode.to_string(), error));
                    break;
                }
                Err(error) => return Err(error),
            }
        }
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(Error::Aggregated {
            summary: "Foreign-thread wait interval controls failed".into(),
            collections: failures,
        })
    }
}

#[nixos_test]
fn notingo_kernel_timeline(shell: &mut Session) -> Result<(), Error> {
    let mut digests = vec![(
        "minimal".to_string(),
        std::env::var("PROBE_MINIMAL_SHA256").expect("Minimal checksum"),
    )];
    for line in std::env::var("PROBE_TIMELINE_DIGESTS")
        .expect("Tagged checksums")
        .lines()
    {
        let mut fields = line.split_whitespace();
        digests.push((
            fields.next().unwrap().to_string(),
            fields.next().unwrap().to_string(),
        ));
    }
    for (name, digest) in digests {
        let checksum = Regex::new(&format!(r"(?m)^{}  /etc/notingo/{}\s*$", digest, name))
            .map_err(|e| Error::Pty(e.into()))?;
        shell.run_cmd_and_expect_regex(&format!("sha256sum /etc/notingo/{name}"), &checksum)?;
    }
    let barrier = Regex::new(r"(?m)^BARRIER_OK\s*$").map_err(|e| Error::Pty(e.into()))?;
    let uploaded = Regex::new(r"(?m)^TIMELINE_UPLOAD_OK ").map_err(|e| Error::Pty(e.into()))?;
    let mut failures = Vec::new();
    for iteration in 1..=3 {
        println!("TIMELINE_BASELINE_BEGIN {iteration}");
        let result = shell.run_cmd_and_expect_regex(
            "GOMAXPROCS=1 timeout --signal=QUIT --kill-after=2s 10s /etc/notingo/minimal poll",
            &barrier,
        );
        println!("TIMELINE_BASELINE_END {iteration} {result:?}");
        match result {
            Ok(()) => {}
            Err(e @ Error::NonZeroExit { .. }) => {
                failures.push(("baseline".into(), e));
                break;
            }
            Err(e) => return Err(e),
        }
    }
    for (name, mode, binary, repeats) in [
        ("tag-off", 0, "tagged", 2),
        ("counts", 1, "tagged", 2),
        ("timeline", 2, "tagged", 2),
        ("control20", 2, "tagged20", 3),
    ] {
        for iteration in 1..=repeats {
            let trial = format!("{name}-{iteration}");
            println!("TIMELINE_TRIAL_BEGIN {trial}");
            let start = std::time::Instant::now();
            let command = format!(
                "( /etc/notingo/timeline-launcher {mode} /tmp/{trial}.bin /etc/notingo/{binary} poll; trial_result=$?; /etc/notingo/timeline-upload /tmp/{trial}.bin {trial} || exit $?; exit $trial_result )"
            );
            let result = shell.run_cmd_and_expect_regex(&command, &uploaded);
            println!(
                "TIMELINE_TRIAL_END {trial} {:.3}s {result:?}",
                start.elapsed().as_secs_f64()
            );
            match result {
                Ok(()) => {}
                Err(e @ Error::NonZeroExit { .. }) => {
                    failures.push((trial, e));
                    break;
                }
                Err(e) => return Err(e),
            }
        }
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(Error::Aggregated {
            summary: "Kernel timeline trials reproduced startup failure".into(),
            collections: failures,
        })
    }
}

#[nixos_test]
fn notingo_uninstrumented_timeline_baseline(shell: &mut Session) -> Result<(), Error> {
    let barrier = Regex::new(r"(?m)^BARRIER_OK\s*$").map_err(|e| Error::Pty(e.into()))?;
    for iteration in 1..=8 {
        let start = std::time::Instant::now();
        let result = shell.run_cmd_and_expect_regex(
            "GOMAXPROCS=1 timeout --signal=QUIT --kill-after=2s 10s /etc/notingo/minimal poll",
            &barrier,
        );
        println!(
            "UNINSTRUMENTED_BASELINE {iteration} {:.3}s {result:?}",
            start.elapsed().as_secs_f64()
        );
        result?;
    }
    Ok(())
}

#[nixos_test]
fn notingo_timer_budget_comparison(shell: &mut Session) -> Result<(), Error> {
    let mut digests = vec![
        (
            "minimal".to_string(),
            std::env::var("PROBE_MINIMAL_SHA256").expect("Minimal checksum"),
        ),
        (
            "shared".to_string(),
            std::env::var("PROBE_SHARED_SHA256").expect("Shared checksum"),
        ),
    ];
    for line in std::env::var("PROBE_TIMELINE_DIGESTS")
        .expect("Tagged checksums")
        .lines()
    {
        let fields: Vec<_> = line.split_whitespace().collect();
        digests.push((fields[0].to_string(), fields[1].to_string()));
    }
    for (name, digest) in digests {
        shell.run_cmd_and_expect(&format!("sha256sum /etc/notingo/{name}"), &digest)?;
    }
    let success =
        Regex::new(r"(?s)BARRIER_OK.*TIMELINE_UPLOAD_OK ").map_err(|e| Error::Pty(e.into()))?;
    let baseline_required = std::env::var("PROBE_BUDGET_BASELINE_REQUIRED").as_deref() != Ok("0");
    for (name, budget, mode, binary, repeats, expect_failure) in [
        ("unbounded-before", 0, 0, "minimal", 3, true),
        ("bounded-first", 64, 0, "minimal", 20, false),
        ("bounded-trace", 64, 2, "tagged", 3, false),
        ("unbounded-trace", 0, 2, "tagged", 3, true),
        ("bounded-after", 64, 0, "minimal", 20, false),
    ] {
        if budget == 64 {
            shell.run_cmd_and_expect(
                "/etc/notingo/timeline-launcher budget 64 && sleep 1 && echo BUDGET_SETTLED",
                "BUDGET_SETTLED",
            )?;
        }
        let mut timed_out = false;
        for iteration in 1..=repeats {
            let trial = format!("{name}-{iteration}");
            let command = format!(
                "( PROBE_TIMER_BUDGET={budget} /etc/notingo/timeline-launcher {mode} /tmp/{trial}.bin /etc/notingo/{binary} poll; trial_result=$?; /etc/notingo/timeline-upload /tmp/{trial}.bin {trial} || exit $?; exit $trial_result )"
            );
            let start = std::time::Instant::now();
            let result = shell.run_cmd_and_expect_regex(&command, &success);
            println!(
                "BUDGET_TRIAL {trial} budget={budget} {:.3}s {result:?}",
                start.elapsed().as_secs_f64()
            );
            match result {
                Ok(()) => {}
                Err(Error::NonZeroExit {
                    exit_status: 124,
                    output,
                }) if expect_failure
                    && !output.contains("BARRIER_OK")
                    && output.contains("timeout_phase=") =>
                {
                    timed_out = true;
                    break;
                }
                Err(error) => return Err(error),
            }
        }
        if expect_failure && baseline_required && !timed_out {
            return Err(Error::UnexpectedOutput {
                expected: format!("{name} to reproduce startup timeout"),
                got: "all baseline calls completed".into(),
            });
        }
    }
    let ok = Regex::new(r"(?m)^OK\s*$").map_err(|e| Error::Pty(e.into()))?;
    for iteration in 1..=10 {
        let start = std::time::Instant::now();
        shell.run_cmd_and_expect_regex("GOMAXPROCS=1 timeout --signal=QUIT --kill-after=2s 10s /etc/notingo/shared NotInGoMetricCgoCallAndCallback",&ok)?;
        println!(
            "BUDGET_FULL_HELPER_PASS {iteration} {:.3}s",
            start.elapsed().as_secs_f64()
        );
    }
    Ok(())
}

#[nixos_test]
fn notingo_timer_budget_capacity(shell: &mut Session) -> Result<(), Error> {
    for (name, variable) in [
        ("minimal", "PROBE_MINIMAL_SHA256"),
        ("shared", "PROBE_SHARED_SHA256"),
    ] {
        shell.run_cmd_and_expect(
            &format!("sha256sum /etc/notingo/{name}"),
            &std::env::var(variable).expect("Helper checksum"),
        )?;
    }
    let success =
        Regex::new(r"(?s)BARRIER_OK.*TIMELINE_UPLOAD_OK ").map_err(|e| Error::Pty(e.into()))?;
    let mut failures = Vec::new();
    let mut control_failed = false;
    for (name, budget, binary, mode, repeats, settle) in [
        ("bounded-cold-original", 64, "minimal", 2, 3, false),
        ("bounded-cold-off", 64, "minimal", 0, 20, false),
        ("bounded-cold-tagged", 64, "tagged", 2, 3, false),
        ("unbounded-observe", 0, "tagged", 2, 3, false),
        ("bounded-settled-original", 64, "minimal", 2, 3, true),
        ("bounded-settled-off", 64, "minimal", 0, 20, false),
        ("bounded-settled-tagged", 64, "tagged", 2, 3, false),
    ] {
        if settle {
            shell.run_cmd_and_expect(
                "/etc/notingo/timeline-launcher budget 64 && echo MONOTONIC_QUEUE_SETTLED",
                "MONOTONIC_QUEUE_SETTLED",
            )?;
        }
        for iteration in 1..=repeats {
            let trial = format!("{name}-{iteration}");
            let command = format!(
                "( PROBE_TIMER_BUDGET={budget} /etc/notingo/timeline-launcher {mode} /tmp/{trial}.bin /etc/notingo/{binary} poll; trial_result=$?; /etc/notingo/timeline-upload /tmp/{trial}.bin {trial} || exit $?; exit $trial_result )"
            );
            let start = std::time::Instant::now();
            let result = shell.run_cmd_and_expect_regex(&command, &success);
            println!(
                "BUDGET_CAPACITY {trial} budget={budget} {:.3}s {result:?}",
                start.elapsed().as_secs_f64()
            );
            match result {
                Ok(()) => {}
                Err(
                    e @ Error::NonZeroExit {
                        exit_status: 124, ..
                    },
                ) => {
                    if budget == 0 {
                        control_failed = true;
                    } else {
                        failures.push((trial, e));
                    }
                    break;
                }
                Err(e) => return Err(e),
            }
        }
    }
    let baseline_required = std::env::var("PROBE_BUDGET_BASELINE_REQUIRED").as_deref() != Ok("0");
    if baseline_required && !control_failed {
        return Err(Error::UnexpectedOutput {
            expected: "Unbounded control to time out".into(),
            got: "All unbounded controls passed".into(),
        });
    }
    if failures.is_empty() {
        let ok = Regex::new(r"(?m)^OK\s*$").map_err(|e| Error::Pty(e.into()))?;
        for iteration in 1..=10 {
            shell.run_cmd_and_expect_regex("GOMAXPROCS=1 timeout --signal=QUIT --kill-after=2s 10s /etc/notingo/shared NotInGoMetricCgoCallAndCallback", &ok)?;
            println!("BUDGET_FULL_HELPER_PASS {iteration}");
        }
        Ok(())
    } else {
        Err(Error::Aggregated {
            summary: "Budget capacity probe retained timeouts".into(),
            collections: failures,
        })
    }
}
