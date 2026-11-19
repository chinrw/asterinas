#!/usr/bin/env python3

# SPDX-License-Identifier: MPL-2.0

"""Check the host runner's result protocol without booting a VM."""

import csv
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from linux_regression_report import report

RUNNER = TOOLS / "run_linux_regression.sh"


class LinuxRegressionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="linux-regression-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        (self.bundle / "subsystem-v1").touch()
        self.exclusions = self.root / "exclusions.tsv"
        self.exclusions.write_text("group\tcommand\tcategory\treason\trestore_when\n")
        for name in ("bzImage", "initramfs.cpio.gz", "kernel.config"):
            (self.bundle / name).write_text("fixture\n")
        (self.bundle / "groups").write_text("hello_world\ntime\ndevice\n")
        (self.bundle / "cases.tsv").write_text(
            "group\tcase\taction\tcommand\treason\n"
            "hello_world\t1\tRUN\t./in_c/hello\t\n"
            "time\t1\tRUN\t./clock_nanosleep/nanosleep_err\t\n"
            "time\t2\tRUN\t./timerfd/timerfd_readiness\t\n"
            "device\t1\tSKIP\t./drm/device_node\trequires a DRM device\n"
        )
        self.bin_dir = self.bundle / "host-tools/bin"
        self.bin_dir.mkdir(parents=True)
        # The protocol tests need file names, not formatted multi-gigabyte disks.
        for name in ("truncate", "mke2fs", "mkfs.exfat"):
            self.executable(name, "#!/bin/sh\nexit 0\n")
        self.qemu = self.executable(
            "qemu-fixture",
            """#!/usr/bin/env python3
import os
import pathlib
import re
import signal
import subprocess
import sys
import sys
import time

if '--version' in sys.argv:
    print('QEMU protocol fixture')
    sys.exit(0)
args = sys.argv[1:]
cmdline = args[args.index('-append') + 1]
group = re.search(r'regression_group=([a-z0-9_]+)', cmdline)[1]
case = re.search(r'regression_case=([0-9]+)', cmdline)[1]
record = next(arg[5:] for arg in args if arg.startswith('file:'))
mode = os.environ.get('REGRESSION_FIXTURE_MODE', 'pass')
with pathlib.Path(os.environ['REGRESSION_FIXTURE_CALLS']).open('a') as calls:
    calls.write(f'{group} {case}\\n')
if batch := re.search(r'regression_batch=([0-9,]+)', cmdline):
    numbers = batch[1].split(',')
    out = pathlib.Path(record)
    def emit(line):
        with out.open('a') as stream:
            print(line, file=stream)
        print(line, flush=True)
    print('Guest boot output')
    bad = False
    for i, number in enumerate(numbers):
        emit(f'BATCH_BEGIN {group} {number} {i}.00')
        print(f'REGRESSION_CASE_START {group} {number}', flush=True)
        if mode == 'poweroff' and group == 'time' and number == '2':
            sys.exit(0)
        status = 17 if mode == 'first_case_failure' and group == 'time' and number == '1' else 0
        bad |= bool(status)
        emit(f'BATCH_END {group} {number} {status} {i}.20')
    with out.open('a') as stream:
        print(f'LINUX_REGRESSION_RESULT {group} {case} {int(bad)}', file=stream)
        if mode == 'duplicate_final':
            print(f'LINUX_REGRESSION_RESULT {group} {case} {int(bad)}', file=stream)
    sys.exit(0)
if mode == 'crash_before_start':
    sys.exit(134)
if mode != 'missing_start':
    print(f'REGRESSION_CASE_START {group} {case}', flush=True)
if mode == 'duplicate_start':
    print(f'REGRESSION_CASE_START {group} {case}', flush=True)
if mode == 'missing':
    sys.exit(0)
if mode == 'timeout':
    time.sleep(60)
if mode == 'cancel':
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    def stop(signum, frame):
        child.wait(timeout=2)
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    pathlib.Path(record + '.pids').write_text(f'{os.getpid()} {child.pid}')
    time.sleep(60)
status = 17 if mode == 'test_failure' or (mode == 'first_case_failure' and group == 'time' and case == '1') else 0
if mode == 'wrong_group':
    group = 'wrong'
if mode == 'wrong_case':
    case = '99'
line = f'LINUX_REGRESSION_RESULT {group} {case} {status}\\n'
if mode == 'duplicate':
    line += line
pathlib.Path(record).write_text(line)
if mode != 'truncated_console':
    print(f'REGRESSION_CONSOLE_END {group} {case}')
sys.exit(19 if mode == 'qemu_failure' else 0)
""",
        )

    def executable(self, name, text):
        path = self.bin_dir / name
        path.write_text(text)
        path.chmod(0o755)
        return path

    def run_runner(self, mode="pass", *options, guest_mode="case"):
        self.output = self.root / "output"
        return subprocess.run(
            [
                "bash", str(RUNNER), "--bundle", str(self.bundle),
                "--exclusions", str(self.exclusions),
                "--output", str(self.output), "--accel", "tcg",
                "--timeout", "1", *(["--mode", guest_mode] if guest_mode else []), *options,
            ],
            env=self.fixture_environment(mode),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
        )

    def fixture_environment(self, mode):
        environment = os.environ.copy()
        environment.pop("REGRESSION_GUEST_MODE", None)
        return dict(
            environment,
            QEMU=str(self.qemu),
            REGRESSION_FIXTURE_MODE=mode,
            REGRESSION_FIXTURE_CALLS=str(self.root / "qemu-calls"),
        )

    def calls(self):
        source = self.root / "qemu-calls"
        return source.read_text().splitlines() if source.exists() else []

    def exclude(self, group="time", command="./clock_nanosleep/nanosleep_err",
                category="test", reason="known expectation mismatch",
                restore_when="the exact Linux expectation is verified"):
        with self.exclusions.open("a") as output:
            output.write("\t".join([group, command, category, reason, restore_when]) + "\n")

    def results(self):
        with (self.output / "results.tsv").open() as source:
            return list(csv.DictReader(source, delimiter="\t"))

    def test_complete_success_accounts_for_every_registered_case(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout)
        rows = self.results()
        self.assertCountEqual(
            [(row["group"], row["case"], row["result"]) for row in rows],
            [("hello_world", "1", "PASS"), ("time", "1", "PASS"),
             ("time", "2", "PASS"), ("device", "1", "SKIP")],
        )
        self.assertEqual(self.calls(), ["hello_world 1", "time 1", "time 2"])

    def test_last_manifest_row_runs_without_a_trailing_newline(self):
        manifest = self.bundle / "cases.tsv"
        lines = manifest.read_text().splitlines()
        final_row = next(row for row in lines if row.startswith("time\t2\t"))
        lines.remove(final_row)
        manifest.write_text("\n".join([*lines, final_row]))
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls(), ["hello_world 1", "time 1", "time 2"])
        self.assertEqual(len(self.results()), 4)
        self.assertEqual(
            next(row["result"] for row in self.results()
                 if row["group"] == "time" and row["case"] == "2"),
            "PASS",
        )

    def test_nonzero_guest_status_fails_and_continues_to_next_group(self):
        result = self.run_runner("test_failure")
        self.assertEqual(result.returncode, 1, result.stdout)
        rows = self.results()
        self.assertEqual(len(rows), 4)
        runs = [row for row in rows if row["result"] != "SKIP"]
        self.assertEqual(len(runs), 3)
        self.assertTrue(all(row["test_status"] == "17" for row in runs))
        self.assertTrue(all(row["result"] == "FAIL" for row in runs))

    def test_failure_does_not_stop_later_cases_in_the_same_group(self):
        result = self.run_runner("first_case_failure", "--group", "time")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(
            [(row["case"], row["result"]) for row in self.results()],
            [("1", "FAIL"), ("2", "PASS")],
        )
        self.assertEqual(self.calls(), ["time 1", "time 2"])

    def test_qemu_failure_overrides_success_record(self):
        result = self.run_runner("qemu_failure")
        self.assertEqual(result.returncode, 1, result.stdout)
        runs = [row for row in self.results() if row["result"] != "SKIP"]
        self.assertTrue(all(row["qemu_status"] == "19" for row in runs))

    def test_missing_completion_fails(self):
        result = self.run_runner("missing")
        self.assertEqual(result.returncode, 1, result.stdout)
        runs = [row for row in self.results() if row["result"] != "SKIP"]
        self.assertTrue(all(row["result"] == "FAIL" for row in runs))

    def test_duplicate_completion_fails(self):
        result = self.run_runner("duplicate")
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_wrong_group_completion_fails(self):
        result = self.run_runner("wrong_group")
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_wrong_case_completion_fails(self):
        result = self.run_runner("wrong_case", "--group", "hello_world")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.results()[0]["result"], "FAIL")

    def test_success_record_without_start_is_not_run(self):
        result = self.run_runner("missing_start", "--group", "hello_world")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.results()[0]["result"], "NOT_RUN")

    def test_qemu_crash_before_start_is_infrastructure_not_run(self):
        result = self.run_runner("crash_before_start", "--group", "hello_world")
        self.assertEqual(result.returncode, 1, result.stdout)
        row = self.results()[0]
        self.assertEqual(row["result"], "NOT_RUN")
        self.assertEqual(row["qemu_status"], "134")
        self.assertEqual(row["test_status"], "missing")
        self.assertIn("QEMU", row["reason"])
        self.assertIn("before workload started", row["reason"])

    def test_duplicate_start_is_rejected(self):
        result = self.run_runner("duplicate_start", "--group", "hello_world")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.results()[0]["result"], "FAIL")

    def test_timeout_fails(self):
        result = self.run_runner("timeout", "--group", "hello_world")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.results()[0]["qemu_status"], "124")

    def test_cancellation_cleans_processes_and_accounts_for_remaining_cases(self):
        output = self.root / "cancel-output"
        process = subprocess.Popen(
            [
                "bash", str(RUNNER), "--bundle", str(self.bundle),
                "--exclusions", str(self.exclusions),
                "--output", str(output), "--accel", "tcg",
                "--timeout", "30", "--mode", "case", "--group", "time",
            ],
            env=self.fixture_environment("cancel"),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        vm_group = None
        try:
            pid_files = []
            saw_start = False
            deadline = time.monotonic() + 5
            while not (pid_files and saw_start) and time.monotonic() < deadline:
                self.assertIsNone(process.poll())
                time.sleep(0.01)
                pid_files = list(output.rglob("*.pids"))
                saw_start = any(
                    "REGRESSION_CASE_START time 1" in path.read_text()
                    for path in output.rglob("*.log")
                )
            self.assertEqual(len(pid_files), 1, "QEMU fixture did not start")
            self.assertTrue(saw_start, "QEMU fixture did not emit the start record")
            pids = [int(pid) for pid in pid_files[0].read_text().split()]
            vm_group = os.getpgid(pids[0])
            process.terminate()
            stdout, _ = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 143, stdout)
            for pid in pids:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            self.output = output
            self.assertEqual(
                [(row["case"], row["result"]) for row in self.results()],
                [("1", "FAIL"), ("2", "NOT_RUN")],
            )
        finally:
            for group in (vm_group, process.pid):
                if group is not None:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            process.communicate(timeout=2)

    def test_selected_group_is_the_only_group_run(self):
        result = self.run_runner("pass", "--group", "time")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([row["group"] for row in self.results()], ["time", "time"])
        self.assertEqual(self.calls(), ["time 1", "time 2"])

    def test_selected_case_is_the_only_case_run(self):
        result = self.run_runner("pass", "--group", "time", "--case", "2")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            [(row["group"], row["case"]) for row in self.results()],
            [("time", "2")],
        )
        self.assertEqual(self.calls(), ["time 2"])

    def test_unknown_case_is_rejected(self):
        result = self.run_runner("pass", "--group", "time", "--case", "99")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(self.calls(), [])

    def test_case_selection_requires_group(self):
        result = self.run_runner("pass", "--case", "1")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(self.calls(), [])

    def test_skip_is_reported_without_starting_qemu(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout)
        skips = [row for row in self.results() if row["result"] == "SKIP"]
        self.assertEqual(len(skips), 1)
        self.assertEqual(skips[0]["group"], "device")
        self.assertEqual(skips[0]["case"], "1")
        self.assertEqual(skips[0]["command"], "./drm/device_node")
        self.assertEqual(skips[0]["reason"], "requires a DRM device")
        self.assertNotIn("device 1", self.calls())

    def test_explicit_skip_selection_succeeds_without_starting_qemu(self):
        result = self.run_runner("pass", "--group", "device", "--case", "1")
        self.assertEqual(result.returncode, 0, result.stdout)
        rows = self.results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], "SKIP")
        self.assertEqual(self.calls(), [])

    def test_default_all_skip_manifest_is_not_a_passing_run(self):
        (self.bundle / "groups").write_text("device\n")
        (self.bundle / "cases.tsv").write_text(
            "group\tcase\taction\tcommand\treason\n"
            "device\t1\tSKIP\t./drm/device_node\trequires a DRM device\n"
        )
        result = self.run_runner()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual([row["result"] for row in self.results()], ["SKIP"])

    def test_unknown_group_is_rejected(self):
        result = self.run_runner("pass", "--group", "missing")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(self.calls(), [])

    def test_temporary_exclusion_is_skipped_and_later_case_runs(self):
        self.exclude()
        result = self.run_runner("first_case_failure", "--group", "time")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls(), ["time 2"])
        rows = self.results()
        self.assertEqual(
            [(row["case"], row["result"]) for row in rows],
            [("1", "SKIP"), ("2", "PASS")],
        )
        self.assertIn("[test] known expectation mismatch", rows[0]["reason"])
        self.assertIn("Restore when:", rows[0]["reason"])

    def test_include_excluded_runs_the_case_and_preserves_its_failure(self):
        self.exclude()
        result = self.run_runner(
            "first_case_failure", "--group", "time", "--include-excluded"
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.calls(), ["time 1", "time 2"])
        self.assertEqual(
            [(row["case"], row["result"]) for row in self.results()],
            [("1", "FAIL"), ("2", "PASS")],
        )

    def test_exclusions_match_commands_after_case_numbers_change(self):
        manifest = self.bundle / "cases.tsv"
        manifest.write_text(manifest.read_text().replace(
            "time\t1\tRUN\t./clock_nanosleep/nanosleep_err",
            "time\t7\tRUN\t./clock_nanosleep/nanosleep_err ",
        ))
        self.exclude()
        result = self.run_runner("pass", "--group", "time")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls(), ["time 2"])
        self.assertEqual(
            [(row["case"], row["result"]) for row in self.results()],
            [("7", "SKIP"), ("2", "PASS")],
        )

    def test_include_excluded_keeps_hardware_profile_skips(self):
        self.exclude()
        result = self.run_runner("pass", "--include-excluded")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls(), ["hello_world 1", "time 1", "time 2"])
        skips = [row for row in self.results() if row["result"] == "SKIP"]
        self.assertEqual([(row["group"], row["case"]) for row in skips], [("device", "1")])

    def test_policy_is_validated_against_the_full_manifest_before_selection(self):
        self.exclude()
        self.exclude("hello_world", "./in_c/hello")
        result = self.run_runner("pass", "--group", "time", "--case", "2")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls(), ["time 2"])
        self.assertEqual([row["result"] for row in self.results()], ["PASS"])

    def test_duplicate_exclusion_is_rejected(self):
        self.exclude()
        self.exclude()
        result = self.run_runner()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.calls(), [])

    def test_unknown_exclusion_is_rejected(self):
        self.exclude(command="./missing")
        result = self.run_runner()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.calls(), [])

    def test_existing_profile_skip_cannot_be_temporarily_excluded_again(self):
        self.exclude("device", "./drm/device_node")
        result = self.run_runner()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.calls(), [])

    def test_exclusion_without_reason_is_rejected(self):
        self.exclude(reason="")
        result = self.run_runner()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.calls(), [])

    def test_exclusion_without_restoration_condition_is_rejected(self):
        self.exclude(restore_when="")
        result = self.run_runner()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.calls(), [])

    def test_last_exclusion_row_is_read_without_a_trailing_newline(self):
        self.exclude()
        self.exclusions.write_text(self.exclusions.read_text().rstrip("\n"))
        result = self.run_runner("first_case_failure", "--group", "time")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls(), ["time 2"])
        self.assertEqual([row["result"] for row in self.results()], ["SKIP", "PASS"])

    def test_guest_status_without_complete_console_fails(self):
        result = self.run_runner("truncated_console", "--group", "hello_world")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.results()[0]["reason"], "incomplete guest console log")

    def test_shards_continue_and_print_all_logs_and_results(self):
        plan_dir = self.root / "plan"
        planned = self.run_runner("pass", "--plan-only", "--output", str(plan_dir))
        self.assertEqual(planned.returncode, 0, planned.stdout)
        self.assertEqual(self.calls(), [])
        for index in range(4):
            result = self.run_runner(
                "first_case_failure", "--shard-index", str(index), "--shard-count", "4",
                "--output", str(self.root / f"shard-{index}"),
            )
            self.assertEqual(result.returncode, int(index == 1), result.stdout)
        self.assertCountEqual(self.calls(), ["hello_world 1", "time 1", "time 2"])

        def collect():
            output = StringIO()
            with redirect_stdout(output):
                failed = report(plan_dir / "cases.tsv", self.root, 4)
            return failed, output.getvalue()

        failed, output = collect()
        self.assertTrue(failed)
        for group, number in [("hello_world", 1), ("time", 1), ("time", 2)]:
            self.assertIn(f"REGRESSION_CASE_START {group} {number}", output)
        self.assertLess(output.index("::endgroup::"), output.index("TEST RESULTS"))
        self.assertLess(output.index("TEST RESULTS"), output.index("TEST SUMMARY"))
        self.assertIn("requires a DRM device", output)
        self.assertIn("PASS: 2", output)
        self.assertIn("FAIL: 1", output)

        (self.root / "shard-1/results.tsv").unlink()
        with (self.root / "shard-3/results.tsv").open("a") as target:
            target.write((self.root / "shard-2/results.tsv").read_text().splitlines()[1] + "\n")
            target.write("time\t2\t")
        (self.root / "shard-0/hello_world/1.log").unlink()
        failed, output = collect()
        self.assertTrue(failed)
        self.assertIn("Missing results from shard 1", output)
        self.assertIn("Duplicate or unexpected result for time/2", output)
        self.assertIn("Incomplete result in shard 3", output)
        self.assertIn("Missing console log for hello_world/1", output)
        self.assertIn("MISSING: 1", output)
        self.assertIn("REGRESSION_CASE_START time 1", output)

    def test_summary_and_full_logs_can_be_viewed_separately_after_failure(self):
        shard = self.root / "shard-0"
        result = self.run_runner("first_case_failure", "--output", str(shard))
        self.assertEqual(result.returncode, 1)
        with (shard / "time/1.log").open("a") as log:
            log.write("assertion failed [got 1, expected 0]\n")
        with (shard / "time/2.log").open("a") as log:
            log.write("test_policy skipped: `scope == YAMA_SCOPE_UNAVAILABLE` is true\n")
        summary, logs = StringIO(), StringIO()
        with redirect_stdout(summary):
            self.assertTrue(report(shard / "cases.tsv", self.root, 1, console_output=logs))
        self.assertIn("TEST SUMMARY", summary.getvalue())
        self.assertIn("assertion failed [got 1, expected 0]", summary.getvalue())
        self.assertIn("Skipped subtests: test_policy", summary.getvalue())
        self.assertNotIn("REGRESSION_CASE_START", summary.getvalue())
        self.assertIn("::group::FAIL time/1", logs.getvalue())
        self.assertIn("::group::PASS time/2", logs.getvalue())
        self.assertNotIn("TEST SUMMARY", logs.getvalue())

    def test_default_reuses_one_guest_per_subsystem_and_continues(self):
        result = self.run_runner("first_case_failure", guest_mode=None)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.calls(), ["hello_world 1", "time 1"])
        rows = {(r["group"], r["case"]): r["result"] for r in self.results()}
        self.assertEqual(rows[("time", "1")], "FAIL")
        self.assertEqual(rows[("time", "2")], "PASS")
        self.assertIn("BATCH_END time 2", (self.output / "time/2.log").read_text())

    def test_early_group_shutdown_preserves_completed_cases(self):
        with (self.bundle / "cases.tsv").open("a") as plan:
            plan.write("time\t3\tRUN\t./third\t\n")
        result = self.run_runner("poweroff", "--group", "time", "--mode", "subsystem")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.calls(), ["time 1"])
        self.assertEqual([r["result"] for r in self.results()], ["PASS", "FAIL", "NOT_RUN"])
        self.assertTrue((self.output / "time/guest.error").is_file())

    def test_corrupt_group_completion_fails_report_even_after_passes(self):
        shard = self.root / "shard-0"
        result = self.run_runner("duplicate_final", "--output", str(shard), "--mode", "subsystem")
        self.assertEqual(result.returncode, 1, result.stdout)
        summary, logs = StringIO(), StringIO()
        with redirect_stdout(summary):
            self.assertTrue(report(shard / "cases.tsv", self.root, 1, mode="subsystem", console_output=logs))
        self.assertIn("invalid guest session", summary.getvalue())
        self.assertEqual(logs.getvalue().count("Guest boot output"), 2)

    def test_group_shards_keep_whole_groups_and_report_all_cases(self):
        for i in range(2):
            result = self.run_runner("pass", "--mode", "subsystem", "--output", str(self.root / f"shard-{i}"),
                                     "--shard-count", "2", "--shard-index", str(i))
            self.assertEqual(result.returncode, 0, result.stdout)
        self.assertCountEqual(self.calls(), ["time 1", "hello_world 1"])
        with redirect_stdout(StringIO()):
            self.assertFalse(report(self.bundle / "cases.tsv", self.root, 2, mode="subsystem"))

    def test_previous_bundle_defaults_to_case_mode(self):
        (self.bundle / "subsystem-v1").unlink()
        result = self.run_runner("pass", "--group", "time", guest_mode=None)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls(), ["time 1", "time 2"])

    def test_simple_entry_generator_includes_new_commands_and_rejects_shell_control_flow(self):
        script = self.root / "time/run_test.sh"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\nset -e\n./a\n./b\n")
        command = [sys.executable, str(TOOLS.parent / "test/initramfs/nix/linux-regression-entries.py"), str(script)]
        subprocess.run(command, check=True)
        self.assertIn("run_test_case ./a\nrun_test_case ./b\n", script.read_text())
        script.write_text("#!/bin/sh\n./a &\n./b\n")
        rejected = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("explicit workload registration required", rejected.stderr)

    def test_invalid_shards_are_rejected(self):
        for count, index in [("0", "0"), ("3", "3"), ("4", "-1"), ("257", "0")]:
            result = self.run_runner("pass", "--shard-count", count, "--shard-index", index)
            self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(self.calls(), [])

    def test_successful_report_includes_passing_console(self):
        self.assertEqual(self.run_runner("pass", "--output", str(self.root / "shard-0")).returncode, 0)
        output = StringIO()
        with redirect_stdout(output):
            self.assertFalse(report(self.root / "shard-0/cases.tsv", self.root, 1))
        self.assertIn("REGRESSION_CONSOLE_END hello_world 1", output.getvalue())
        self.assertIn("PASS: 3", output.getvalue())

    def test_empty_group_list_is_rejected(self):
        (self.bundle / "groups").write_text("")
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_duplicate_group_is_rejected(self):
        (self.bundle / "groups").write_text("time\ntime\n")
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_empty_case_list_is_rejected(self):
        (self.bundle / "cases.tsv").write_text("group\tcase\taction\tcommand\treason\n")
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
