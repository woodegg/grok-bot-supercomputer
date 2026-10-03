#!/usr/bin/python3
"""Move the existing access identity to outer runit, with a private rollback copy."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
if os.geteuid()!=0:
    os.execv('/usr/bin/sudo',['sudo','-n','/usr/bin/python3',str(Path(__file__).resolve()),*sys.argv[1:]])
os.umask(0o077)
source=Path('/workspace/debian-rootfs')
target=Path('/workspace/infrastructure-rootfs')
runtime=Path('/workspace/infrastructure-runtime')
backup=runtime/'state/access-migration-backup'
sys.path.insert(0,'/workspace/debian-runtime/libexec')
from containerctl import configuration,guest_run
cfg=configuration(Path('/workspace/debian-runtime/etc/config.toml'))
def guest(*command):
    return guest_run(cfg,list(command),check=True,text=True,capture_output=True,timeout=40).stdout.strip()
def infra(*command):
    return subprocess.run([str(runtime/'bin/infractl'),*command],check=True,text=True,capture_output=True,timeout=65).stdout.strip()
if (runtime/'state/access-migrated').exists():
    print('Access already migrated; not copying stale guest state again');raise SystemExit(0)
if backup.exists() and sys.argv[1:]!=['--retry']:
    raise SystemExit('An unfinished migration backup exists; inspect before retrying')
backup.mkdir(mode=0o700,exist_ok=True)
states=guest('systemctl','is-enabled','ssh.service','tailscaled.service').splitlines()
if states!=['enabled','enabled']:
    raise SystemExit('Existing access services are disabled; preserve them and migrate deliberately')
(backup/'unit-states.txt').write_text('\n'.join(states)+'\n')
try:
    guest('systemctl','disable','--now','ssh.service','tailscaled.service')
    for name in ('ssh_host_ed25519_key','ssh_host_ed25519_key.pub','authorized_keys'):
        src=source/'etc/ssh'/name
        if src.is_dir():
            if not (backup/name).exists():shutil.copytree(src,backup/name)
            shutil.copytree(src,target/'etc/ssh'/name,dirs_exist_ok=True)
        else:
            if not (backup/name).exists():shutil.copy2(src,backup/name)
            shutil.copy2(src,target/'etc/ssh'/name)
    if not (backup/'tailscale').exists():shutil.copytree(source/'var/lib/tailscale',backup/'tailscale')
    shutil.copytree(source/'var/lib/tailscale',target/'var/lib/tailscale',dirs_exist_ok=True)
    (target/'etc/ssh/authorized_keys').chmod(0o755)
    for key in (target/'etc/ssh/authorized_keys').iterdir():key.chmod(0o644)
    (target/'etc/ssh/ssh_host_ed25519_key').chmod(0o600)
    (target/'var/lib/tailscale').chmod(0o700)
    for state in (target/'var/lib/tailscale').iterdir():
        if state.is_file():state.chmod(0o600)
    subprocess.run(['chroot',str(target),'/usr/local/libexec/tailscale-baseline','check'],check=True)
    infra('enable','sshd');infra('enable','tailscaled')
    deadline=time.monotonic()+35
    while True:
        results=[subprocess.run([str(runtime/'bin/infractl'),'host-exec',name,'--',*command],capture_output=True,text=True) for name,command in (('sshd',['/usr/sbin/sshd','-t']),('tailscaled',['/usr/bin/tailscale','debug','prefs']))]
        if all(result.returncode==0 for result in results):break
        result=next(result for result in results if result.returncode)
        if time.monotonic()>deadline:raise RuntimeError('Outer access readiness timed out: '+result.stdout+result.stderr)
        time.sleep(.5)
    (runtime/'state/access-migrated').write_text('SSH host key/authorized keys and Tailscale state migrated to native outer packages\n')
    print('Outer SSH/Tailscale ready; guest copies disabled; identity backup remains private until qualification')
except BaseException:
    for name in ('sshd','tailscaled'):
        try:infra('disable',name)
        except Exception:pass
    guest('systemctl','enable','--now','ssh.service','tailscaled.service')
    raise
