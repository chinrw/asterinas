# SPDX-License-Identifier: MPL-2.0
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import time

class Qmp:
    def __init__(self, path):
        self.connection = socket.socket(socket.AF_UNIX)
        self.connection.settimeout(5)
        self.connection.connect(str(path))
        self.stream = self.connection.makefile("rwb")
        json.loads(self.stream.readline())
        self.identifier = 0
        self.call("qmp_capabilities")
    def call(self, command, arguments=None):
        self.identifier += 1
        request = {"execute": command, "id": self.identifier}
        if arguments is not None:
            request["arguments"] = arguments
        self.stream.write(json.dumps(request).encode() + b"\n")
        self.stream.flush()
        while True:
            reply = json.loads(self.stream.readline())
            if reply.get("id") == self.identifier:
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                return reply["return"]
    def hmp(self, command):
        return self.call("human-monitor-command", {"command-line": command})
    def memory(self, address, size):
        if size > 128:
            return b"".join(self.memory(address + offset, min(128, size - offset)) for offset in range(0, size, 128))
        value = self.hmp(f"x /{size}bx 0x{address:x}")
        data = bytearray()
        for line in value.splitlines():
            if ":" in line:
                data.extend(int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})\b", line.split(":", 1)[1]))
        if len(data) != size:
            raise RuntimeError(f"unreadable guest memory {address:x}: {value[:200]}")
        return bytes(data)
    def number(self, address, size=8):
        return int.from_bytes(self.memory(address, size), "little")
    def close(self):
        self.stream.close()
        self.connection.close()

def fields(data, typename):
    result = {}
    for name, field in layout["structures"][typename]["fields"].items():
        raw = data[field["offset"]:field["offset"] + field["size"]]
        result[name] = int.from_bytes(raw, "little") if len(raw) <= 8 else raw.hex()
    return result

def record(qmp, address, typename):
    size = max(f["offset"] + f["size"] for f in layout["structures"][typename]["fields"].values())
    return fields(qmp.memory(address, size), typename)

def snapshot(qmp):
    identity = layout["identity"]
    if qmp.memory(identity["address"], len(bytes.fromhex(identity["bytes"]))).hex() != identity["bytes"]:
        raise RuntimeError("active address space is not the shared helper")
    addresses = layout["addresses"]
    output = {"registers": qmp.hmp("info registers"), "lapic": qmp.hmp("info lapic")}
    for name in ("runtime.gomaxprocs", "runtime.extraMWaiters", "runtime.extraMLength", "runtime.extraMInUse", "main.blockedForever"):
        if name in addresses:
            output[name] = qmp.number(addresses[name][0], 4)
    output["c_spinning_candidates"] = {hex(x): qmp.number(x, 4) for x in addresses.get("spinning", [])}
    sched_address = addresses["runtime.sched"][0]
    output["sched"] = record(qmp, sched_address, "runtime.schedt")
    output["Ps"] = []
    array = addresses["runtime.allp"][0]
    pointer, count = qmp.number(array), qmp.number(array + 8)
    if count > 64:
        raise RuntimeError(f"invalid P count: {count}")
    for index in range(count):
        address = qmp.number(pointer + 8 * index)
        if address:
            output["Ps"].append({"address": hex(address), **record(qmp, address, "runtime.p")})
    output["Ms"] = []
    address = qmp.number(addresses["runtime.allm"][0])
    seen = set()
    while address and address not in seen and len(seen) < 64:
        seen.add(address)
        row = record(qmp, address, "runtime.m")
        output["Ms"].append({"address": hex(address), **row})
        address = row["alllink"]
    output["system_goroutines"] = []
    for thread in output["Ms"]:
        address = thread["g0"]
        if not address:
            continue
        row = record(qmp, address, "runtime.g")
        low = qmp.number(address)
        high = qmp.number(address + 8)
        entry = {"M": thread["address"], "g0": hex(address), "fields": row, "stack_low": hex(low), "stack_high": hex(high)}
        if low < high and high - low <= 64 * 1024 * 1024:
            entry["stack_chunks"] = []
            begin = max(low, high - 8192) & ~4095
            for page in range(begin, high, 4096):
                start = max(page, low)
                end = min(page + 4096, high)
                chunk = {"address": hex(start)}
                try:
                    chunk["hex"] = qmp.memory(start, end - start).hex()
                except Exception as error:
                    chunk["error"] = str(error)
                entry["stack_chunks"].append(chunk)
        output["system_goroutines"].append(entry)
    registers = output["registers"]
    for register in ("RSP", "RBP"):
        match = re.search(register + r"=([0-9a-fA-F]+)", registers)
        if match and int(match[1], 16) >= 0xffff800000000000:
            address = int(match[1], 16) & ~4095
            try:
                output["kernel_" + register + "_address"] = hex(address)
                output["kernel_" + register + "_hex"] = qmp.memory(address, 4096).hex()
            except Exception as error:
                output["kernel_" + register + "_error"] = str(error)
    output["Gs"] = []
    if "runtime.allgs" in addresses:
        array = addresses["runtime.allgs"][0]
        pointer, count = qmp.number(array), qmp.number(array + 8)
        output["allgs_count"] = count
        for index in range(min(count, 128)):
            address = qmp.number(pointer + index * 8)
            if address:
                output["Gs"].append({"address": hex(address), **record(qmp, address, "runtime.g")})
    return output

