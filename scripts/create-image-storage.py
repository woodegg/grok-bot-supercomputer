#!/usr/bin/python3
"""Explicitly create a new image layout. Never run this from recurring startup."""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

PLAN = [
    ('debian-system', 768, 'rootfs', '/workspace/debian-rootfs'),
    ('tools-system', 1024, 'rootfs', '/workspace/infrastructure-rootfs'),
    ('incus-system', 1536, 'rootfs', '/workspace/incus-rootfs'),
    ('debian-runtime', 64, 'runtime', '/workspace/debian-runtime'),
    ('infrastructure-runtime', 128, 'runtime', '/workspace/infrastructure-runtime'),
    ('incus-runtime', 64, 'runtime', '/workspace/incus-runtime'),
    ('incus-data', 512, 'data', '/workspace/incus-rootfs/var/lib/incus'),
    ('incus-pool', 2048, 'pool', '/workspace/incus-rootfs/srv/incus-pool'),
]

def create_image(directory, name, mib, payload, target, account):
    image = directory / (name + '.ext4')
    with image.open('xb') as stream:
        stream.truncate(mib * 1024 * 1024)
    image.chmod(0o600)
    os.chown(image, account.pw_uid, account.pw_gid)
    formatter = shutil.which('mkfs.ext4')
    if formatter:
        command = [formatter]
    else:
        root = Path('/workspace/infrastructure-rootfs')
        command = [str(root / 'lib64/ld-linux-x86-64.so.2'), '--library-path',
                   str(root / 'usr/lib/x86_64-linux-gnu'), str(root / 'usr/sbin/mkfs.ext4')]
    subprocess.run(command + ['-q', '-F', '-m', '0', '-L', name[:16], str(image)], check=True)
    uuid = subprocess.check_output(['blkid', '-p', '-s', 'UUID', '-o', 'value', str(image)], text=True).strip()
    mounted = directory / 'mounts' / name
    mounted.mkdir(parents=True)
    os.chown(mounted, account.pw_uid, account.pw_gid)
    subprocess.run(['mount', '-o', 'loop', str(image), str(mounted)], check=True)
    try:
        (mounted / payload).mkdir()
    finally:
        subprocess.run(['umount', str(mounted)], check=True)
    return dict(name=name, file=image.name, size_mib=mib, uuid=uuid, payload=payload, target=target)

def entrypoints(directory, account):
    for command in ('infractl', 'incus', 'debianctl', 'tailscale', 'sv', 'layerctl', 'backupctl', 'operator'):
        path = directory / command
        invocation = '/workspace/infra-images/' + command + '.py' if command in ('layerctl', 'backupctl') else '/workspace/infra-images/image-storage.py dispatch ' + command
        path.write_text('#!/bin/sh\nexec /usr/bin/python3 ' + invocation + ' "$@"\n')
        path.chmod(0o755)
        os.chown(path, account.pw_uid, account.pw_gid)
        link = Path(account.pw_dir) / '.local/bin' / command
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() and link.readlink() in (Path('/workspace/infrastructure-runtime/bin') / command, path):
            link.unlink()
        if not link.exists() and not link.is_symlink():
            link.symlink_to(path)
            os.lchown(link, account.pw_uid, account.pw_gid)
        else:
            print('Preserved unrelated command:', link)
    profiles = [Path(account.pw_dir) / '.profile', Path(account.pw_dir) / '.bashrc']
    for filename in ('.bash_profile', '.bash_login'):
        profile = Path(account.pw_dir) / filename
        if profile.exists():
            profiles.append(profile)
            break
    block = '\n# Container infrastructure image command PATH\ncase "$PATH" in\n    "$HOME/.local/bin"|"$HOME/.local/bin":*) ;;\n    *) export PATH="$HOME/.local/bin:$PATH" ;;\nesac\n'
    for profile in profiles:
        if '# Container infrastructure image command PATH' not in (profile.read_text() if profile.exists() else ''):
            with profile.open('a') as output:
                output.write(block)
            os.chown(profile, account.pw_uid, account.pw_gid)
    for name, text in (
        ('startup.sh', '#!/bin/sh\nexec /workspace/infra-images/infractl --trigger bootstrap "$@" startup\n'),
        ('shutdown.sh', '#!/bin/sh\nexec /usr/bin/python3 /workspace/infra-images/image-storage.py shutdown\n'),
    ):
        path = directory / name
        path.write_text(text)
        path.chmod(0o755)
        os.chown(path, account.pw_uid, account.pw_gid)
    helper = directory / 'image-storage.py'
    source = Path(__file__).with_name('image-storage.py')
    if source.resolve() != helper.resolve():
        shutil.copyfile(source, helper)
    helper.chmod(0o755)
    os.chown(helper, account.pw_uid, account.pw_gid)
    for filename in ('create-image-storage.py', 'layerctl.py', 'backupctl.py', 'survivability.py', 'operator-shell.py'):
        target = directory / filename
        source = Path(__file__).with_name(filename)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        target.chmod(0o755)
        os.chown(target, account.pw_uid, account.pw_gid)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'entrypoints'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        os.execvp('sudo', ['sudo', '-n', sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    if not os.path.samefile('/', '/proc/1/root'):
        os.execvp('nsenter', ['nsenter', '--target', '1', '--mount', '--root', '--wd',
                             sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    account = pwd.getpwnam('box')
    directory = Path('/workspace/infra-images')
    if args.action == 'create':
        if directory.exists() or any(Path(target).exists() for _, _, _, target in PLAN[:6]):
            raise RuntimeError('Existing paths: inspect and preserve them before creating a fresh installation')
        if shutil.disk_usage(directory.parent).free < sum(item[1] for item in PLAN) * 1024 * 1024:
            raise RuntimeError('Insufficient space for the full image capacities')
        directory.mkdir(mode=0o700)
        os.chown(directory, account.pw_uid, account.pw_gid)
        images = [create_image(directory, *item, account) for item in PLAN]
        manifest = directory / 'manifest.json'
        manifest.write_text(json.dumps(dict(version=1, owner_uid=account.pw_uid, build_complete=False, images=images), indent=2) + '\n')
        os.chown(manifest, account.pw_uid, account.pw_gid)
        for _, _, _, target in PLAN[:6]:
            path = Path(target)
            path.mkdir()
            os.chown(path, account.pw_uid, account.pw_gid)
            startup = path / 'startup.sh' if target.endswith('-runtime') else None
            if startup:
                startup.write_text('#!/bin/sh\nexec /workspace/infra-images/startup.sh "$@"\n')
                startup.chmod(0o755)
                os.chown(startup, account.pw_uid, account.pw_gid)
    entrypoints(directory, account)

if __name__ == '__main__':
    main()
