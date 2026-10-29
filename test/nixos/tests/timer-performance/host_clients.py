# SPDX-License-Identifier: MPL-2.0
"""Validate the existing closed-loop workloads and separate fixed-rate probes."""
import asyncio
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

OUT = Path(os.environ['PERF_SAMPLE_DIR'])
TOOLS = Path(os.environ['PERF_HOST_CLIENTS'])/'bin'
PILOT = os.environ.get('PERF_PILOT') == '1'
HTTP_PORT = int(os.environ['NGINX_PORT'])
REDIS_PORT = int(os.environ['REDIS_PORT'])
CLIENT_HOST = os.environ.get('PERF_CLIENT_HOST', '127.0.0.1')

def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2)+'\n')
    temporary.replace(path)

def ready():
    last = None
    for _ in range(100):
        try:
            connection = http.client.HTTPConnection(CLIENT_HOST, HTTP_PORT, timeout=1)
            connection.request('GET','/index.html')
            response = connection.getresponse()
            assert response.status == 200 and response.read() == b'timer-perf\n'
            connection.close()
            with socket.create_connection((CLIENT_HOST, REDIS_PORT),timeout=1) as sock:
                sock.sendall(b'*1\r\n$4\r\nPING\r\n')
                assert sock.recv(64) == b'+PONG\r\n'
            return
        except (OSError, AssertionError, http.client.HTTPException) as error:
            last = error
            time.sleep(.1)
    raise RuntimeError(f'service readiness failed: {last}')

