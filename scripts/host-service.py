#!/usr/bin/python3
"""Run native host-tool daemons in a private mount namespace, sharing outer PID/net."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tempfile

root, runtime, name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
os.umask(0o077)
if name == '_tailscale':
    command=['/usr/sbin/tailscaled','--tun=tailscale0',
             '--state=/var/lib/tailscale/tailscaled.state','--statedir=/var/lib/tailscale',
             '--socket=/run/tailscale/tailscaled.sock','--socks5-server=127.0.0.1:1055']
    subprocess.run(['/usr/local/libexec/tailscale-baseline','check'],check=True)
    Path('/run/tailscale').mkdir(mode=0o700,parents=True,exist_ok=True)
    os.environ['TS_DEBUG_FIREWALL_MODE']='nftables'
    daemon=subprocess.Popen(command)
    apply=subprocess.Popen(['/usr/local/libexec/tailscale-baseline','apply'])
    stopping=False
    def terminate(signum,frame):
        global stopping
        stopping=True
        if daemon.poll() is None:
            daemon.terminate()
        if apply.poll() is None:
            apply.terminate()
    signal.signal(signal.SIGTERM,terminate)
    signal.signal(signal.SIGINT,terminate)
    try:
        while daemon.poll() is None:
            if apply.poll() not in (None,0):
                raise RuntimeError('Routing baseline enforcement failed')
            time.sleep(.2)
        if not stopping and daemon.returncode:
            raise RuntimeError('tailscaled exited with '+str(daemon.returncode))
    finally:
        terminate(None,None)
        daemon.wait(timeout=25)
        apply.wait(timeout=5)
        subprocess.run(['/usr/sbin/tailscaled','--cleanup'],timeout=15,check=True)
    raise SystemExit(0)

if name not in ('sshd','tailscaled'):
    raise SystemExit('Unknown host service')
# Recreated platforms can supply a different resolver. Copy into our own tree,
# never modify the platform's resolver, and replace atomically across launches.
with tempfile.NamedTemporaryFile(dir=root/'etc',delete=False) as output:
    resolver=Path(output.name)
    output.write(Path('/etc/resolv.conf').read_bytes())
resolver.chmod(0o644)
resolver.replace(root/'etc/resolv.conf')
sys.path.insert(0,str(runtime/'libexec'))
from lifecycle_events import process_ticks
record=runtime/'state'/(name+'.pid')
record.write_text(f'{os.getpid()} {process_ticks(os.getpid())}\n')
os.unshare(os.CLONE_NEWNS)
subprocess.run(['/usr/bin/mount','--make-rslave','/'],check=True)
# Keep controls at the same absolute location inside the access chroot.
base = root.parent
for source,target in (('/proc','proc'),('/sys','sys'),('/dev','dev'),
                      (str(base), str(base.relative_to('/')))):
    destination=root/target
    destination.mkdir(parents=True,exist_ok=True)
    subprocess.run(['/usr/bin/mount','--rbind',source,str(destination)],check=True)
    subprocess.run(['/usr/bin/mount','--make-rslave',str(destination)],check=True)
subprocess.run(['/usr/bin/mount','-t','tmpfs','-o','mode=755,nosuid,nodev','tmpfs',str(root/'run')],check=True)
os.chroot(root)
os.chdir('/')
if name == 'sshd':
    Path('/run/sshd').mkdir(mode=0o755)
    subprocess.run(['/usr/sbin/sshd','-t'],check=True)
    os.execv('/usr/sbin/sshd',['/usr/sbin/sshd','-D','-e'])
else:
    os.execv('/usr/bin/python3',['python3',str(runtime/'libexec/host-service.py'),str(root),str(runtime),'_tailscale'])
