#!/usr/bin/python3
"""Install root-owned outer controls; preserve existing config and down markers."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

if os.geteuid()!=0:
    os.execv('/usr/bin/sudo',['sudo','-n','/usr/bin/python3',str(Path(__file__).resolve()),*sys.argv[1:]])
os.umask(0o077)
project=Path(__file__).resolve().parent.parent
runtime=Path('/srv/container-infrastructure/infrastructure-runtime')
root=Path('/srv/container-infrastructure/infrastructure-rootfs')
incus_runtime=Path('/srv/container-infrastructure/incus-runtime')
if not (root/'etc/container-infrastructure-host-tools').is_file() or not Path('/srv/container-infrastructure/incus-rootfs/etc/container-infrastructure-incus').is_file():
    raise SystemExit('Provision native host-tools and Incus filesystems first')
incus_root=Path('/srv/container-infrastructure/incus-rootfs')
for source,destination,mode in (
    ('scripts/incus-nesting.py','usr/local/libexec/incus-nesting',0o755),
    ('scripts/incus-firewall.py','usr/local/libexec/incus-firewall',0o755),
    ('templates/incus-nesting.conf','etc/systemd/system/incus.service.d/nesting.conf',0o644),
):
    target=incus_root/destination
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as output:
        temporary=Path(output.name)
    try:
        shutil.copyfile(project/source,temporary)
        temporary.chmod(mode);os.chown(temporary,0,0)
        temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)
incus_exists=(incus_runtime/'etc/config.toml').exists()
subprocess.run(['/usr/bin/env','RUNTIME_DIR='+str(incus_runtime),str(project/'scripts/install-controls.sh')],check=True)
incus_config=incus_runtime/'etc/config.toml'
if not incus_exists:
    incus_config.write_text('rootfs = "/srv/container-infrastructure/incus-rootfs"\nmachine = "incus-manager"\nstate_dir = "/srv/container-infrastructure/incus-runtime/state"\nprivate_users = "no"\nallow_tun = false\nallow_nesting = true\n')
    incus_config.chmod(0o600)
for path,mode in ((runtime,0o755),(runtime/'bin',0o755),(runtime/'libexec',0o755),(runtime/'etc',0o700),(runtime/'state',0o700),(runtime/'services',0o755)):
    path.mkdir(parents=True,exist_ok=True);path.chmod(mode);os.chown(path,0,0)
def write(path,text,mode):
    path.write_text(text);path.chmod(mode);os.chown(path,0,0)
for name in ('lifecycle_events.py','infrastructurectl.py','host-service.py','environment-service.py','initialize-incus.py'):
    with tempfile.NamedTemporaryFile(dir=runtime/'libexec',delete=False) as out:
        temp=Path(out.name)
    try:
        shutil.copyfile(project/'scripts'/name,temp);temp.chmod(0o755);os.chown(temp,0,0)
        temp.replace(runtime/'libexec'/name)
    finally:temp.unlink(missing_ok=True)
config=runtime/'etc/config.toml'
if not config.exists():
    write(config,'''runtime = "/srv/container-infrastructure/infrastructure-runtime"
host_rootfs = "/srv/container-infrastructure/infrastructure-rootfs"
[environments.debian]
config = "/srv/container-infrastructure/debian-runtime/etc/config.toml"
controller_dir = "/srv/container-infrastructure/debian-runtime/libexec"
[environments.incus]
config = "/srv/container-infrastructure/incus-runtime/etc/config.toml"
controller_dir = "/srv/container-infrastructure/incus-runtime/libexec"
''',0o600)
quote=lambda p:shlex.quote(str(p))
for command in ('runsv','sv','svlogd'):
    preface='if [ "$(id -u)" != 0 ]; then exec sudo -n "$0" "$@"; fi\nexport SVDIR='+quote(runtime/'services')+'\n' if command=='sv' else ''
    write(runtime/'bin'/command,'#!/bin/sh\n'+preface+'exec '+quote(root/'lib64/ld-linux-x86-64.so.2')+' --library-path '+quote(root/'usr/lib/x86_64-linux-gnu')+' '+quote(root/'usr/bin'/command)+' "$@"\n',0o755)
write(runtime/'bin/infractl','#!/bin/sh\nexec /usr/bin/python3 '+quote(runtime/'libexec/infrastructurectl.py')+' "$@"\n',0o755)
write(runtime/'startup.sh','#!/bin/sh\nexec '+quote(runtime/'bin/infractl')+' --trigger bootstrap "$@" startup\n',0o755)
# Both wrappers work even from an outer SSH chroot by passing through infractl.
write(runtime/'bin/tailscale','#!/bin/sh\nexec '+quote(runtime/'bin/infractl')+' host-exec tailscaled -- /usr/bin/tailscale "$@"\n',0o755)
write(runtime/'bin/incus','#!/bin/sh\nif [ "$(id -u)" != 0 ]; then exec sudo -n "$0" "$@"; fi\nexec /usr/bin/nsenter --target 1 --mount --root --wd /srv/container-infrastructure/incus-runtime/bin/debianctl exec -- /usr/bin/incus "$@"\n',0o755)
write(runtime/'bin/debianctl','#!/bin/sh\nif [ "$(id -u)" != 0 ]; then exec sudo -n "$0" "$@"; fi\nexec /usr/bin/nsenter --target 1 --mount --root --wd /srv/container-infrastructure/debian-runtime/bin/debianctl "$@"\n',0o755)
for name in ('debian','incus','sshd','tailscaled'):
    service=runtime/'services'/name
    new=not service.exists()
    service.mkdir(exist_ok=True);service.chmod(0o755)
    logdir=runtime/'state/logs'/name
    logdir.mkdir(parents=True,exist_ok=True,mode=0o700)
    write(logdir/'config','s1000000\nn10\nt86400\n',0o600)
    (service/'log').mkdir(exist_ok=True)
    write(service/'log/run','#!/bin/sh\nexec '+quote(runtime/'bin/svlogd')+' -tt '+quote(logdir)+'\n',0o755)
    if name in ('debian','incus'):
        env_runtime=Path('/srv/container-infrastructure/debian-runtime') if name=='debian' else incus_runtime
        argv=['/usr/bin/python3',str(runtime/'libexec/environment-service.py'),str(env_runtime/'etc/config.toml'),str(env_runtime/'libexec'),str(service),str(runtime/'bin/sv')]
        check='exec '+quote(env_runtime/'bin/debianctl')+' status >/dev/null 2>&1'
        if new and (name=='incus' or (env_runtime/'state/disabled').exists()):
            (service/'down').touch(mode=0o600)
    else:
        argv=['/usr/bin/python3',str(runtime/'libexec/host-service.py'),str(root),str(runtime),name]
        check='exec '+quote(runtime/'bin/infractl')+' host-exec '+name+' -- '+('/usr/sbin/sshd -t' if name=='sshd' else '/usr/bin/tailscale debug prefs')+' >/dev/null 2>&1'
        if new:(service/'down').touch(mode=0o600)
    write(service/'run','#!/bin/sh\nexec 2>&1\nexec '+shlex.join(argv)+'\n',0o755)
    write(service/'check','#!/bin/sh\n'+check+'\n',0o755)
write(root/'etc/profile.d/container-infrastructure.sh','export PATH=/srv/container-infrastructure/infrastructure-runtime/bin:$PATH\n',0o644)
write(root/'etc/sudoers.d/container-infrastructure-path','Defaults secure_path="/srv/container-infrastructure/infrastructure-runtime/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"\n',0o440)
print('Outer controls installed. Current guest/access state untouched. Use infractl start when ready.')
