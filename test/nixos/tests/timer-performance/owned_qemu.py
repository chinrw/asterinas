#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Launch or signal only the QEMU instance owned by one benchmark sample."""
import json
import os
from pathlib import Path
import signal
import select
import sys

def start_ticks(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]

def stop(owner):
    pid, expected, sample = Path(owner).read_text().split()
    try:
        fd = os.pidfd_open(int(pid))
    except ProcessLookupError:
        return
    try:
        try:
            actual = start_ticks(pid)
        except FileNotFoundError:
            return
        if actual != expected:
            raise RuntimeError("QEMU process identity changed")
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        if not select.select([fd], [], [], 5)[0]:
            raise RuntimeError("owned QEMU did not exit")
    except ProcessLookupError:
        pass
    finally:
        os.close(fd)

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--stop":
        stop(sys.argv[2])
    else:
        owner = Path(os.environ["PROBE_QEMU_OWNER"])
        sample = os.environ["PROBE_SAMPLE_ID"]
        pid = os.getpid()
        temporary = owner.with_suffix(".tmp")
        temporary.write_text(f"{pid} {start_ticks(pid)} {sample}\n")
        temporary.replace(owner)
        arguments=sys.argv[1:]
        if os.environ.get("TAIL_QMP") in {"1", "map"}:
            qmp=owner.with_suffix(".qmp")
            assert len(str(qmp).encode())<104
            arguments += ["-qmp",f"unix:{qmp},server=on,wait=off","-name","guest=timer-tail,debug-threads=on"]
        owner.with_suffix(".argv.json").write_text(json.dumps(arguments,indent=2)+"\n")
        os.execvp("qemu-system-x86_64",["qemu-system-x86_64",*arguments])
