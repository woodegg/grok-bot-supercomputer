#!/usr/bin/python3
"""Mount explicitly created box-owned ext4 images; never format during startup."""
import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import stat
import sys

DEFAULT = Path('/workspace/infra-images/manifest.json')

def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)

def mount_info(path):
    result = subprocess.run(['findmnt', '--json', '--mountpoint', str(path),
                             '-o', 'SOURCE,FSTYPE,OPTIONS'], capture_output=True, text=True)
    if result.returncode:
        return None
    return json.loads(result.stdout)['filesystems'][0]

def validate(entry, directory, owner):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', entry['name']):
        raise RuntimeError('Invalid image name')
    payload = Path(entry['payload'])
    if payload.is_absolute() or '..' in payload.parts:
        raise RuntimeError('Invalid payload path')
    image = directory / entry['file']
    if image.parent != directory or image.is_symlink() or not image.is_file():
        raise RuntimeError('Missing or unsafe image: ' + str(image))
    if image.stat().st_uid != owner:
        raise RuntimeError('Image is not owned by the preserved user: ' + str(image))
    uuid = subprocess.check_output(['blkid', '-p', '-s', 'UUID', '-o', 'value', str(image)], text=True).strip()
    if uuid != entry['uuid']:
        raise RuntimeError('Image filesystem UUID differs: ' + str(image))
    if entry.get('readonly') and entry.get('sha256'):
        with image.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != entry['sha256']:
                raise RuntimeError('Read-only base image checksum differs: ' + str(image))
    return image

def ensure(manifest, directory):
    created = []
    try:
        # Validate every image before attaching any of them.
        images = [validate(e, directory, manifest['owner_uid']) for e in manifest['images']]
        # Device nodes are ephemeral after recreation. Add nodes only; mount
        # selects unused loop devices and never reconfigures somebody else's.
        for index in range(len(images) + 8):
            node = Path('/dev/loop' + str(index))
            if not node.exists():
                os.mknod(node, stat.S_IFBLK | 0o600, os.makedev(7, index))
        for entry, image in zip(manifest['images'], images):
            mounted = directory / 'mounts' / entry['name']
            mounted.mkdir(parents=True, exist_ok=True)
            info = mount_info(mounted)
            if info:
                source = info['source'].split('[')[0]
                backing = subprocess.check_output(['losetup', '--noheadings', '--output', 'BACK-FILE', source], text=True).strip()
                if info['fstype'] != 'ext4' or Path(backing).resolve() != image.resolve():
                    raise RuntimeError('Unexpected mounted filesystem: ' + str(mounted))
                if ('ro' in info['options'].split(',')) != entry.get('readonly', False):
                    raise RuntimeError('Unexpected mount write policy: ' + str(mounted))
            else:
                run('mount', '-o', 'loop,ro,noload' if entry.get('readonly') else 'loop', image, mounted)
                created.append(mounted)
            payload = mounted / entry['payload']
            if not payload.is_dir():
                raise RuntimeError('Missing image payload: ' + str(payload))
            target = Path(entry['target'])
            if not target.is_absolute() or target in map(Path, ('/', '/workspace', '/usr', '/etc', '/home', '/var', '/run')) or target.is_symlink():
                raise RuntimeError('Unsafe image target')
            target.mkdir(parents=True, exist_ok=True)
            target_info = mount_info(target)
            if target_info:
                if not os.path.samefile(payload, target):
                    raise RuntimeError('Unexpected target mount: ' + str(target))
                if ('ro' in target_info['options'].split(',')) != entry.get('readonly', False):
                    raise RuntimeError('Unexpected target write policy: ' + str(target))
            else:
                run('mount', '--bind', payload, target)
                created.append(target)
                if entry.get('readonly'):
                    run('mount', '-o', 'remount,bind,ro', target)
        return created
    except BaseException:
        for path in reversed(created):
            subprocess.run(['umount', str(path)], check=False)
        raise

