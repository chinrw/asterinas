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
