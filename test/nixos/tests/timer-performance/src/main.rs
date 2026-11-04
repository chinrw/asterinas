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
    if let Err(error) = &result { eprintln!("CONTROL_PHASE_ERROR {name}: {error:?}"); }
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
    let result = Command::new("python3").arg(if name == "history_redis" { PathBuf::from(env::var("HISTORY_CLIENT_SCRIPT").unwrap()) } else { suite.join("host_clients.py") })
        .args([name, mode]).status().map_err(|e| Error::Pty(e.into()))?;
    let dir = PathBuf::from(env::var("PERF_SAMPLE_DIR").unwrap());
    let mut file = fs::OpenOptions::new().create(true).append(true).open(dir.join("phases.jsonl")).unwrap();
    writeln!(file,"{{\"name\":\"client_{name}_{mode}\",\"host_elapsed_ns\":{},\"qemu_cpu_ticks\":{},\"passed\":{}}}",started.elapsed().as_nanos(),qemu_ticks()-before,result.success()).unwrap();
    if !result.success() {
        return Err(Error::UnexpectedOutput { expected: "validated host workload".into(), got: format!("{name}/{mode}: {result}") });
    }
    Ok(())
}

fn wait_for_guest_profile() -> Result<(), Error> {
    if env::var("TAIL_PERF").unwrap_or_default() != "1" {
        return Ok(());
    }
    let directory = PathBuf::from(env::var("PERF_SAMPLE_DIR").unwrap());
    let started = Instant::now();
    while started.elapsed() < std::time::Duration::from_secs(35) {
        if directory.join("guest-perf-failed").exists() {
            break;
        }
        if directory.join("guest-perf-ready").exists() {
            return Ok(());
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    Err(Error::UnexpectedOutput {
        expected: "owned QMP vCPU profiler ready before micro workloads".into(),
        got: "profiler readiness failed or exceeded 35 seconds".into(),
    })
}

fn start_batch_probe(shell: &mut Session) -> Result<(), Error> {
    if env::var("TAIL_BATCH_BUILD").unwrap_or_default() != "1" {
        return Ok(());
    }
    let path = env::var("TAIL_BATCH_PROBE_PATH")
        .unwrap_or_else(|_| "/etc/timer-perf/timer-batch-probe".into());
    assert!(matches!(path.as_str(), "/etc/timer-perf/timer-batch-probe" | "/opt/timer-batch-probe"));
    let threshold = env::var("TAIL_BATCH_THRESHOLD_NS")
        .unwrap_or_else(|_| "0".into()).parse::<u64>().unwrap();
    let hash = env::var("PERF_BATCH_HELPER_SHA256").unwrap();
    let command = format!("test $(sha256sum {path} | cut -d ' ' -f 1) = {hash} && {{ {path} --threshold-ns {threshold} >/var/tmp/timer-batch-daemon.log 2>&1 & echo $! >/var/tmp/timer-batch-daemon.pid; for i in $(seq 1 100); do grep -q '^TIMER_BATCH_READY ' /var/tmp/timer-batch-daemon.log && break; sleep 0.05; done; kill -0 $(cat /var/tmp/timer-batch-daemon.pid) && cat /var/tmp/timer-batch-daemon.log; }}");
    phase(shell, "batch_server_start", &command, r"(?m)^TIMER_BATCH_READY ")
}

fn export_batch_probe(shell: &mut Session) -> Result<(), Error> {
    if env::var("TAIL_BATCH_BUILD").unwrap_or_default() != "1" {
        return Ok(());
    }
    let command = r#"for f in /var/tmp/timer-batch-*.bin; do [ -f "$f" ] || continue; test $(stat -c%s "$f") -eq 51584 || exit 1; name=${f##*/}; id=${name#timer-batch-}; id=${id%.bin}; i=0; base64 -w1024 "$f" | while IFS= read -r line; do i=$((i+1)); printf 'TAIL_BATCH_CHUNK %s %s 68 %s\n' "$id" "$i" "$line"; sleep 0.02; done; done; echo TAIL_BATCH_EXPORT_DONE"#;
    phase(shell, "batch_export", command, r"(?m)^TAIL_BATCH_EXPORT_DONE\s*$")
}

#[nixos_test]
fn lifecycle_performance(shell: &mut Session) -> Result<(), Error> {
    let pilot = env::var("PERF_PILOT").unwrap_or_default() == "1";
    let dir = PathBuf::from(env::var("PERF_SAMPLE_DIR").unwrap());
    let cpu = env::var("SMP").unwrap();
    let digest = env::var("PERF_HELPER_SHA256").unwrap();
    phase(shell,"identity",&format!("ulimit -n 8192 && test $(nproc) -eq {cpu} && sha256sum /etc/timer-perf/timer-perf && cat /proc/mounts"),&format!(r"(?m)^{digest}\s+"))?;
    wait_for_guest_profile()?;
    start_batch_probe(shell)?;
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
    export_batch_probe(shell)?;
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

fn wait_for_host_affinity() -> Result<(), Error> {
    if env::var("PINNING_CONTROL").unwrap_or_default() != "1" {
        return Ok(());
    }
    let directory = PathBuf::from(env::var("PERF_SAMPLE_DIR").unwrap());
    let started = Instant::now();
    while started.elapsed() < std::time::Duration::from_secs(35) {
        if directory.join("affinity-failed").exists() {
            break;
        }
        if directory.join("affinity-ready").exists() {
            return Ok(());
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    Err(Error::UnexpectedOutput {
        expected: "owned vCPU affinity setup before workload identity".into(),
        got: "affinity setup failed or exceeded 35 seconds".into(),
    })
}

#[nixos_test]
fn http_history(shell: &mut Session) -> Result<(), Error> {
    wait_for_host_affinity()?;
    if env::var("HISTORY_OS").unwrap() == "linux" {
        // Avoid applying ONLCR in both the guest console and the host PTY.
        shell.run_cmd("stty -onlcr")?;
    }
    let dir = PathBuf::from(env::var("PERF_SAMPLE_DIR").unwrap());
    let cpu = env::var("SMP").unwrap();
    let digest = env::var("PERF_HELPER_SHA256").unwrap();
    phase(shell,"identity",&format!("echo CONTROL_KERNEL $(uname -srm) && ulimit -n 8192 && test $(nproc) -eq {cpu} && sha256sum /etc/timer-perf/timer-perf"),&format!(r"(?m)^{digest}\s+"))?;
    phase(shell,"control_logging","systemctl log-level crit && test $(systemctl log-level) = crit && echo CONTROL_LOGGING_CRIT",r"(?m)^CONTROL_LOGGING_CRIT\s*$")?;
    let linux = env::var("HISTORY_OS").unwrap() == "linux";
    if linux {
        phase(shell,"linux_control_setup",&env::var("HISTORY_LINUX_SETUP").unwrap(),r"(?m)^LINUX_CONTROL_NETWORK \{")?;
    }
    start_batch_probe(shell)?;
    phase(shell,"services_start","python3 /etc/timer-perf/setup.py start",r"(?m)^PERF_SERVICES_STARTED\s*$")?;
    client("ready","none")?;
    let churn = env::var("TAIL_HISTORY_CHURN").unwrap_or_default() == "1";
    if churn {
        phase(shell,"load_start_churn_history","/etc/timer-perf/timer-perf load churn 5000 120 >/var/tmp/timer-load-churn_history.log 2>&1 & echo $! >/var/tmp/timer-load-churn_history.pid; for i in $(seq 1 100); do grep -q '^PERF_LOAD_STARTED ' /var/tmp/timer-load-churn_history.log && break; sleep 0.05; done; kill -0 $(cat /var/tmp/timer-load-churn_history.pid) && cat /var/tmp/timer-load-churn_history.log",r"(?m)^PERF_LOAD_STARTED \{")?;
        phase(shell,"begin_churn_history","/etc/timer-perf/timer-perf stamp churn_history_begin >/var/tmp/churn_history.begin && cat /var/tmp/churn_history.begin",r"(?m)^PERF_STAMP \{")?;
    }
    client("history_redis","none")?;
    if churn {
        phase(shell,"end_churn_history","/etc/timer-perf/timer-perf stamp churn_history_end >/var/tmp/churn_history.end && cat /var/tmp/churn_history.end",r"(?m)^PERF_STAMP \{")?;
        phase(shell,"load_stop_churn_history","kill -TERM $(cat /var/tmp/timer-load-churn_history.pid) && wait $(cat /var/tmp/timer-load-churn_history.pid) && cat /var/tmp/timer-load-churn_history.log && python3 /etc/timer-perf/loadcheck.py churn_history",r"(?m)^PERF_LOAD_VALIDATED \{")?;
    }
    phase(shell,"services_stop","python3 /etc/timer-perf/setup.py stop",r"(?m)^PERF_SERVICES_STOPPED\s*$")?;
    if linux {
        phase(shell,"linux_control_check",&env::var("HISTORY_LINUX_CHECK").unwrap(),r"(?m)^LINUX_CONTROL_NETWORK \{")?;
    }
    export_batch_probe(shell)?;
    fs::write(dir.join("complete.tmp"),"complete\n").unwrap();
    fs::rename(dir.join("complete.tmp"),dir.join("complete")).unwrap();
    notingo_host_sample(shell)
}


fn census_control(action: &str, case_id: u64, mode: u64) -> Result<(), Error> {
    let script = env::var("CENSUS_CONTROL_SCRIPT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| env::current_dir().unwrap().join("census/control.py"));
    let status = Command::new("python3")
        .arg(script)
        .args([action, &case_id.to_string(), &mode.to_string()])
        .status()
        .map_err(|error| Error::Pty(error.into()))?;
    if !status.success() {
        return Err(Error::UnexpectedOutput {
            expected: "validated census control reply".into(),
            got: format!("{action}/{case_id}/{mode}: {status}"),
        });
    }
    Ok(())
}

fn census_redis(case_id: u64) -> Result<(), Error> {
    let script = env::var("CENSUS_REDIS_SCRIPT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| env::current_dir().unwrap().join("census/redis_client.py"));
    let before = qemu_ticks();
    let started = Instant::now();
    let status = Command::new("python3")
        .arg(script)
        .arg(case_id.to_string())
        .status()
        .map_err(|error| Error::Pty(error.into()))?;
    let directory = PathBuf::from(env::var("PERF_SAMPLE_DIR").unwrap());
    let mut file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(directory.join("phases.jsonl"))
        .map_err(|error| Error::Pty(error.into()))?;
    writeln!(file, "{{\"name\":\"census_redis_{case_id}\",\"host_elapsed_ns\":{},\"qemu_cpu_ticks\":{},\"passed\":{}}}", started.elapsed().as_nanos(), qemu_ticks() - before, status.success())
        .map_err(|error| Error::Pty(error.into()))?;
    if !status.success() {
        return Err(Error::UnexpectedOutput {
            expected: "validated census Redis workload".into(),
            got: status.to_string(),
        });
    }
    Ok(())
}

#[nixos_test]
fn queue_census(shell: &mut Session) -> Result<(), Error> {
    let directory = PathBuf::from(env::var("PERF_SAMPLE_DIR").unwrap());
    let plain = env::var("CENSUS_PLAIN").unwrap_or_default() == "1";
    let cpu = env::var("SMP").unwrap();
    assert!(matches!(cpu.as_str(), "1" | "4"));
    let helper =
        env::var("CENSUS_HELPER_PATH").unwrap_or_else(|_| "/etc/timer-perf/timer-census".into());
    let workload = env::var("CENSUS_WORKLOAD_PATH")
        .unwrap_or_else(|_| "/etc/timer-perf/census-workload".into());
    let exporter = env::var("CENSUS_EXPORT_PATH")
        .unwrap_or_else(|_| "/etc/timer-perf/census-export.py".into());
    assert!(matches!(
        helper.as_str(),
        "/etc/timer-perf/timer-census" | "/opt/timer-census/timer-census"
    ));
    assert!(matches!(
        workload.as_str(),
        "/etc/timer-perf/census-workload" | "/opt/timer-census/census-workload"
    ));
    assert!(matches!(
        exporter.as_str(),
        "/etc/timer-perf/census-export.py" | "/opt/timer-census/export.py"
    ));
    let timer_hash = env::var("PERF_HELPER_SHA256").unwrap();
    let workload_hash = env::var("CENSUS_WORKLOAD_SHA256").unwrap();
    let go_hash = env::var("PROBE_HELPER_SHA256").unwrap();
    phase(
        shell,
        "census_identity",
        &format!(
            "echo CENSUS_KERNEL $(uname -srm) && ulimit -n 8192 && test $(nproc) -eq {cpu} && test $(sha256sum /etc/timer-perf/timer-perf | cut -d ' ' -f 1) = {timer_hash} && test $(sha256sum {workload} | cut -d ' ' -f 1) = {workload_hash} && test $(sha256sum /etc/notingo/minimal | cut -d ' ' -f 1) = {go_hash} && echo CENSUS_IDENTITY_OK"
        ),
        r"(?m)^CENSUS_IDENTITY_OK\s*$",
    )?;
    phase(
        shell,
        "census_logging",
        "systemctl log-level crit && test $(systemctl log-level) = crit && echo CENSUS_LOGGING_OK",
        r"(?m)^CENSUS_LOGGING_OK\s*$",
    )?;
    if !plain {
        let helper_hash = env::var("CENSUS_HELPER_SHA256").unwrap();
        phase(
            shell,
            "census_daemon",
            &format!(
                "test $(sha256sum {helper} | cut -d ' ' -f 1) = {helper_hash} && {{ {helper} >/var/tmp/timer-census-daemon.log 2>&1 & echo $! >/var/tmp/timer-census-daemon.pid; for i in $(seq 1 100); do grep -q '^TIMER_CENSUS_READY ' /var/tmp/timer-census-daemon.log && break; sleep 0.05; done; kill -0 $(cat /var/tmp/timer-census-daemon.pid) && cat /var/tmp/timer-census-daemon.log; }}"
            ),
            r"(?m)^TIMER_CENSUS_READY ",
        )?;
    }
    phase(
        shell,
        "census_services_start",
        "python3 /etc/timer-perf/setup.py start && cat /var/tmp/timer-perf/redis.log && cat /var/tmp/timer-perf/nginx-error.log && echo CENSUS_SERVICES_READY",
        r"(?m)^CENSUS_SERVICES_READY\s*$",
    )?;
    client("ready", "none")?;
    phase(shell, "census_network", "ip -j addr && echo CENSUS_NETWORK_READY", r"(?m)^CENSUS_NETWORK_READY\s*$")?;
    if !plain { census_control("ping", 0, 0)?; }
    census_control("plan", 0, 0)?;
    let plan = fs::read_to_string(directory.join("census-plan.tsv"))
        .map_err(|error| Error::Pty(error.into()))?;
    let go_runs = env::var("CENSUS_GO_RUNS")
        .unwrap_or_else(|_| "5".into())
        .parse::<usize>()
        .unwrap();
    assert!((1..=100).contains(&go_runs));
    for line in plan.lines() {
        let fields: Vec<_> = line.split('\t').collect();
        assert_eq!(fields.len(), 3);
        let case_id = fields[0].parse::<u64>().unwrap();
        let mode = fields[1].parse::<u64>().unwrap();
        let case = fields[2];
        let name = format!("census_{case_id}_m{mode}_{case}");
        let churn_key = format!("churn_census_{case_id}");
        if case == "redis_churn" {
            phase(
                shell,
                &format!("{name}_load_start"),
                &format!(
                    "/etc/timer-perf/timer-perf load churn 5000 120 >/var/tmp/timer-load-{churn_key}.log 2>&1 & echo $! >/var/tmp/timer-load-{churn_key}.pid; for i in $(seq 1 100); do grep -q '^PERF_LOAD_STARTED ' /var/tmp/timer-load-{churn_key}.log && break; sleep 0.05; done; kill -0 $(cat /var/tmp/timer-load-{churn_key}.pid) && cat /var/tmp/timer-load-{churn_key}.log && /etc/timer-perf/timer-perf stamp {churn_key}_begin >/var/tmp/{churn_key}.begin && cat /var/tmp/{churn_key}.begin"
                ),
                r"(?m)^PERF_LOAD_STARTED \{",
            )?;
        }
        if !plain {
            census_control("start", case_id, mode)?;
        }
        let window_ticks = qemu_ticks();
        let window_started = Instant::now();
        let result = (|| -> Result<(), Error> {
            match case {
                "idle" | "future" | "periodic" => phase(
                    shell,
                    &name,
                    &format!("{workload} {case}"),
                    r"(?m)^CENSUS_WORKLOAD \{",
                )?,
                "go" => {
                    phase(
                        shell,
                        &format!("{name}_begin"),
                        "echo CENSUS_GO_BEGIN",
                        r"(?m)^CENSUS_GO_BEGIN\s*$",
                    )?;
                    for repetition in 0..go_runs {
                        let marker = PathBuf::from(env::var("PROBE_HOST_MARKER").unwrap());
                        if marker.exists() {
                            fs::remove_file(&marker).map_err(|error| Error::Pty(error.into()))?;
                        }
                        let result = notingo_host_sample(shell);
                        if marker.exists() {
                            fs::copy(
                                marker,
                                directory.join(format!("census-go-{case_id}-{repetition}.json")),
                            )
                            .map_err(|error| Error::Pty(error.into()))?;
                        }
                        result?;
                    }
                    phase(
                        shell,
                        &format!("{name}_end"),
                        "echo CENSUS_GO_END",
                        r"(?m)^CENSUS_GO_END\s*$",
                    )?;
                }
                "redis" | "redis_churn" => {
                    phase(
                        shell,
                        &format!("{name}_begin"),
                        "echo CENSUS_REDIS_BEGIN",
                        r"(?m)^CENSUS_REDIS_BEGIN\s*$",
                    )?;
                    census_redis(case_id)?;
                    phase(
                        shell,
                        &format!("{name}_end"),
                        "echo CENSUS_REDIS_END",
                        r"(?m)^CENSUS_REDIS_END\s*$",
                    )?;
                }
                _ => {
                    let micro: Vec<_> = case.split('_').collect();
                    assert_eq!(micro.len(), 3);
                    assert_eq!(micro[0], "micro");
                    assert!(matches!(micro[1], "1" | "64" | "4096"));
                    assert!(matches!(micro[2], "cancel" | "drop" | "rearm" | "expiry"));
                    phase(
                        shell,
                        &name,
                        &format!("/etc/timer-perf/timer-perf micro {} {}", micro[1], micro[2]),
                        r"(?m)^PERF_MICRO \{",
                    )?;
                }
            }
            Ok(())
        })();
        let window_elapsed_ns = window_started.elapsed().as_nanos();
        let window_cpu_ticks = if result.is_ok() {
            (qemu_ticks() - window_ticks).to_string()
        } else {
            "null".into()
        };
        // FREEZE runs after workload errors too, unless the watchdog stopped the VM.
        let freeze = if plain {
            Ok(())
        } else {
            census_control("freeze", case_id, mode)
        };
        let mut windows = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(directory.join("census-windows.jsonl"))
            .map_err(|error| Error::Pty(error.into()))?;
        writeln!(windows, "{{\"case_id\":{case_id},\"mode\":{mode},\"workload\":\"{case}\",\"plain\":{plain},\"host_elapsed_ns\":{window_elapsed_ns},\"qemu_cpu_ticks\":{window_cpu_ticks},\"workload_passed\":{},\"freeze_passed\":{}}}", result.is_ok(), freeze.is_ok())
            .map_err(|error| Error::Pty(error.into()))?;
        if case == "redis_churn" {
            let stopped = phase(
                shell,
                &format!("{name}_load_stop"),
                &format!(
                    "/etc/timer-perf/timer-perf stamp {churn_key}_end >/var/tmp/{churn_key}.end && kill -TERM $(cat /var/tmp/timer-load-{churn_key}.pid) && wait $(cat /var/tmp/timer-load-{churn_key}.pid) && cat /var/tmp/timer-load-{churn_key}.log && python3 /etc/timer-perf/loadcheck.py {churn_key}"
                ),
                r"(?m)^PERF_LOAD_VALIDATED \{",
            );
            result?;
            freeze?;
            stopped?;
        } else {
            result?;
            freeze?;
        }
    }
    phase(
        shell,
        "census_services_stop",
        "python3 /etc/timer-perf/setup.py stop",
        r"(?m)^PERF_SERVICES_STOPPED\s*$",
    )?;
    if !plain {
        phase(
            shell,
            "census_export",
            &format!("python3 {exporter}"),
            r"(?m)^CENSUS_EXPORT_DONE\s*$",
        )?;
        phase(
            shell,
            "census_daemon_stop",
            "kill -TERM $(cat /var/tmp/timer-census-daemon.pid) && wait $(cat /var/tmp/timer-census-daemon.pid); echo CENSUS_DAEMON_STOPPED",
            r"(?m)^CENSUS_DAEMON_STOPPED\s*$",
        )?;
    }
    fs::write(directory.join("complete.tmp"), "complete\n")
        .map_err(|error| Error::Pty(error.into()))?;
    fs::rename(directory.join("complete.tmp"), directory.join("complete"))
        .map_err(|error| Error::Pty(error.into()))?;
    Ok(())
}
