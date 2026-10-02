#!/usr/bin/python3
"""Allow only this Incus bridge through a pre-existing legacy FORWARD policy."""
import json
from pathlib import Path
import subprocess
import sys

IPTABLES='/usr/sbin/iptables-legacy'
remove=sys.argv[1:]==['remove']
record=Path('/run/incus-nesting/firewall-interface')
if remove:
    if not record.exists():
        raise SystemExit(0)
    interface=record.read_text().strip()
else:
    if not Path('/var/lib/incus/networks/incusbr0').is_dir():
        raise SystemExit(0)
    routes=json.loads(subprocess.check_output(['/usr/sbin/ip','-j','route','show','default'],text=True))
    if len(routes)!=1 or routes[0]['dev']=='incusbr0':
        raise SystemExit('Expected one physical default route; no firewall change')
    interface=routes[0]['dev']
    record.write_text(interface+'\n')
rules=(
    ['-i','incusbr0','-o',interface,'-s','10.88.0.0/24','-m','comment','--comment','infra-incus-egress','-j','ACCEPT'],
    ['-i',interface,'-o','incusbr0','-d','10.88.0.0/24','-m','conntrack','--ctstate','RELATED,ESTABLISHED','-m','comment','--comment','infra-incus-return','-j','ACCEPT'],
)
for rule in rules:
    exists=subprocess.run([IPTABLES,'-w','5','-C','FORWARD',*rule],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    if remove and exists:
        subprocess.run([IPTABLES,'-w','5','-D','FORWARD',*rule],check=True)
    elif not remove and not exists:
        subprocess.run([IPTABLES,'-w','5','-I','FORWARD','1',*rule],check=True)
if remove:
    record.unlink(missing_ok=True)
