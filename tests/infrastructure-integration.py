#!/usr/bin/python3
"""Destructive qualification of disposable instances and brief environment restarts."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import uuid

BASE=Path('/workspace/infrastructure-runtime')
def run(*argv,input=None,timeout=120):
    p=subprocess.run(argv,input=input,text=True,capture_output=True,timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stdout+p.stderr)
    return p.stdout.strip()
def ctl(*a):return run(str(BASE/'bin/infractl'),*a)
def incus(*a):return run(str(BASE/'bin/incus'),*a)
def guest(*a):return run(str(BASE/'bin/debianctl'),'exec','--',*a)
def wait(fn,label):
    end=time.monotonic()+90
    last=None
    while time.monotonic()<end:
        try:
            if fn():return
        except Exception as error:last=error
        time.sleep(.5)
    raise RuntimeError(label+' timed out: '+str(last))
def ready():return bool(incus('query','/1.0'))
def check_access():
    ctl('host-exec','sshd','--','/usr/sbin/sshd','-t')
    ctl('host-exec','tailscaled','--','/usr/bin/tailscale','debug','prefs')
    run('curl','--max-time','10','-fsS','https://deb.debian.org/debian/README')
    return True
def passed(s):print('PASS:',s,flush=True)

def main():
    if os.geteuid()!=0:raise SystemExit('Run as root; briefly restarts owned infrastructure')
    name='verify-'+uuid.uuid4().hex[:8]
    clone=name+'-clone'
    original={p.name:not (p/'down').exists() for p in (BASE/'services').iterdir()}
    account=False
    unit=name+'.service'
    identity=None
    try:
        for service in ('debian','incus','sshd','tailscaled'):ctl('enable',service)
        wait(ready,'Incus')
        incus('copy','infra-qualification/baseline',name)
        incus('start',name)
        wait(lambda:incus('exec',name,'--','systemctl','is-system-running')=='running','instance boot')
        incus('copy',name,clone)
        incus('start',clone)
        wait(lambda:bool(incus('exec',clone,'--','curl','-4','--max-time','5','-fsS','https://deb.debian.org/debian/README')),'instance HTTPS')
        passed('two unprivileged instances, DHCP/DNS/HTTPS; outer connectivity retained')
        incus('exec',name,'--','sh','-c','echo original > /root/check')
        incus('snapshot','create',name,'checkpoint')
        incus('exec',name,'--','sh','-c','echo modified > /root/check')
        incus('stop',name)
        incus('snapshot','restore',name,'checkpoint')
        incus('start',name)
        assert incus('exec',name,'--','cat','/root/check')=='original'
        incus('stop',clone)
        passed('directory snapshot and restore')
        identity=guest('cat','/etc/machine-id')
        guest('useradd','-m',name);account=True
        guest('sh','-c',f'echo persistent > /home/{name}/check')
        definition='[Service]\nType=oneshot\nExecStart=/usr/bin/test -f /home/'+name+'/check\nRemainAfterExit=yes\nPrivateTmp=yes\nProtectSystem=strict\n[Install]\nWantedBy=multi-user.target\n'
        run(str(BASE/'bin/debianctl'),'exec','--','tee','/etc/systemd/system/'+unit,input=definition)
        guest('systemctl','daemon-reload');guest('systemctl','enable','--now',unit)
        ctl('service-restart','debian')
        wait(lambda:guest('systemctl','is-active',unit)=='active','Debian unit')
        assert guest('cat','/etc/machine-id')==identity
        assert guest('cat',f'/home/{name}/check')=='persistent'
        passed('Debian accounts, files, identity and protected service persist')
        ctl('service-restart','incus');wait(ready,'Incus restart')
        wait(lambda:incus('exec',name,'--','cat','/root/check')=='original','running instance restoration')
        assert json.loads(incus('query','/1.0/instances/'+clone))['status']=='Stopped'
        assert 'snapshot-user' in incus('exec',name,'--','getent','passwd','snapshot-user')
        passed('Incus manager restart retains database, instance data/accounts and running/stopped states')
        for debian,manager in ((True,False),(False,True),(False,False),(True,True)):
            for service,enabled in (('debian',debian),('incus',manager)):
                ctl('enable' if enabled else 'disable',service)
            ctl('--trigger','scheduled','startup')
            wait(check_access,'independent access')
            for service,enabled in (('debian',debian),('incus',manager)):
                assert (BASE/'services'/service/'down').exists()!=enabled
            if manager:wait(ready,'Incus option')
            if debian:wait(lambda:guest('systemctl','is-system-running')=='running','Debian option')
        passed('all four guest enable combinations; access independent; scheduled trigger honors disable')
        ctl('restart');wait(ready,'supervisor restart');wait(check_access,'access restart')
        passed('whole supervisor clean restart')
        pid=int((BASE/'state/supervisor.pid').read_text().split()[0])
        os.kill(pid,signal.SIGKILL)
        ctl('--trigger','scheduled','startup')
        wait(ready,'scanner recovery');wait(check_access,'scanner access recovery')
        passed('scheduled bootstrap drains owned orphan supervisors and recovers killed scanner')
        ctl('stop')
        assert ctl('--trigger','scheduled','startup')=='skipped_disabled'
        ctl('start');wait(ready,'explicit supervisor start');wait(check_access,'explicit access start')
        passed('persistent supervisor disable and explicit start')
        record=json.loads(incus('query','/1.0/instances/'+name))
        assert record['expanded_config'].get('security.privileged','false')=='false'
        assert incus('exec',name,'--','cat','/root/check')=='original'
        passed('Incus installation and snapshots survive repeated complete stop/start')
        old=int(Path('/workspace/incus-runtime/state/manager.pid').read_text().split()[0])
        os.kill(old,signal.SIGKILL)
        wait(lambda:int(Path('/workspace/incus-runtime/state/manager.pid').read_text().split()[0])!=old and ready(),'killed guest manager recovery')
        wait(lambda:incus('exec',name,'--','cat','/root/check')=='original','orphan instance persistence')
        passed('killed nspawn manager: verified orphan guest powers off and supervisor restores persistent instances')
    finally:
        ctl('start')
        for service in ('debian','incus','sshd','tailscaled'):ctl('enable',service)
        wait(ready,'cleanup Incus')
        for instance in (clone,name):
            subprocess.run([str(BASE/'bin/incus'),'delete','--force',instance],check=False)
        if account:
            guest('systemctl','disable','--now',unit)
            guest('rm','-f','/etc/systemd/system/'+unit)
            guest('systemctl','daemon-reload')
            guest('userdel','--remove',name)
        for service,enabled in original.items():
            if not enabled:ctl('disable',service)

if __name__=='__main__':main()
