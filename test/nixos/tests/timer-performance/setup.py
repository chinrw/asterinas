# SPDX-License-Identifier: MPL-2.0
"""Prepare a fixed static HTTP document and an in-memory Redis instance."""
from pathlib import Path
import os
import subprocess
import sys
import time

root = Path('/var/tmp/timer-perf')
root.mkdir(exist_ok=True)
if sys.argv[1] == 'start':
    (root/'index.html').write_text('timer-perf\n')
    (root/'nginx.conf').write_text('''user root;
worker_processes 1;
pid /var/tmp/timer-perf/nginx.pid;
error_log /var/tmp/timer-perf/nginx-error.log;
events { worker_connections 512; }
http { access_log off; server { listen 8080; root /var/tmp/timer-perf; } }
''')
    subprocess.run(['nginx','-c',str(root/'nginx.conf')],check=True)
    subprocess.run(['redis-server','--bind','0.0.0.0','--port','6379',
                    '--protected-mode','no','--save','','--appendonly','no',
                    '--daemonize','yes','--pidfile',str(root/'redis.pid'),
                    '--logfile',str(root/'redis.log')],check=True)
    print('PERF_SERVICES_STARTED')
elif sys.argv[1] == 'stop':
    pids=[int((root/name).read_text()) for name in ['nginx.pid','redis.pid']]
    subprocess.run(['nginx','-c',str(root/'nginx.conf'),'-s','quit'],check=True)
    subprocess.run(['redis-cli','-p','6379','shutdown','nosave'],check=True)
    for pid in pids:
        for _ in range(100):
            try:
                os.kill(pid,0)
            except ProcessLookupError:
                break
            time.sleep(.05)
        else:
            raise RuntimeError(f'service PID {pid} did not exit')
    print('PERF_SERVICES_STOPPED')
else:
    raise SystemExit(2)
