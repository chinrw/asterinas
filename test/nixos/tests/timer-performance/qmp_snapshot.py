# SPDX-License-Identifier: MPL-2.0
"""Read a bounded, explicitly intrusive snapshot from the owned guest's QMP socket."""
import json
import re
import socket
import time


class Qmp:
    def __init__(self, path):
        self.path=str(path)
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(str(path))
        self.file=self.sock.makefile('rb')
        self.serial=0
        greeting=json.loads(self.file.readline())
        assert 'QMP' in greeting
        self.call('qmp_capabilities')

    def call(self,command,args=None):
        self.serial+=1
        request={'execute':command,'id':self.serial}
        if args is not None:request['arguments']=args
        self.sock.sendall(json.dumps(request).encode()+b'\n')
        while True:
            line=self.file.readline()
            if not line:raise RuntimeError('QMP disconnected')
            reply=json.loads(line)
            if reply.get('id')!=self.serial:continue
            if 'error' in reply:raise RuntimeError(f'QMP {command}: {reply["error"]}')
            return reply['return']

    def close(self):
        self.file.close();self.sock.close()


def snapshot(qmp, cpus):
    result={'intrusive':True,'stop_requested_ns':time.monotonic_ns(),'cpus':[]}
    deadline=time.monotonic()+.1
    qmp.sock.settimeout(.25)
    try:
        qmp.call('stop')
        assert qmp.call('query-status')['running'] is False
        result['stopped_ns']=time.monotonic_ns()
        for cpu in cpus:
            if time.monotonic()>deadline:
                result['budget_exhausted']=True
                break
            index=cpu['cpu-index']
            before=time.monotonic_ns()
            regs=qmp.call('human-monitor-command',{'command-line':'info registers','cpu-index':index})
            general='\n'.join(line for line in regs.splitlines() if re.match(r'^(?:R(?:AX|BX|CX|DX|SI|DI|SP|BP|IP|FL|\d+)|E(?:AX|BX|CX|DX|SI|DI|SP|BP|IP|FL)|CR\d|EFER|CPL)',line.strip()))
            row={'cpu_index':index,'thread_id':cpu['thread-id'],'registers':general,'register_read_start_ns':before,
                 'register_read_end_ns':time.monotonic_ns()}
            stack=re.search(r'\bRSP=([0-9a-fA-F]+)',regs)
            ip=re.search(r'\bRIP=([0-9a-fA-F]+)',regs)
            if stack and ip and int(stack[1],16)&(1<<63) and int(ip[1],16)&(1<<63):
                row['stack_words']=qmp.call('human-monitor-command',{'command-line':f'x /24gx 0x{stack[1]}','cpu-index':index})
            result['cpus'].append(row)
    except Exception as error:
        result['error']=f'{type(error).__name__}: {error}'
    finally:
        try:
            qmp.call('cont')
        except Exception:
            # A timed-out socket reader cannot be reused. Resume through a new
            # connection so a diagnostic read does not leave the guest paused.
            qmp.close()
            recovery=Qmp(qmp.path)
            try:recovery.call('cont')
            finally:recovery.close()
            result['resume_reconnected']=True
        result['resumed_ns']=time.monotonic_ns()
    return result
