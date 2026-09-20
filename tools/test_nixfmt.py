# SPDX-License-Identifier: MPL-2.0

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


HELPER = Path(__file__).with_name("nixfmt.sh").resolve()
BASH = shutil.which("bash")


class NixfmtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aster-nixfmt-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.selected = self.root / "selected"
        self.selected.mkdir()
        self.outside = self.root / "outside.nix"
        self.outside.write_text("{outside=1;}\n")

    def run_helper(self, *args, env=None):
        return subprocess.run(
            [BASH, str(HELPER), *map(str, args)],
            cwd=self.root,
            env=env,
            capture_output=True,
        )

    def snapshot(self):
        return {
            str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mode)
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def mock(self, name, body):
        directory = self.root / "mock-bin"
        directory.mkdir(exist_ok=True)
        executable = directory / name
        executable.write_text(f"#!{BASH}\n{body}\n")
        executable.chmod(0o755)
        return {**os.environ, "PATH": str(directory) + os.pathsep + os.environ["PATH"]}

    def test_check_is_read_only_and_format_preserves_scope(self):
        names = ["normal.nix", "space name.nix", "中文.nix", "line\nbreak.nix", "*.nix"]
        for name in names:
            (self.selected / name).write_text("{a=1;}\n")
        (self.selected / "ignored.txt").write_text("{b=2;}\n")
        (self.selected / "link.nix").symlink_to(self.outside)
        before = self.snapshot()
        result = self.run_helper("--check", "--", "selected")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)

        result = self.run_helper("--", "selected")
        self.assertEqual(result.returncode, 0, result.stderr)
        formatted = self.snapshot()
        self.assertEqual(set(formatted), set(before))
        for name in names:
            self.assertNotEqual(formatted["selected/" + name][0], before["selected/" + name][0])
        for name in ["outside.nix", "selected/ignored.txt", "selected/link.nix"]:
            self.assertEqual(formatted[name], before[name])
        self.assertTrue((self.selected / "link.nix").is_symlink())

        result = self.run_helper("--check", "--", "selected")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), formatted)
        self.assertEqual(self.run_helper("--", "selected").returncode, 0)
        self.assertEqual(self.snapshot(), formatted)

    def test_explicit_file_roots_are_not_find_expressions(self):
        names = ["-option.nix", "!", "("]
        for name in names:
            directory = self.root / name
            directory.mkdir()
            (directory / "file.nix").write_text("{a=1;}\n")
        result = self.run_helper("--", *names)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_helper("--check", "--", *names)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_individual_file(self):
        path = self.selected / "file.nix"
        path.write_text("{a=1;}\n")
        self.assertEqual(self.run_helper("--", path).returncode, 0)
        self.assertEqual(self.run_helper("--check", "--", path).returncode, 0)

    def test_empty_directory_does_not_invoke_formatter(self):
        env = self.mock("nixfmt", "exit 23")
        result = self.run_helper("--check", "--", "selected", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_arguments_fail(self):
        before = self.snapshot()
        cases = [
            (),
            ("--",),
            ("--check", "--"),
            ("--", ""),
            ("--check", "--", ""),
            ("--unknown", "--", "selected"),
            ("selected",),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertNotEqual(self.run_helper(*args).returncode, 0)
                self.assertEqual(self.snapshot(), before)
        self.assertNotEqual(self.run_helper("--check", "--", "missing").returncode, 0)

    def test_missing_formatter_fails(self):
        result = self.run_helper("--check", "--", "selected", env={**os.environ, "PATH": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"nixfmt is not in PATH", result.stderr)

    def test_enumeration_failure_propagates(self):
        env = self.mock("find", "exit 17")
        self.assertNotEqual(self.run_helper("--check", "--", "selected", env=env).returncode, 0)

    def test_formatter_failure_propagates(self):
        (self.selected / "file.nix").write_text("{a=1;}\n")
        env = self.mock("nixfmt", "exit 23")
        self.assertNotEqual(self.run_helper("--check", "--", "selected", env=env).returncode, 0)


if __name__ == "__main__":
    unittest.main()
