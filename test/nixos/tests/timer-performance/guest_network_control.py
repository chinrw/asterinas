"""Settle the copied Linux guest's intended static IPv4 configuration."""
import json
import os
from pathlib import Path
import subprocess
import sys

assert os.uname().release == '6.18.39'
mode=sys.argv[1]
assert mode in {'setup','check'}
def output(*command):
    return subprocess.check_output(command,text=True).strip()
def state():
    return {'addresses':json.loads(output('ip','-j','address','show','dev','eth0')),
        'routes':json.loads(output('ip','-j','-4','route','show')),
        'dhcp':output('systemctl','show','dhcpcd.service','-p','ActiveState','--value'),
        'systemd_log_level':output('systemctl','log-level'),
        'kernel_console_log_level':int(Path('/proc/sys/kernel/printk').read_text().split()[0]),
        'ipv6_disabled':Path('/proc/sys/net/ipv6/conf/eth0/disable_ipv6').read_text().strip()}
before=state()
if mode=='setup':
    subprocess.run(['systemctl','stop','dhcpcd.service'],check=True)
    # Asterinas configures this NIC with IPv4 only; leave loopback IPv6 intact.
    Path('/proc/sys/net/ipv6/conf/eth0/disable_ipv6').write_text('1\n')
    Path('/proc/sys/kernel/printk').write_text('3\n')
    subprocess.run(['ip','link','set','eth0','up'],check=True)
    subprocess.run(['ip','-4','addr','replace','10.0.2.15/24','dev','eth0'],check=True)
    subprocess.run(['ip','-4','route','replace','default','via','10.0.2.2','dev','eth0'],check=True)
after=state()
assert after['dhcp']=='inactive' and after['systemd_log_level']=='crit'
assert after['kernel_console_log_level']==3 and after['ipv6_disabled']=='1'
addresses=after['addresses'][0]['addr_info']
assert any(a['family']=='inet' and a['local']=='10.0.2.15' and a['prefixlen']==24 for a in addresses)
assert not any(a['family']=='inet6' for a in addresses)
assert any(r.get('dst')=='default' and r.get('gateway')=='10.0.2.2' and r.get('dev')=='eth0' for r in after['routes'])
print('LINUX_CONTROL_NETWORK '+json.dumps({'mode':mode,'before':before,'after':after}),flush=True)
