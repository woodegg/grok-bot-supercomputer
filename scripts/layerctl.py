#!/usr/bin/python3
"""Explicit shared-base sandboxes on separate box-owned ext4 images.

Image preparation requires the Incus manager stopped. Incus native snapshots,
clones and exports do not include these externally managed root/home files.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import pwd
import re
import stat
import shutil
import subprocess
import sys
import tarfile

DIRECTORY = Path('/workspace/infra-images')
STATE = Path('/workspace/incus-runtime/state/layers.json')
MANAGER = Path('/workspace/incus-rootfs')
LAYER_ROOT = '/var/lib/incus/layers'

def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

def save(path, data):
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o600)
    if path == DIRECTORY / 'manifest.json':
        os.chown(temporary, pwd.getpwnam('box').pw_uid, pwd.getpwnam('box').pw_gid)
    temporary.replace(path)

def name(value):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,40}', value):
        raise argparse.ArgumentTypeError('Use a short lowercase name')
    return value

def incus(*args):
    return subprocess.check_output(['/workspace/infra-images/incus', *args], text=True).strip()

def offline():
    status = subprocess.run(['/workspace/incus-runtime/bin/debianctl', 'status'], capture_output=True, text=True, check=True)
    if not status.stdout.startswith('Guest stopped;'):
        raise RuntimeError('Stop Incus with infractl disable incus before preparing images; this interrupts its instances')

def create(manifest, state, kind, label, mib, prepare):
    if label in state[kind]:
        raise RuntimeError('Already exists: ' + label)
    account = pwd.getpwnam('box')
    creator = module('image_creator', 'create-image-storage.py')
    storage = module('image_storage', 'image-storage.py')
    image_name = ('base-' if kind == 'bases' else 'home-' if kind == 'homes' else 'delta-') + label
    target = MANAGER / (LAYER_ROOT.lstrip('/') + '/' + kind + '/' + label)
    entry = creator.create_image(DIRECTORY, image_name, mib, 'payload', str(target), account)
    mounted = DIRECTORY / 'mounts' / image_name
    subprocess.run(['mount', '-o', 'loop', str(DIRECTORY / entry['file']), str(mounted)], check=True)
    try:
        metadata = prepare(mounted / 'payload') or {}
        subprocess.run(['sync', '-f', str(mounted)], check=True)
    finally:
        subprocess.run(['umount', str(mounted)], check=True)
    if kind == 'bases':
        entry['readonly'] = True
        with (DIRECTORY / entry['file']).open('rb') as source:
            entry['sha256'] = hashlib.file_digest(source, 'sha256').hexdigest()
    candidate = dict(manifest, images=manifest['images'] + [entry])
    # Verify new mounts before registering this image in the saved manifest.
    storage.ensure(candidate, DIRECTORY)
    save(DIRECTORY / 'manifest.json', candidate)
    manifest.update(candidate)
    state[kind][label] = dict(image=image_name, path=LAYER_ROOT + '/' + kind + '/' + label)
    state[kind][label].update(metadata)
    save(STATE, state)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    base = commands.add_parser('base-import')
    base.add_argument('name', type=name)
    base.add_argument('archive', type=Path)
    base.add_argument('--size-mib', type=int, default=128)
    for kind in ('delta-create', 'home-create'):
        item = commands.add_parser(kind)
        item.add_argument('name', type=name)
        item.add_argument('--size-mib', type=int, default=256)
    register = commands.add_parser('register')
    register.add_argument('name', type=name)
    register.add_argument('--base', required=True, type=name)
    register.add_argument('--delta', required=True, type=name)
    register.add_argument('--home', required=True, type=name)
    switch = commands.add_parser('switch')
    switch.add_argument('name', type=name)
    switch.add_argument('--base', required=True, type=name)
    switch.add_argument('--delta', required=True, type=name)
    commands.add_parser('status')
    args = parser.parse_args()
    if os.geteuid() != 0:
        os.execvp('sudo', ['sudo', '-n', sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    if not os.path.samefile('/', '/proc/1/root'):
        os.execvp('nsenter', ['nsenter', '--target', '1', '--mount', '--root', '--wd',
                             sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    operation_lock = open('/run/infra-layer-operations.lock', 'a')
    fcntl.flock(operation_lock, fcntl.LOCK_EX)
    storage = module('image_storage', 'image-storage.py')
    # Serialize preparation with bootstrap mount operations.
    with open('/run/infra-image-storage.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = json.loads((DIRECTORY / 'manifest.json').read_text())
        storage.ensure(manifest, DIRECTORY)
        state = json.loads(STATE.read_text()) if STATE.exists() else dict(bases={}, deltas={}, homes={}, instances={})
        if args.action in ('base-import', 'delta-create', 'home-create'):
            offline()
            if args.size_mib < 32 or args.size_mib > 16384:
                raise RuntimeError('Use an explicit size between 32 and 16384 MiB')
            if args.action == 'base-import':
                archive = args.archive.resolve()
                if not archive.is_file():
                    raise RuntimeError('Rootfs archive not found')
                def prepare(path):
                    # Extraction is for trusted, verified Incus rootfs archives only.
                    with tarfile.open(archive) as source:
                        for member in source:
                            member_path = Path(member.name)
                            if member_path.is_absolute() or '..' in member_path.parts:
                                raise RuntimeError('Unsafe rootfs archive path')
                    with lzma.open(archive, 'rb') as source:
                        process = subprocess.Popen(['tar', '--numeric-owner', '--xattrs', '--acls', '-xpf', '-', '-C', str(path)], stdin=subprocess.PIPE)
                        try:
                            shutil.copyfileobj(source, process.stdin)
                        finally:
                            process.stdin.close()
                        if process.wait() != 0:
                            raise RuntimeError('Rootfs extraction failed')
                    if not (path / 'etc/os-release').exists():
                        raise RuntimeError('Expected an Incus rootfs archive, not an image metadata archive')
                    for parent, dirs, files in os.walk(path, followlinks=False):
                        for item in [Path(parent), *(Path(parent) / n for n in dirs + files)]:
                            st = item.lstat()
                            # Directories occur twice during traversal; do not shift twice.
                            if st.st_uid < 1000000 and st.st_gid < 1000000:
                                os.lchown(item, st.st_uid + 1000000, st.st_gid + 1000000)
                                if not item.is_symlink():
                                    item.chmod(stat.S_IMODE(st.st_mode))
                    (path / 'etc/layer-base-version').write_text(args.name + '\n')
                    os.chown(path / 'etc/layer-base-version', 1000000, 1000000)
                    release = (path / 'etc/os-release').read_text()
                    return {'init': 'busybox' if re.search(r'^ID=["\']?alpine["\']?$', release, re.M) else 'default'}
                create(manifest, state, 'bases', args.name, args.size_mib, prepare)
            else:
                kind = 'deltas' if args.action == 'delta-create' else 'homes'
                def prepare(path):
                    os.chown(path, 1000000, 1000000)
                    path.chmod(0o755)
                    if kind == 'deltas':
                        for directory in ('upper', 'work'):
                            (path / directory).mkdir()
                            os.chown(path / directory, 1000000, 1000000)
                create(manifest, state, kind, args.name, args.size_mib, prepare)
            print('Prepared', args.name, '; enable Incus after all image preparation is finished')
            return
        # Release storage lock before invoking wrappers that verify image mounts.
        fcntl.flock(lock, fcntl.LOCK_UN)
        if args.action == 'status':
            print(json.dumps(state, indent=2))
            return
        base_path = state['bases'][args.base]['path']
        delta_path = state['deltas'][args.delta]['path']
        # The image prep assumes the verified common unprivileged mapping.
        raw = 'lxc.rootfs.path = overlayfs:' + base_path + ':' + delta_path + '/upper\nlxc.rootfs.options = userxattr'
        if state['bases'][args.base].get('init') == 'busybox':
            # BusyBox PID1 uses USR2 for poweroff and TERM for reboot; the
            # generic systemd halt signal leaves these guests running.
            raw += '\nlxc.signal.halt = SIGUSR2\nlxc.signal.reboot = SIGTERM'
        if args.action == 'register':
            if args.name in state['instances']:
                raise RuntimeError('Instance already registered')
            for existing in state['instances'].values():
                used_deltas = {existing['delta'], *(item['delta'] for item in existing.get('history', []))}
                if args.delta in used_deltas or existing['home'] == args.home:
                    raise RuntimeError('Do not share a writable delta or home between instances')
            home_path = state['homes'][args.home]['path']
            incus('init', '--empty', args.name)
            mapping = json.loads(incus('config', 'get', args.name, 'volatile.idmap.next'))
            if not all(any(item['Nsid'] == 0 and item['Hostid'] == 1000000 and item['Maprange'] == 16777216 and item[kind] for item in mapping) for kind in ('Isuid', 'Isgid')):
                incus('delete', args.name)
                raise RuntimeError('Instance mapping differs from shared base owners; removed the new empty instance')
            incus('config', 'set', args.name, 'raw.lxc', raw)
            incus('config', 'device', 'add', args.name, 'home', 'disk', 'source=' + home_path, 'path=/home')
            state['instances'][args.name] = dict(base=args.base, delta=args.delta, home=args.home)
        else:
            instance = state['instances'][args.name]
            details = json.loads(incus('query', '/1.0/instances/' + args.name + '/state'))
            if details['status'] != 'Stopped':
                raise RuntimeError('Stop the instance before switching its root')
            for other, existing in state['instances'].items():
                used_deltas = {existing['delta'], *(item['delta'] for item in existing.get('history', []))}
                if other != args.name and args.delta in used_deltas:
                    raise RuntimeError('Delta already used by another instance')
            incus('config', 'set', args.name, 'raw.lxc', raw)
            instance.setdefault('history', []).append(dict(base=instance['base'], delta=instance['delta']))
            instance.update(base=args.base, delta=args.delta)
        save(STATE, state)
        print('Configured', args.name, '; start it explicitly with incus start')

if __name__ == '__main__':
    try:
        main()
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('layerctl:', error, file=sys.stderr)
        sys.exit(1)
