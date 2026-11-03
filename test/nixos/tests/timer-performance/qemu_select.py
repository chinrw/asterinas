#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Add Linux boot files to the otherwise unchanged owned QEMU command."""
import os
from pathlib import Path
import sys
args=sys.argv[1:]
# Both OS variants use private overlays for otherwise unused auxiliary disks.
for i,value in enumerate(args[:-1]):
    if value=='-drive' and 'id=u0,' not in args[i+1] and 'file=' in args[i+1]:
        if 'snapshot=' not in args[i+1] and 'readonly=on' not in args[i+1]:
            args[i+1] += ',snapshot=on'
kind=os.environ['HISTORY_OS']
assert kind in {'asterinas','linux'}
if kind=='linux':
    boot=Path(os.environ['HISTORY_BOOT_BUNDLE'])
    assert boot.is_absolute() and (boot/'kernel').is_file() and (boot/'initrd').is_file()
    assert '-kernel' not in args and '-initrd' not in args and '-append' not in args
    args += ['-kernel',str(boot/'kernel'),'-initrd',str(boot/'initrd'),'-append',
        'console=ttyS0,115200 console=hvc0 earlycon=uart8250,io,0x3f8,115200 loglevel=3 systemd.log_level=crit panic=1 rdinit=/init root=/dev/vda2 init=/nix/var/nix/profiles/system/init systemd.getty_auto=no net.ifnames=0']
owned=Path(__file__).resolve().parent/'owned_qemu.py'
os.execv(sys.executable,[sys.executable,str(owned),*args])
