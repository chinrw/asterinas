# SPDX-License-Identifier: MPL-2.0

"""Wrap simple upstream group entries only in the Linux initramfs."""

from pathlib import Path
import re
import sys


def prepare_entry(path):
    lines = path.read_text().splitlines()
    include = '. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"'
    if include in lines:
        return  # This group explicitly registers workloads or platform conditions.

    group = path.parent.name
    entry = ["#!/bin/sh", "set -e", include]
    for number, line in enumerate(lines, 1):
        command = line.strip()
        if not command or command.startswith("#") or command == "set -e":
            continue
        if not re.fullmatch(r"\./[a-zA-Z0-9_./-]+", command) or ".." in command.split(
            "/"
        ):
            raise ValueError(
                f"{path}:{number}: explicit workload registration required for {command!r}"
            )

        register = "run_test_case"
        if group == "device":
            if command.startswith("./drm/"):
                register = 'skip_test_case "The Linux regression VM has no DRM device."'
            elif command == "./vt/vt_ioctl":
                register = (
                    'skip_test_case "The Linux regression VM uses a serial console."'
                )
            elif command in ("./evdev", "./framebuffer"):
                register = 'skip_test_case "The Linux regression VM has no input or display device."'
            elif command == "./hwrng":
                register = "run_test_case_requiring CONFIG_HW_RANDOM_VIRTIO"
        elif group == "security" and command == "./lsm/yama":
            register = "run_test_case_requiring CONFIG_SECURITY_YAMA"
        entry.append(f"{register} {command}")
    path.write_text("\n".join([*entry, "finish_test_cases", ""]))


if __name__ == "__main__":
    for script in sys.argv[1:]:
        prepare_entry(Path(script))