suite = Path(__file__).resolve().parent
layout = json.loads((suite / "helper_layout.json").read_text())
def main():
    directory = Path(sys.argv[1])
    repo = suite.parents[3]
    helper = repo / "target/notingo-native-controls/shared-helper"
    assert hashlib.sha256(helper.read_bytes()).hexdigest() == layout["sha256"]
    started = {}
    completed = set()
    captured = set()
    limits = time.monotonic() + 1800
    while time.monotonic() < limits and not (directory / "done").exists():
        text = (directory / "driver.log").read_text(errors="replace") if (directory / "driver.log").exists() else ""
        for kind, mode, iteration in re.findall(r"(?m)^LIVE_(GO|C)_BEGIN (\S+) (\d+)$", text):
            key = kind + "-" + mode + "-" + iteration
            started.setdefault(key, (time.monotonic(), kind, mode))
        for kind, mode, iteration in re.findall(r"(?m)^LIVE_(GO|C)_END (\S+) (\d+)", text):
            completed.add(kind + "-" + mode + "-" + iteration)
        for key, (begin, kind, mode) in list(started.items()):
            if key in completed or key in captured:
                continue
            elapsed = time.monotonic() - begin
            if kind == "C" and elapsed < 60:
                continue
            if kind == "GO" and (elapsed < 2 or mode == "original"):
                continue
            captured.add(key)
            result = {"mode": key, "host_seconds_before_probe": elapsed}
            for attempt in range(8):
                qmp = None
                try:
                    qmp = Qmp(directory / "qmp.sock")
                    qmp.call("stop")
                    if kind == "GO":
                        result["state"] = snapshot(qmp)
                    else:
                        result["state"] = {"registers": qmp.hmp("info registers"), "lapic": qmp.hmp("info lapic")}
                        result["watchdog_quit"] = True
                    break
                except Exception as error:
                    result["last_error"] = str(error)
                finally:
                    if qmp is not None:
                        try:
                            qmp.call("quit" if result.get("watchdog_quit") else "cont")
                        except Exception as error:
                            result["resume_error"] = str(error)
                        qmp.close()
                time.sleep(0.02)
            (directory / (key + ".json")).write_text(json.dumps(result, indent=2) + "\n")
            print("LIVE_SNAPSHOT " + key + " " + ("captured" if "state" in result else "unavailable"), flush=True)
        time.sleep(0.05)


if __name__ == "__main__":
    main()
