// SPDX-License-Identifier: MPL-2.0
use std::{env, fs, io::Write, path::PathBuf, process::Command, time::Instant};

use nixos_test_framework::*;

nixos_test_main!();

fn qemu_ticks() -> u64 {
    let owner = fs::read_to_string(env::var("PROBE_QEMU_OWNER").unwrap()).unwrap();
    let pid = owner.split_whitespace().next().unwrap();
    let stat = fs::read_to_string(format!("/proc/{pid}/stat")).unwrap();
    let fields: Vec<_> = stat.rsplit_once(") ").unwrap().1.split_whitespace().collect();
    fields[11].parse::<u64>().unwrap() + fields[12].parse::<u64>().unwrap()
}

fn phase(shell: &mut Session, name: &str, command: &str, expected: &str) -> Result<(), Error> {
    let dir = PathBuf::from(env::var("PERF_SAMPLE_DIR").unwrap());
    let started = Instant::now();
    let before = qemu_ticks();
    let regex = Regex::new(expected).unwrap();
    let result = shell.run_cmd_and_expect_regex(
        &format!("echo PERF_BEGIN_{name}; {command} && echo PERF_END_{name}"), &regex);
    let elapsed = started.elapsed().as_nanos();
    let after = qemu_ticks();
    let mut file = fs::OpenOptions::new().create(true).append(true).open(dir.join("phases.jsonl")).unwrap();
    writeln!(file,"{{\"name\":\"{name}\",\"host_elapsed_ns\":{elapsed},\"qemu_cpu_ticks\":{},\"passed\":{}}}",after-before,result.is_ok()).unwrap();
    result
}

fn client(name: &str, mode: &str) -> Result<(), Error> {
    let suite = env::current_dir().unwrap();
    let started = Instant::now();
    let before = qemu_ticks();
    let result = Command::new("python3").arg(suite.join("host_clients.py"))
        .args([name, mode]).status().map_err(|e| Error::Pty(e.into()))?;
    let dir = PathBuf::from(env::var("PERF_SAMPLE_DIR").unwrap());
    let mut file = fs::OpenOptions::new().create(true).append(true).open(dir.join("phases.jsonl")).unwrap();
    writeln!(file,"{{\"name\":\"client_{name}_{mode}\",\"host_elapsed_ns\":{},\"qemu_cpu_ticks\":{},\"passed\":{}}}",started.elapsed().as_nanos(),qemu_ticks()-before,result.success()).unwrap();
    if !result.success() {
        return Err(Error::UnexpectedOutput { expected: "validated host workload".into(), got: format!("{name}/{mode}: {result}") });
    }
    Ok(())
}

#[nixos_test]
fn lifecycle_performance(shell: &mut Session) -> Result<(), Error> {
    let pilot = env::var("PERF_PILOT").unwrap_or_default() == "1";
    let dir = PathBuf::from(env::var("PERF_SAMPLE_DIR").unwrap());
    let cpu = env::var("SMP").unwrap();
    let digest = env::var("PERF_HELPER_SHA256").unwrap();
    phase(shell,"identity",&format!("ulimit -n 8192 && test $(nproc) -eq {cpu} && sha256sum /etc/timer-perf/timer-perf && cat /proc/mounts"),&format!(r"(?m)^{digest}\s+"))?;
    for n in if pilot { vec![64] } else { vec![1, 64, 1024, 4096] } {
        for mode in ["cancel","drop","rearm","expiry"] {
            phase(shell,&format!("micro_{n}_{mode}"),&format!("/etc/timer-perf/timer-perf micro {n} {mode}"),r"(?m)^PERF_MICRO \{")?;
        }
    }
    phase(shell,"services_start","python3 /etc/timer-perf/setup.py start",r"(?m)^PERF_SERVICES_STARTED\s*$")?;
    client("ready","none")?;
    let modes = if env::var("PERF_BLOCK").unwrap().parse::<usize>().unwrap() % 2 == 0 {
        ["none","churn","expiry"]
    } else { ["expiry","churn","none"] };
    for mode in modes {
        for workload in ["probe","wake","nginx","redis","sqlite"] {
            let key = format!("{mode}_{workload}");
            if mode != "none" {
                let command = format!("rm -f /var/tmp/timer-load-{key}.log /var/tmp/timer-load-{key}.pid; /etc/timer-perf/timer-perf load {mode} 5000 120 >/var/tmp/timer-load-{key}.log 2>&1 & echo $! >/var/tmp/timer-load-{key}.pid; for i in $(seq 1 100); do grep -q '^PERF_LOAD_STARTED ' /var/tmp/timer-load-{key}.log && break; sleep 0.05; done; kill -0 $(cat /var/tmp/timer-load-{key}.pid) && cat /var/tmp/timer-load-{key}.log");
                phase(shell,&format!("load_start_{mode}_{workload}"),&command,r"(?m)^PERF_LOAD_STARTED \{")?;
            }
            phase(shell,&format!("begin_{key}"),&format!("/etc/timer-perf/timer-perf stamp {key}_begin >/var/tmp/{key}.begin && cat /var/tmp/{key}.begin"),r"(?m)^PERF_STAMP \{")?;
            match workload {
                "probe" => phase(shell,&format!("probe_{mode}"),&format!("/etc/timer-perf/timer-perf probe {}",if pilot {1}else{5}),r"(?m)^PERF_PROBE \{")?,
                "wake" => phase(shell,&format!("wake_{mode}"),&format!("/etc/timer-perf/timer-perf wake {}",if pilot {1}else{5}),r"(?m)^PERF_WAKE \{")?,
                "sqlite" => phase(shell,&format!("sqlite_{mode}"),&format!("rm -f /var/tmp/timer-perf.db* && /etc/timer-perf/sqlite-speedtest1 --size {} /var/tmp/timer-perf.db",if pilot {5}else{100}),r"(?m)^\s*TOTAL.*")?,
                _ => client(workload,mode)?,
            }
            phase(shell,&format!("end_{key}"),&format!("/etc/timer-perf/timer-perf stamp {key}_end >/var/tmp/{key}.end && cat /var/tmp/{key}.end"),r"(?m)^PERF_STAMP \{")?;
            if mode != "none" {
                phase(shell,&format!("load_stop_{mode}_{workload}"),&format!("kill -TERM $(cat /var/tmp/timer-load-{key}.pid) && wait $(cat /var/tmp/timer-load-{key}.pid) && cat /var/tmp/timer-load-{key}.log && python3 /etc/timer-perf/loadcheck.py {key}"),r"(?m)^PERF_LOAD_VALIDATED \{")?;
            }
        }
    }
    phase(shell,"services_stop","python3 /etc/timer-perf/setup.py stop",r"(?m)^PERF_SERVICES_STOPPED\s*$")?;
    fs::write(dir.join("complete.tmp"),"complete\n").unwrap();
    fs::rename(dir.join("complete.tmp"),dir.join("complete")).unwrap();
    notingo_host_sample(shell)
}

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
