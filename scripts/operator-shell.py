#!/usr/bin/python3
"""Enter the tools operator account in a private mount namespace without SSHD."""
import os
from pathlib import Path
import subprocess
import sys

def main():
    if os.geteuid() != 0:
        raise SystemExit('Use the image-aware operator wrapper with sudo support')
    root = Path('/workspace/infrastructure-rootfs')
    os.unshare(os.CLONE_NEWNS)
    subprocess.run(['mount', '--make-rslave', '/'], check=True)
    # Make host controls usable from the shell, matching the access environment.
    for source, target in (('/proc', 'proc'), ('/sys', 'sys'), ('/dev', 'dev'),
                           ('/workspace', 'workspace')):
        destination = root / target
        destination.mkdir(parents=True, exist_ok=True)
        subprocess.run(['mount', '--rbind', source, str(destination)], check=True)
        subprocess.run(['mount', '--make-rslave', str(destination)], check=True)
    os.chroot(root)
    os.chdir('/')
    terminal = os.environ.get('TERM', 'xterm')
    os.environ.clear()
    os.environ.update(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', TERM=terminal)
    os.execv('/usr/sbin/runuser', ['runuser', '--login', 'operator', *sys.argv[1:]])

if __name__ == '__main__':
    main()