def unmount_all(manifest, directory):
    for entry in reversed(manifest['images']):
        image = validate(entry, directory, manifest['owner_uid'])
        mounted = directory / 'mounts' / entry['name']
        target = Path(entry['target'])
        if mount_info(target):
            if not os.path.samefile(mounted / entry['payload'], target):
                raise RuntimeError('Refusing to unmount unrelated target')
            run('umount', target)
        if mount_info(mounted):
            info = mount_info(mounted)
            backing = subprocess.check_output(['losetup', '-n', '-O', 'BACK-FILE', info['source'].split('[')[0]], text=True).strip()
            if Path(backing).resolve() != image.resolve():
                raise RuntimeError('Refusing to unmount unrelated image')
            run('sync', '-f', mounted)
            run('umount', mounted)

def recover_backup(manifest, directory):
    helper = directory / 'backupctl.py'
    if helper.exists() and (directory / 'backup-maintenance.json').exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location('infra_backup_recovery', helper)
        backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backup)
        backup.recover_storage(manifest, directory)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=DEFAULT)
    parser.add_argument('--systems-only', action='store_true', help='initial provisioning: omit Incus data/pool and sandbox mounts')
    parser.add_argument('action', choices=('mount', 'status', 'shutdown', 'dispatch'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if os.geteuid() != 0:
        os.execvp('sudo', ['sudo', '-n', sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    # Mount in the platform namespace, including calls through operator SSH.
    if not os.path.samefile('/', '/proc/1/root'):
        os.execvp('nsenter', ['nsenter', '--target', '1', '--mount', '--root', '--wd',
                             sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    manifest = json.loads(args.manifest.read_text())
    if args.systems_only:
        if args.action != 'mount':
            raise RuntimeError('--systems-only is for the initial mount action only')
        names = {'debian-system', 'tools-system', 'incus-system', 'debian-runtime', 'infrastructure-runtime', 'incus-runtime'}
        manifest = dict(manifest, images=[entry for entry in manifest['images'] if entry['name'] in names])
    directory = args.manifest.parent.resolve()
    with open('/run/infra-image-storage.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == 'status':
            for entry in manifest['images']:
                validate(entry, directory, manifest['owner_uid'])
                print(entry['name'], 'mounted' if mount_info(entry['target']) else 'unmounted',
                      'readonly' if entry.get('readonly') else 'writable')
            return
        if args.action == 'shutdown':
            # Do not mount a missing deployment just to shut it down.
            runtime = Path('/workspace/infrastructure-runtime')
            if mount_info(runtime):
                run(runtime / 'bin/infractl', 'stop', env=dict(os.environ, INFRA_STORAGE_VERIFIED='1'))
            unmount_all(manifest, directory)
            return
        if args.action == 'dispatch' and not manifest.get('build_complete', False):
            raise RuntimeError('Image installation is not yet complete; startup refused')
        ensure(manifest, directory)
        recover_backup(manifest, directory)
    if args.action == 'dispatch':
        commands = {'infractl', 'incus', 'debianctl', 'tailscale', 'sv'}
        if not args.arguments or args.arguments[0] not in commands:
            raise RuntimeError('Select an installed infrastructure command')
        command = '/workspace/infrastructure-runtime/bin/' + args.arguments[0]
        if args.arguments[0] == 'incus' and len(args.arguments) > 1:
            invocation = args.arguments[1:]
            records = Path('/workspace/incus-runtime/state/layers.json')
            if records.exists() and invocation[0] in {'copy', 'move', 'export', 'publish', 'snapshot'}:
                layered = json.loads(records.read_text())['instances']
                if any(value.split('/')[0] in layered for value in invocation[1:]):
                    raise RuntimeError('Native snapshots/clones/exports do not include layered roots or homes; use stopped image backups and layerctl instead')
        os.environ['INFRA_STORAGE_VERIFIED'] = '1'
        os.execv(command, [command, *args.arguments[1:]])
    print('All image mounts verified')

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('image-storage:', error, file=sys.stderr)
        sys.exit(1)
