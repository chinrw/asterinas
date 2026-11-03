# SPDX-License-Identifier: MPL-2.0
"""Vary only a declared HTTP connection history before the Redis observation."""
import json,os,re,subprocess,time
from pathlib import Path
import host_clients as clients

def main():
    n=int(os.environ['TAIL_HTTP_HISTORY'])
    assert n in {0,10000}
    begin=time.monotonic_ns()
    result={'http_requests':n,'host_start_ns':begin,'scope':'Same-host OS history control; not the full performance protocol'}
    if n:
        command=[str(clients.TOOLS/'ab'),'-n',str(n),'-c','20',f'http://{clients.CLIENT_HOST}:{clients.HTTP_PORT}/index.html']
        done=subprocess.run(command,capture_output=True,text=True,timeout=120)
        raw=done.stdout+done.stderr
        (clients.OUT/'http-history.txt').write_text(raw)
        assert done.returncode==0 and re.search(r'Complete requests:\s+'+str(n)+r'\b',raw)
        assert re.search(r'Failed requests:\s+0\b',raw)
        assert not re.search(r'Non-2xx responses:|apr_socket_recv:|Connection refused',raw)
        result.update(command=command,exit_code=done.returncode)
    else:
        time.sleep(5)
        result['idle_control_seconds']=5
    result['host_end_ns']=time.monotonic_ns()
    (clients.OUT/'http-history.json').write_text(json.dumps(result,indent=2)+'\n')
    clients.main('redis','churn' if os.environ.get('TAIL_HISTORY_CHURN')=='1' else 'none')

if __name__ == "__main__":
    main()
