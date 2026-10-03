#!/usr/bin/python3
"""Expose installed controls in the invoking user's shell, without replacing commands."""
import argparse
import os
from pathlib import Path
import pwd


PATH_BLOCK = '''
# Container infrastructure command PATH
case ":$PATH:" in
    *:"$HOME/.local/bin":*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
'''


def setup(home, runtime):
    commands = home / '.local/bin'
    commands.mkdir(parents=True, exist_ok=True, mode=0o755)
    for name in ('infractl', 'incus', 'sv', 'debianctl', 'tailscale'):
        target = runtime / 'bin' / name
        if not target.is_file() or not os.access(target, os.X_OK):
            raise RuntimeError('Installed executable missing: ' + str(target))
        link = commands / name
        if link.is_symlink() and link.readlink() == target:
            continue
        if link.exists() or link.is_symlink():
            print('Preserved existing command: ' + str(link))
            continue
        link.symlink_to(target)
    profiles = [home / '.profile', home / '.bashrc']
    # Bash reads only the first existing login file in this order.
    for name in ('.bash_profile', '.bash_login'):
        if (home / name).exists():
            profiles.append(home / name)
            break
    for profile in profiles:
        text = profile.read_text() if profile.exists() else ''
        if '# Container infrastructure command PATH' not in text:
            with profile.open('a') as output:
                output.write(PATH_BLOCK)
    print('User commands installed in ' + str(commands))
    print('For this terminal: export PATH="$HOME/.local/bin:$PATH"')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    if not args.runtime.is_absolute() or runtime == Path('/'):
        parser.error('runtime must be a dedicated absolute directory')
    account = pwd.getpwnam(os.environ.get('SUDO_USER') or pwd.getpwuid(os.getuid()).pw_name)
    if os.geteuid() == 0:
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    elif account.pw_uid != os.getuid():
        raise RuntimeError('Cannot configure another user without root')
    os.umask(0o022)
    setup(Path(account.pw_dir), runtime)


if __name__ == '__main__':
    main()
