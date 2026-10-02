#!/usr/bin/python3
"""Provide fully visible API filesystems in the trusted manager namespace only."""
from pathlib import Path
import os
import subprocess

os.umask(0o077)
base=Path('/run/incus-nesting')
base.mkdir(mode=0o700,exist_ok=True)
base.chmod(0o700)
for name,kind in (('proc','proc'),('sys','sysfs')):
    target=base/name
    target.mkdir(exist_ok=True)
    if not os.path.ismount(target):
        subprocess.run(['/usr/bin/mount','-t',kind,'-o','nosuid,nodev,noexec',kind,str(target)],check=True)
    actual=subprocess.check_output(['/usr/bin/findmnt','-n','-o','FSTYPE','--mountpoint',str(target)],text=True).strip()
    if actual!=kind:
        raise SystemExit('Unexpected nesting mount type: '+str(target))
# Incus owns bridge-specific sysctls in the shared, trusted outer network.
# Preserve the other standard /proc restrictions.
target=Path('/proc/sys/net')
if not os.path.ismount(target):
    subprocess.run(['/usr/bin/mount','--bind',str(base/'proc/sys/net'),str(target)],check=True)
target=Path('/sys/class/net')
if not os.path.ismount(target):
    subprocess.run(['/usr/bin/mount','--bind',str(base/'sys/class/net'),str(target)],check=True)
subprocess.run(['/usr/local/libexec/incus-firewall'],check=True)