async def fixed_rate(service, rate, seconds, destination):
    """Include client-side queue delay by timing from each planned arrival."""
    queue = asyncio.Queue()
    records = []
    port = HTTP_PORT if service == 'nginx' else REDIS_PORT
    async def worker():
        reader, writer = await asyncio.wait_for(asyncio.open_connection(CLIENT_HOST, port),3)
        try:
            while True:
                job = await queue.get()
                if job is None:
                    queue.task_done()
                    break
                number, scheduled = job
                started = time.monotonic_ns()
                async def request():
                    if service == 'nginx':
                        writer.write(b'GET /index.html HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n')
                        await writer.drain()
                        headers = await reader.readuntil(b'\r\n\r\n')
                        assert headers.splitlines()[0].split()[1] == b'200'
                        length = re.search(rb'(?im)^content-length:\s*(\d+)',headers)
                        assert length and int(length[1]) == 11
                        assert await reader.readexactly(11) == b'timer-perf\n'
                    else:
                        writer.write(b'*2\r\n$3\r\nGET\r\n$14\r\nfixed-perf-key\r\n')
                        await writer.drain()
                        assert await reader.readexactly(9) == b'$3\r\nabc\r\n'
                error = None
                try:
                    await asyncio.wait_for(request(),5)
                except Exception as exc:
                    error = f'{type(exc).__name__}: {exc}'
                finished = time.monotonic_ns()
                records.append({'id':number,'scheduled_ns':scheduled,'started_ns':started,
                                'finished_ns':finished,'latency_ns':finished-scheduled,
                                'client_queue_ns':started-scheduled,'error':error})
                queue.task_done()
                if error:
                    raise RuntimeError(error)
        finally:
            writer.close()
            await writer.wait_closed()
    workers = [asyncio.create_task(worker()) for _ in range(20)]
    start = time.monotonic_ns()+100_000_000
    total = rate*seconds
    failure = None
    try:
        for number in range(total):
            scheduled = start+number*1_000_000_000//rate
            await asyncio.sleep(max(0,(scheduled-time.monotonic_ns())/1e9))
            if any(task.done() for task in workers):
                await asyncio.gather(*[task for task in workers if task.done()])
                raise RuntimeError('fixed-rate worker ended prematurely')
            queue.put_nowait((number,scheduled))
        await asyncio.wait_for(queue.join(),10)
        for _ in workers: queue.put_nowait(None)
        await asyncio.gather(*workers)
    except Exception as exc:
        failure=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        for task in workers:
            if not task.done(): task.cancel()
        await asyncio.gather(*workers,return_exceptions=True)
        atomic_json(destination,{'service':service,'offered_rps':rate,'duration_s':seconds,
            'planned':total,'completed':len(records),'failure':failure,'records':records})
    assert len(records) == total and len({r['id'] for r in records}) == total
    assert not any(r['error'] for r in records)
    records.sort(key=lambda r:r['id'])
    values=sorted(r['latency_ns'] for r in records)
    return {'service':service,'offered_rps':rate,'duration_s':seconds,'planned':total,
            'completed':len(records),'p50_ns':values[(len(values)-1)//2],
            'p99_ns':values[(len(values)*99-1)//100],'max_ns':values[-1],
            'max_client_queue_ns':max(r['client_queue_ns'] for r in records),'records':records}

def main(service, mode):
    ready()
    if service == 'ready':
        print('PERF_HOST_READY')
        return
    n = (1000 if PILOT else 10000) if service == 'nginx' else (10000 if PILOT else 100000)
    if service == 'nginx':
        binary=TOOLS/'ab'
        command=[str(binary),'-n',str(n),'-c','20',f'http://{CLIENT_HOST}:{HTTP_PORT}/index.html']
    else:
        binary=TOOLS/'redis-benchmark'
        command=[str(binary),'-h',CLIENT_HOST,'-p',str(REDIS_PORT),'-n',str(n),'-c','20','-t','set,get']
    start=time.monotonic_ns()
    run=subprocess.run(command,capture_output=True,text=True,timeout=90)
    elapsed=time.monotonic_ns()-start
    raw=run.stdout+'\n'+run.stderr
    (OUT/f'{service}-{mode}.log').write_text(raw)
    assert run.returncode == 0, (service,run.returncode,raw[-1000:])
    result={'service':service,'mode':mode,'command':command,'exit_code':run.returncode,
            'host_elapsed_ns':elapsed,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'endpoint':'closed-loop capacity at concurrency 20','client_host':CLIENT_HOST}
    if service == 'nginx':
        def number(label):
            match=re.search(r'(?m)^'+re.escape(label)+r':\s*([0-9.]+)',raw)
            assert match,label
            return float(match[1])
        assert number('Complete requests') == n and number('Failed requests') == 0
        non_success=re.search(r'Non-2xx responses:\s*(\d+)',raw)
        assert not non_success or int(non_success[1]) == 0
        result.update(requests=n,rps=number('Requests per second'),kbps=number('Transfer rate'))
        result['p99_ms']=int(re.search(r'(?m)^\s*99%\s+(\d+)',raw)[1])
    else:
        assert len(re.findall(str(n)+r' requests completed in',raw)) == 2
        rates=re.findall(r'throughput summary:\s*([0-9.]+) requests per second',raw)
        assert len(rates) == 2 and all(float(x)>0 for x in rates)
        assert not re.search(r'(?im)^Error:|connection refused|error from server',raw)
        result.update(requests_each=n,set_rps=float(rates[0]),get_rps=float(rates[1]))
    atomic_json(OUT/f'{service}-{mode}.json',result)
    subprocess.run([str(TOOLS/'redis-cli'),'-h',CLIENT_HOST,'-p',str(REDIS_PORT),
                    'SET','fixed-perf-key','abc'],check=True,capture_output=True)
    fixed=asyncio.run(fixed_rate(service,200 if service=='nginx' else 1000,1 if PILOT else 5,OUT/f'{service}-{mode}-fixed.json'))
    atomic_json(OUT/f'{service}-{mode}-fixed.json',fixed)
    print('PERF_HOST_CASE '+json.dumps({**result,'fixed_p99_ns':fixed['p99_ns']}),flush=True)

if __name__ == '__main__':
    main(*sys.argv[1:])
