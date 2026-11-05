#!/usr/bin/env python3

# SPDX-License-Identifier: MPL-2.0

"""Run Redis capacity and fixed-arrival GET measurements over virtio-net."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import time


RATE = 1000
DURATION_S = 5
CONNECTIONS = 20
TOTAL = RATE * DURATION_S
GET = b"*2\r\n$3\r\nGET\r\n$14\r\nfixed-perf-key\r\n"
REPLY = b"$3\r\nabc\r\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("condition", choices=("none", "churn"))
    parser.add_argument("cpus", type=int, choices=(1, 4))
    args = parser.parse_args()
    host = os.environ["GUEST_SERVER_IP_ADDRESS"]
    port = int(os.environ.get("REDIS_PORT", "6379"))
    system = os.environ.get("BENCHMARK_SYSTEM", "standalone")
    artifact_root = os.environ.get("BENCHMARK_ARTIFACT_DIR")
    if artifact_root:
        Path(artifact_root).mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(
        prefix=f"redis-timer-{args.condition}-smp{args.cpus}-{system}-",
        dir=artifact_root,
    ))
    result_path = destination / "result.json"
    log_path = destination / "throughput.log"
    log_path.touch()
    result = {
        "schema_version": 1, "system": system, "condition": args.condition,
        "cpus": args.cpus, "host": host, "port": port,
        "status": "failed", "failure": None, "control": [],
        "throughput": {"log_path": str(log_path)},
        "fixed": empty_fixed(), "load": {"start_raw": None}, "shutdown_verified": False,
    }
    control = None
    measurement_failure = None
    cleanup_failure = None

    def interrupted(signum, _frame):
        raise RuntimeError(f"interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        control = wait_ready(host, port, result["control"])
        require(control.command("GET", "timer-bench:condition") == args.condition,
                "guest condition does not match host job")
        start_record = control.command("GET", "timer-bench:load-start")
        result["load"] = {"start_raw": start_record}
        run_throughput(host, port, log_path, result["throughput"])
        asyncio.run(fixed_rate(host, port, result["fixed"]))
    except BaseException as error:
        measurement_failure = describe(error)
    finally:
        if control is not None:
            try:
                finish_guest(control, result, args.condition)
            except Exception as error:
                cleanup_failure = describe(error)
            finally:
                try:
                    # Acknowledge even failed validation so the guest can stop Redis.
                    control.command("RPUSH", "timer-bench:ack", "1")
                    control.sock.settimeout(15)
                    require(control.stream.read(1) == b"", "unexpected data during shutdown")
                    result["shutdown_verified"] = True
                except Exception as error:
                    cleanup_failure = combine(cleanup_failure, describe(error))
                control.close()
        result["fixed"]["summaries"] = summarize(result["fixed"]["records"])
        result["failure"] = combine(measurement_failure, cleanup_failure)
        if result["failure"] is None:
            result["status"] = "success"
        atomic_json(result_path, result)
        print(f"Redis timer results: {result_path}", flush=True)
        print(f"Redis timer throughput log: {log_path}", flush=True)

    if result["failure"]:
        raise RuntimeError(result["failure"])
    print(f"REDIS_TIMER_THROUGHPUT_RPS {result['throughput']['rps']}")
    for metric, summary in result["fixed"]["summaries"].items():
        prefix = f"REDIS_TIMER_{metric.upper()}"
        for name, value in summary.items():
            print(f"{prefix}_{name.upper()} {value}")


def empty_fixed():
    start = time.monotonic_ns() + 100_000_000
    return {
        "offered_rps": RATE, "duration_s": DURATION_S, "connections": CONNECTIONS,
        "planned": TOTAL, "started": 0, "completed": 0, "error_count": TOTAL,
        "failure": "not run", "summaries": {},
        "records": [
            {"id": number, "scheduled_ns": start + number * 1_000_000_000 // RATE,
             "started_ns": None, "finished_ns": None, "connection_id": None,
             "local_port": None, "error": "not started", "response_bytes": None}
            for number in range(TOTAL)
        ],
    }


async def fixed_rate(host, port, result):
    queue = asyncio.Queue()
    writers = []
    workers = []
    result["failure"] = None

    async def worker(connection_id, reader, writer):
        while True:
            record = await queue.get()
            if record is None:
                queue.task_done()
                return
            record.update(started_ns=time.monotonic_ns(), connection_id=connection_id,
                          local_port=writer.get_extra_info("sockname")[1], error=None)
            try:
                async def request():
                    writer.write(GET)
                    await writer.drain()
                    reply = await reader.readexactly(len(REPLY))
                    record["response_bytes"] = len(reply)
                    require(reply == REPLY, f"unexpected GET reply: {reply!r}")
                await asyncio.wait_for(request(), 5)
            except BaseException as error:
                record["error"] = describe(error)
                raise
            finally:
                record["finished_ns"] = time.monotonic_ns()
                queue.task_done()

    async def produce():
        for record in result["records"]:
            delay_s = (record["scheduled_ns"] - time.monotonic_ns()) / 1e9
            await asyncio.sleep(max(0, delay_s))
            queue.put_nowait(record)
        await queue.join()
        for _ in workers:
            queue.put_nowait(None)

    async def open_connections():
        for connection_id in range(CONNECTIONS):
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 3)
            writers.append(writer)
            workers.append(asyncio.create_task(worker(connection_id, reader, writer)))

    producer = None
    try:
        await asyncio.wait_for(open_connections(), 5)
        start = time.monotonic_ns() + 100_000_000
        for record in result["records"]:
            record["scheduled_ns"] = start + record["id"] * 1_000_000_000 // RATE
        producer = asyncio.create_task(produce())
        await asyncio.wait_for(asyncio.gather(producer, *workers), 20)
        require(all(record["error"] is None for record in result["records"]),
                "fixed-rate requests did not all succeed")
    except BaseException as error:
        result["failure"] = describe(error)
        raise
    finally:
        if producer is not None:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
        for task in workers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for writer in writers:
            writer.close()
        for writer in writers:
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except Exception as error:
                result["failure"] = combine(result["failure"], describe(error))
        records = result["records"]
        result["started"] = sum(record["started_ns"] is not None for record in records)
        result["completed"] = sum(record["finished_ns"] is not None for record in records)
        result["error_count"] = sum(record["error"] is not None for record in records)
        result["summaries"] = summarize(records)
    require(result["failure"] is None, result["failure"])


def summarize(records):
    successful = [record for record in records if record["error"] is None]
    errors = len(records) - len(successful)
    differences = {
        "service": ("finished_ns", "started_ns"),
        "client_queue": ("started_ns", "scheduled_ns"),
        "total": ("finished_ns", "scheduled_ns"),
    }
    summaries = {}
    for name, (end, start) in differences.items():
        values = sorted(record[end] - record[start] for record in successful)
        summaries[name] = {
            "p999_ns": values[math.ceil(len(values) * 0.999) - 1] if values else None,
            "max_ns": values[-1] if values else None,
            "error_count": errors,
            "over_10ms_count": sum(value > 10_000_000 for value in values),
        }
    return summaries


def run_throughput(host, port, log_path, result):
    command = ["redis-benchmark", "-h", host, "-p", str(port),
               "-n", "100000", "-c", "20", "-t", "get"]
    started = time.monotonic_ns()
    result.update(command=command, requests=100000, concurrency=CONNECTIONS,
                  exit_code=None, host_elapsed_ns=None)
    with log_path.open("wb") as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
        try:
            process.wait(timeout=90)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            result.update(exit_code=process.returncode,
                          host_elapsed_ns=time.monotonic_ns() - started)
    raw = log_path.read_text(errors="replace")
    require(process.returncode == 0, f"redis-benchmark exited {process.returncode}")
    require(len(re.findall(r"100000 requests completed in", raw)) == 1,
            "missing complete Redis throughput request count")
    require(not re.search(r"(?im)^Error:|connection refused|error from server", raw),
            "Redis throughput client reported an error")
    rates = re.findall(r"throughput summary:\s*([0-9.]+) requests per second", raw)
    require(len(rates) == 1 and float(rates[0]) > 0, "invalid throughput summary")
    result["rps"] = float(rates[0])


def wait_ready(host, port, transcript):
    deadline = time.monotonic() + 30
    last_error = None
    while time.monotonic() < deadline:
        control = None
        try:
            control = RedisControl(host, port, transcript)
            if control.command("GET", "timer-bench:ready") == "1":
                return control
        except (OSError, RuntimeError) as error:
            last_error = error
        if control is not None:
            control.close()
        time.sleep(0.1)
    raise RuntimeError(f"Redis guest readiness timed out: {last_error}")


def finish_guest(control, result, condition):
    control.command("RPUSH", "timer-bench:done", "1")
    control.sock.settimeout(20)
    require(control.command("BLPOP", "timer-bench:completed", "15") ==
            ["timer-bench:completed", "1"], "missing guest completion")
    require(control.command("GET", "timer-bench:status") == "complete",
            "guest status is not complete")
    exit_code = control.command("GET", "timer-bench:load-exit")
    load_log = control.command("GET", "timer-bench:load-log")
    result["load"].update(exit_code=exit_code, log=load_log)
    require(exit_code == "0", f"guest load exited with status {exit_code}")
    if condition == "churn":
        validate_churn(result["load"])
    else:
        require(result["load"]["start_raw"] is None and load_log is None,
                "unexpected explicit timer churn in none condition")


def validate_churn(load):
    start_prefix = "TIMERFD_CHURN_STARTED "
    finish_prefix = "TIMERFD_CHURN_FINISHED "
    require(isinstance(load["start_raw"], str) and load["start_raw"].startswith(start_prefix),
            "missing churn start record")
    start = json.loads(load["start_raw"][len(start_prefix):])
    lines = (load["log"] or "").splitlines()
    require(lines and lines[-1].startswith(finish_prefix), "missing final churn record")
    finish = json.loads(lines[-1][len(finish_prefix):])
    load.update(start=start, finish=finish)
    require(start["pid"] == finish["pid"] and start["pid"] > 0,
            "churn process identity changed")
    require(start["rate"] == finish["rate"] == 5000, "churn target rate is not 5000")
    require(start["start_ns"] == finish["start_ns"] <= finish["finish_ns"],
            "invalid churn timestamps")
    elapsed = finish["finish_ns"] - finish["start_ns"]
    require(elapsed == finish["elapsed_ns"] and elapsed > 0 and finish["cycles"] > 0,
            "invalid churn duration or cycle count")
    require(finish["stop_requested"] is True, "churn stopped before requested teardown")
    require(finish["skipped_slots"] >= 0, "invalid churn skipped-slot count")
    achieved_rate = finish["cycles"] * 1e9 / elapsed
    load["achieved_rate"] = achieved_rate
    require(achieved_rate >= 4500, f"churn rate below 90 percent: {achieved_rate}")


class RedisControl:
    def __init__(self, host, port, transcript):
        self.sock = socket.create_connection((host, port), timeout=3)
        self.stream = self.sock.makefile("rb")
        self.transcript = transcript

    def command(self, *arguments):
        encoded = [argument.encode() for argument in arguments]
        payload = f"*{len(encoded)}\r\n".encode() + b"".join(
            f"${len(argument)}\r\n".encode() + argument + b"\r\n"
            for argument in encoded)
        entry = {"command": list(arguments), "started_ns": time.monotonic_ns()}
        self.transcript.append(entry)
        try:
            self.sock.sendall(payload)
            reply = self.read_reply()
            entry["reply"] = reply
            return reply
        except Exception as error:
            entry["error"] = describe(error)
            raise
        finally:
            entry["finished_ns"] = time.monotonic_ns()

    def read_reply(self):
        line = self.stream.readline(1_048_576)
        require(line.endswith(b"\r\n"), "truncated Redis control reply")
        kind, value = line[:1], line[1:-2]
        if kind == b"-":
            raise RuntimeError(f"Redis control error: {value.decode()}")
        if kind == b"+":
            return value.decode()
        if kind == b":":
            return int(value)
        if kind == b"$":
            length = int(value)
            if length == -1:
                return None
            require(0 <= length <= 1_048_576, "invalid Redis bulk reply length")
            body = self.stream.read(length + 2)
            require(len(body) == length + 2 and body[-2:] == b"\r\n",
                    "truncated Redis bulk reply")
            return body[:-2].decode()
        if kind == b"*":
            count = int(value)
            if count == -1:
                return None
            require(0 <= count <= 16, "invalid Redis array reply length")
            return [self.read_reply() for _ in range(count)]
        raise RuntimeError(f"unknown Redis reply type: {kind!r}")

    def close(self):
        self.stream.close()
        self.sock.close()


def atomic_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def describe(error):
    return f"{type(error).__name__}: {error}"


def combine(first, second):
    return "; ".join(value for value in (first, second) if value) or None


if __name__ == "__main__":
    main()
