#!/usr/bin/python3
"""Explicit, repeatable Incus dir/bridge setup; never called by bootstrap."""
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

CLI='/workspace/infrastructure-runtime/bin/incus'
RUNTIME=Path('/workspace/infrastructure-runtime')
NFT=Path('/workspace/infrastructure-rootfs/usr/sbin/nft')
LOADER=Path('/workspace/infrastructure-rootfs/lib64/ld-linux-x86-64.so.2')
LIB='/workspace/infrastructure-rootfs/usr/lib/x86_64-linux-gnu'

def incus(*args):
    return subprocess.check_output([CLI,*args],text=True).strip()

def nft(*args):
    return subprocess.check_output([str(LOADER),'--library-path',LIB,str(NFT),*args],text=True)

def tables():
    return {(x['table']['family'],x['table']['name']) for x in json.loads(nft('-j','list','tables'))['nftables'] if 'table' in x}

def probe_hosts():
    """Optional private management endpoints belong in local configuration."""
    hosts = tuple(value.strip() for value in
                  os.environ.get('INFRA_PROBE_HOSTS', 'deb.debian.org').split(','))
    if not all(hosts):
        raise ValueError('INFRA_PROBE_HOSTS must contain nonempty hostnames or IPs')
    return hosts

def healthy(baseline, hosts):
    try:
        if subprocess.check_output(['/usr/sbin/ip','-j','route','show','default'],text=True)!=baseline:
            return False
        socket.setdefaulttimeout(5)
        socket.getaddrinfo('deb.debian.org',443)
        for address in hosts:
            with socket.create_connection((address,443),timeout=5):
                pass
        return True
    except (OSError,subprocess.SubprocessError):
        return False

def guard(specfile):
    spec=json.loads(specfile.read_text())
    commit=specfile.with_suffix('.committed')
    bad=0
    deadline=time.monotonic()+180
    while time.monotonic()<deadline and not commit.exists():
        bad=0 if healthy(spec['default'], spec['probe_hosts']) else bad+1
        if bad>=3:
            break
        time.sleep(2)
    if commit.exists():
        print('Network guard committed',flush=True)
        return
    print('Network guard rolling back only newly owned Incus bridge/table',flush=True)
    # Independent of the Incus guest/API. Never flush an existing platform table.
    if spec['new_bridge']:
        subprocess.run(['/usr/sbin/ip','link','delete','incusbr0'],check=False)
    legacy='/workspace/infrastructure-rootfs/usr/sbin/iptables-legacy'
    environment=dict(os.environ,XTABLES_LIBDIR='/workspace/infrastructure-rootfs/usr/lib/x86_64-linux-gnu/xtables')
    for rule in spec['new_rules']:
        subprocess.run([str(LOADER),'--library-path',LIB,legacy,'-w','5','-D','FORWARD',*rule],env=environment,check=False)
    previous={tuple(row) for row in spec['tables']}
    for family,name in tables()-previous:
        if name=='incus':
            nft('delete','table',family,name)
    subprocess.run([str(RUNTIME/'bin/infractl'),'disable','incus'],timeout=70,check=False)

def main():
    if os.geteuid()!=0:
        os.execv('/usr/bin/sudo',['sudo','-n','/usr/bin/env',
            'INFRA_PROBE_HOSTS='+os.environ.get('INFRA_PROBE_HOSTS','deb.debian.org'),sys.executable,str(Path(__file__).resolve()),*sys.argv[1:]])
    os.umask(0o077)
    if sys.argv[1:2]==['--guard']:
        guard(Path(sys.argv[2]));return
    hosts=probe_hosts()
    pools=json.loads(incus('storage','list','--format=json'))
    local=next((p for p in pools if p['name']=='local'),None)
    if local and local['driver']!='dir':
        raise SystemExit('Existing local pool is not dir; left untouched')
    if not local:
        incus('storage','create','local','dir')
    profile=json.loads(incus('query','/1.0/profiles/default'))
    disk=profile['devices'].get('root')
    if disk and disk!={'type':'disk','path':'/','pool':'local'}:
        raise SystemExit('Existing default root device differs; left untouched')
    if not disk:
        incus('profile','device','add','default','root','disk','path=/','pool=local')
    nic=profile['devices'].get('eth0')
    if nic and nic!={'type':'nic','network':'incusbr0','name':'eth0'}:
        raise SystemExit('Existing default eth0 differs; left untouched')
    networks=json.loads(incus('network','list','--format=json'))
    bridge=next((n for n in networks if n['name']=='incusbr0'),None)
    expected={'ipv4.address':'10.88.0.1/24','ipv4.nat':'true','ipv6.address':'none'}
    if bridge:
        if not bridge['managed'] or any(bridge['config'].get(k)!=v for k,v in expected.items()):
            raise SystemExit('Existing incusbr0 differs; left untouched')
    else:
        subnet=ipaddress.ip_network('10.88.0.0/24')
        for row in json.loads(subprocess.check_output(['/usr/sbin/ip','-j','route'],text=True)):
            if row['dst']!='default' and ipaddress.ip_network(row['dst'],strict=False).overlaps(subnet):
                raise SystemExit('Incus bridge subnet conflicts with '+row['dst'])
    baseline=subprocess.check_output(['/usr/sbin/ip','-j','route','show','default'],text=True)
    if not healthy(baseline, hosts):
        raise SystemExit('Platform connectivity baseline failed; no network change')
    interface=json.loads(baseline)[0]['dev']
    candidates=[
        ['-i','incusbr0','-o',interface,'-s','10.88.0.0/24','-m','comment','--comment','infra-incus-egress','-j','ACCEPT'],
        ['-i',interface,'-o','incusbr0','-d','10.88.0.0/24','-m','conntrack','--ctstate','RELATED,ESTABLISHED','-m','comment','--comment','infra-incus-return','-j','ACCEPT'],
    ]
    legacy='/workspace/infrastructure-rootfs/usr/sbin/iptables-legacy'
    environment=dict(os.environ,XTABLES_LIBDIR='/workspace/infrastructure-rootfs/usr/lib/x86_64-linux-gnu/xtables')
    new_rules=[r for r in candidates if subprocess.run([str(LOADER),'--library-path',LIB,legacy,'-w','5','-C','FORWARD',*r],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode]
    spec=RUNTIME/'state'/('network-guard-'+uuid.uuid4().hex+'.json')
    spec.write_text(json.dumps({'default':baseline,'probe_hosts':hosts,'tables':sorted(tables()),'new_bridge':not bool(bridge),'new_rules':new_rules}))
    log_path=RUNTIME/'state/network-guard.log'
    if log_path.exists() and log_path.stat().st_size>1024*1024:
        log_path.replace(log_path.with_suffix('.log.1'))
    with log_path.open('a') as log:
        subprocess.Popen([sys.executable,str(RUNTIME/'libexec/initialize-incus.py'),'--guard',str(spec)],
            start_new_session=True,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
    if not bridge:
        incus('network','create','incusbr0',*[k+'='+v for k,v in expected.items()])
    subprocess.run(['/workspace/incus-runtime/bin/debianctl','exec','--','/usr/local/libexec/incus-firewall'],check=True)
    if not healthy(baseline, hosts):
        raise SystemExit('Connectivity changed; independent guard will roll back')
    if not nic:
        incus('profile','device','add','default','eth0','nic','network=incusbr0','name=eth0')
    spec.with_suffix('.committed').touch(mode=0o600)
    print('Persistent dir pool and isolated NAT bridge configured; existing instances retained')

if __name__=='__main__':
    main()
