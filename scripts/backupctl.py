#!/usr/bin/python3
"""Versioned configuration-only backups; never copy images or stop services."""
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import time
import uuid

ROOT = Path('/workspace/infra-images')
POLICY = ROOT / 'backup-policy.json'
JOURNAL = ROOT / 'backup-maintenance.json'
DEFAULTS = dict(enabled=True, config_interval_seconds=300, config_keep=12, external_directory=None)
RUNTIME = Path('/workspace/infrastructure-runtime')
ENV = dict(os.environ, INFRA_STORAGE_VERIFIED='1')

def storage_module(directory=ROOT):
    spec = importlib.util.spec_from_file_location('backup_image_storage', directory / 'image-storage.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def account(directory=ROOT):
    return pwd.getpwuid(json.loads((directory / 'manifest.json').read_text())['owner_uid'])

def atomic_json(path, data, directory=ROOT):
    owner = account(directory)
    temp = path.with_name(path.name + '.new-' + uuid.uuid4().hex)
    with temp.open('x') as output:
        json.dump(data, output, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    temp.chmod(0o600)
    os.chown(temp, owner.pw_uid, owner.pw_gid)
    temp.replace(path)
    sync_directory(path.parent)

def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def fingerprint(pid=None):
    pid = pid or os.getpid()
    return dict(pid=pid, boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                ticks=Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[19])

def alive(record):
    try:
        return fingerprint(record['pid']) == {key: record[key] for key in ('pid', 'boot', 'ticks')}
    except (OSError, KeyError, ValueError):
        return False

def command(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, env=ENV, **kwargs)

def incus(*args):
    return subprocess.check_output([str(RUNTIME / 'bin/incus'), *args], env=ENV, text=True).strip()

def supervisor_running():
    try:
        pid, ticks = (RUNTIME / 'state/supervisor.pid').read_text().split()
        if fingerprint(int(pid))['ticks'] != ticks:
            return False
        return b'/workspace/infrastructure-rootfs/usr/bin/runsvdir' in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    except (OSError, ValueError):
        return False

def policy():
    saved = json.loads(POLICY.read_text()) if POLICY.exists() else {'enabled': False}
    settings = {key: saved.get(key, value) for key, value in DEFAULTS.items()}
    settings['config_interval_seconds'] = saved.get('config_interval_seconds', saved.get('quick_interval_seconds', 300))
    settings['config_keep'] = saved.get('config_keep', saved.get('quick_keep', 12))
    if type(settings['enabled']) is not bool:
        raise RuntimeError('Backup enabled policy must be boolean')
    for key in ('config_interval_seconds', 'config_keep'):
        if type(settings[key]) is not int or settings[key] < 1:
            raise RuntimeError('Backup intervals and retention must be positive integers')
    return settings

def log(event, **fields):
    path = ROOT / 'backup-events.jsonl'
    owner = account()
    if path.exists() and path.stat().st_size > 1024 * 1024:
        path.replace(path.with_suffix('.jsonl.1'))
    with path.open('a') as output:
        output.write(json.dumps(dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(), event=event, **fields)) + '\n')
        output.flush()
        os.fsync(output.fileno())
    path.chmod(0o600)
    os.chown(path, owner.pw_uid, owner.pw_gid)

def restore_supervisor_policy(record):
    if 'supervisor_disabled' not in record or not (RUNTIME / 'etc/config.toml').exists():
        return
    disabled = RUNTIME / 'state/disabled'
    if record['supervisor_disabled']:
        disabled.touch(mode=0o600)
    else:
        disabled.unlink(missing_ok=True)

def recover_storage(manifest, directory=ROOT):
    """Called under the mount lock, before bootstrap. Never starts services."""
    journal = directory / 'backup-maintenance.json'
    if not journal.exists():
        return
    record = json.loads(journal.read_text())
    if alive(record) and record.get('active_capture', True) and record['pid'] != os.getpid():
        return
    entries = {entry['name']: entry for entry in manifest['images']}
    storage = storage_module(directory)
    for name in record.get('freeze_intents', []):
        entry = entries[name]
        storage.validate(entry, directory, manifest['owner_uid'])
        target = Path(entry['target'])
        if storage.mount_info(target):
            if not os.path.samefile(target, directory / 'mounts' / name / entry['payload']):
                raise RuntimeError('Refusing to thaw an unrelated filesystem')
            result = subprocess.run(['/usr/sbin/fsfreeze', '-u', str(target)], capture_output=True, text=True)
            if result.returncode and 'Invalid argument' not in result.stderr:
                raise RuntimeError('Cannot recover owned filesystem freeze: ' + name)
    record['freeze_intents'] = []
    restore_supervisor_policy(record)
    atomic_json(journal, record, directory)

def recover_instances():
    if not JOURNAL.exists():
        return
    record = json.loads(JOURNAL.read_text())
    if alive(record) and record.get('active_capture', True) and record['pid'] != os.getpid():
        return
    paused = record.get('pause_intents', [])
    if paused:
        if not supervisor_running() or (RUNTIME / 'services/incus/down').exists():
            return
        states = {item['name']: item['status'] for item in json.loads(incus('list', '--format=json'))}
        for name in paused:
            if states.get(name) in ('Frozen', 'Stopped'):
                incus('start', name)
    JOURNAL.unlink()
    sync_directory(ROOT)
    log('interrupted_backup_recovered')

def capture_configs(stage, workspace=Path('/workspace')):
    paths = []
    for base in ('debian-rootfs', 'infrastructure-rootfs', 'incus-rootfs'):
        paths.append(base + '/etc')
    for runtime in ('debian-runtime', 'infrastructure-runtime', 'incus-runtime'):
        paths += [runtime + '/etc', runtime + '/state/disabled']
    paths += ['incus-runtime/state/layers.json']
    services = workspace / 'infrastructure-runtime/services'
    if services.exists():
        for service in services.iterdir():
            if service.is_dir():
                for name in ('run', 'finish', 'down', 'log/run'):
                    paths.append(str((service / name).relative_to(workspace)))
    paths = [path for path in paths if (workspace / path).exists()]
    command('tar', '--numeric-owner', '--acls', '--xattrs', '--atime-preserve=system',
            '-czf', stage / 'configuration.tar.gz', '-C', workspace, *paths)
    # Export declarative Incus configuration, never its database or volume contents.
    if not (RUNTIME / 'services/incus/down').exists():
        configuration = {}
        for name, endpoint in (('server', '/1.0'), ('projects', '/1.0/projects?recursion=1'),
                               ('profiles', '/1.0/profiles?recursion=1&all-projects=true'),
                               ('networks', '/1.0/networks?recursion=1&all-projects=true'),
                               ('storage_pools', '/1.0/storage-pools?recursion=1'),
                               ('instances', '/1.0/instances?recursion=1&all-projects=true')):
            data = json.loads(incus('query', endpoint))
            if name == 'server':
                data = {'config': data.get('config', {})}
            elif name == 'instances':
                keys = ('name', 'project', 'type', 'architecture', 'config', 'devices', 'profiles', 'description', 'ephemeral')
                data = [{key: item[key] for key in keys if key in item} for item in data]
            configuration[name] = data
        (stage / 'incus-configuration.json').write_text(json.dumps(configuration, indent=2) + '\n')

def completed(root, kind=None):
    if not root.exists():
        return []
    result = []
    for path in root.iterdir():
        if path.is_symlink() or not path.is_dir() or path.name.startswith('.'):
            continue
        metadata = path / 'metadata.json'
        if not (path / 'COMPLETE').is_file() or not metadata.is_file():
            continue
        data = json.loads(metadata.read_text())
        if kind is None or data['kind'] == kind:
            result.append((path, data))
    return sorted(result, key=lambda item: item[1]['created_epoch'])

def due(root, kind, interval, now=None):
    generations = completed(root, kind)
    current = time.time() if now is None else now
    return not generations or current < generations[-1][1]['created_epoch'] or current - generations[-1][1]['created_epoch'] >= interval

def verify_generation(path):
    if not (path / 'COMPLETE').is_file():
        raise RuntimeError('Backup generation is incomplete')
    data = json.loads((path / 'metadata.json').read_text())
    if set(data['hashes']) != {'payload.tar.gz'}:
        raise RuntimeError('Unexpected backup payload')
    with (path / 'payload.tar.gz').open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != data['hashes']['payload.tar.gz']:
            raise RuntimeError('Backup payload checksum differs')
    return data

def mirror_generation(path, external):
    if not external:
        return False
    destination = Path(external)
    if not destination.is_absolute() or not destination.is_dir():
        raise RuntimeError('External backup directory must already exist')
    if destination.resolve().is_relative_to(Path('/workspace')):
        raise RuntimeError('External backup destination must be outside /workspace')
    if destination.stat().st_dev == ROOT.stat().st_dev:
        raise RuntimeError('External backup destination is on the same filesystem')
    kind = subprocess.check_output(['findmnt', '-n', '-o', 'FSTYPE', '-T', str(destination)], text=True).strip()
    if kind in ('tmpfs', 'ramfs', 'overlay'):
        raise RuntimeError('External destination does not expose independent persistent storage')
    temporary = destination / ('.' + path.name + '.incomplete')
    final = destination / path.name
    if final.exists():
        verify_generation(final)
        return True
    if temporary.exists():
        if temporary.is_symlink() or not temporary.is_dir():
            raise RuntimeError('Unsafe external staging path')
        shutil.rmtree(temporary)
    shutil.copytree(path, temporary)
    verify_generation(temporary)
    owner = account()
    os.chown(temporary, owner.pw_uid, owner.pw_gid)
    for item in temporary.iterdir():
        os.chown(item, owner.pw_uid, owner.pw_gid)
        with item.open('rb') as source:
            os.fsync(source.fileno())
    sync_directory(temporary)
    temporary.rename(final)
    sync_directory(destination)
    return True

def publish(stage, metadata, destination, owner):
    for parent, dirs, files in os.walk(stage):
        os.chown(parent, owner.pw_uid, owner.pw_gid)
        os.chmod(parent, 0o700)
        for name in files:
            path = Path(parent) / name
            os.chown(path, owner.pw_uid, owner.pw_gid)
            path.chmod(0o600)
    atomic_json(stage / 'metadata.json', metadata)
    marker = stage / 'COMPLETE'
    marker.write_text('completed and checksummed\n')
    marker.chmod(0o600)
    os.chown(marker, owner.pw_uid, owner.pw_gid)
    with marker.open('rb') as source:
        os.fsync(source.fileno())
    sync_directory(stage)
    stage.rename(destination)
    sync_directory(destination.parent)

def backup(kind):
    root = ROOT / 'backups'
    owner = account()
    root.mkdir(mode=0o700, exist_ok=True)
    os.chown(root, owner.pw_uid, owner.pw_gid)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    label = kind + '-' + timestamp + '-' + uuid.uuid4().hex[:6]
    stage = root / ('.' + label + '.incomplete')
    stage.mkdir(mode=0o700)
    capture = stage / 'capture'
    capture.mkdir()
    started = time.monotonic()
    log('backup_started', kind=kind, generation=label)
    with open('/run/infra-image-storage.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if JOURNAL.exists():
            raise RuntimeError('Legacy backup maintenance must be recovered before configuration capture')
        capture_configs(capture)
        for name in ('manifest.json', 'backup-policy.json'):
            shutil.copy2(ROOT / name, capture / name)
        capture_seconds = round(time.monotonic() - started, 3)
    payload = stage / 'payload.tar.gz'
    command('tar', '--sparse', '-I', 'gzip -1', '-cf', payload, '-C', capture, '.')
    with payload.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        os.fsync(stream.fileno())
    shutil.rmtree(capture)
    metadata = dict(version=2, scope='configuration-only', kind=kind, created_epoch=time.time(), generation=label,
                    hashes={'payload.tar.gz': digest}, images=[],
                    capture_seconds=capture_seconds, sqlite_backups=0, independent_copy=False)
    final = root / label
    publish(stage, metadata, final, owner)
    settings = policy()
    external_error = None
    try:
        if mirror_generation(final, settings['external_directory']):
            metadata['independent_copy'] = True
            atomic_json(final / 'metadata.json', metadata)
    except Exception as error:
        external_error = str(error)
        log('external_copy_failed', generation=label, error=external_error)
    keep = settings[kind + '_keep']
    generations = completed(root, kind)
    for old, _ in generations[:-keep]:
        if old.parent != root or old.is_symlink():
            raise RuntimeError('Unsafe retention path')
        shutil.rmtree(old)
    log('backup_completed', kind=kind, generation=label, capture_seconds=capture_seconds,
        total_seconds=round(time.monotonic() - started, 3), sqlite_backups=0,
        independent_copy=metadata['independent_copy'])
    print(label, 'complete; capture seconds:', capture_seconds, flush=True)
    if external_error:
        raise RuntimeError('Local backup complete, external copy failed: ' + external_error)
    return final

def tick():
    settings = policy()
    if not settings['enabled'] or not supervisor_running() or (RUNTIME / 'state/disabled').exists():
        return
    with open('/run/infra-backup-operation.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with open('/run/infra-image-storage.lock', 'a') as mount_lock:
            fcntl.flock(mount_lock, fcntl.LOCK_EX)
            recover_storage(json.loads((ROOT / 'manifest.json').read_text()))
            recover_instances()
        root = ROOT / 'backups'
        if settings['external_directory']:
            points = completed(root, 'config')
            if points and not points[-1][1]['independent_copy']:
                path, data = points[-1]
                if mirror_generation(path, settings['external_directory']):
                    data['independent_copy'] = True
                    atomic_json(path / 'metadata.json', data)
        if due(root, 'config', settings['config_interval_seconds']):
            backup('config')

def start_worker():
    if not policy()['enabled'] or not supervisor_running() or (RUNTIME / 'state/disabled').exists():
        return
    record = ROOT / 'backup-worker.json'
    with open('/run/infra-backup-worker-start.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if record.exists() and alive(json.loads(record.read_text())):
            return
        log_path = ROOT / 'backup-worker.log'
        if log_path.exists() and log_path.stat().st_size > 1024 * 1024:
            log_path.replace(log_path.with_suffix('.log.1'))
        with log_path.open('a') as output:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'worker'],
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       cwd=ROOT, start_new_session=True, env=ENV)
        os.chown(log_path, account().pw_uid, account().pw_gid)
        log_path.chmod(0o600)
        atomic_json(record, fingerprint(process.pid))

def worker():
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log('worker_started')
    while not stopping and policy()['enabled']:
        try:
            tick()
        except Exception as error:
            log('backup_failed', error=str(error))
        for _ in range(30):
            if stopping or not policy()['enabled']:
                break
            time.sleep(1)
    log('worker_stopped')

def extract_generation(generation, destination):
    metadata = verify_generation(generation)
    if destination.exists() or not destination.is_absolute():
        raise RuntimeError('Use a new absolute recovery directory; active deployment is never overwritten')
    destination.mkdir(mode=0o700)
    try:
        with tarfile.open(generation / 'payload.tar.gz', 'r:gz') as archive:
            archive.extractall(destination, filter='data')
        owner = account()
        for parent, dirs, files in os.walk(destination):
            os.chown(parent, owner.pw_uid, owner.pw_gid)
            for name in files:
                os.chown(Path(parent) / name, owner.pw_uid, owner.pw_gid)
        # Older recovery points may have been captured before helper copy2.
        # Restore executable bits only for known deployment entrypoints.
        for item in destination.iterdir():
            if item.is_file() and (item.suffix in ('.py', '.sh') or item.name in ('infractl', 'incus', 'sv', 'debianctl', 'tailscale', 'layerctl', 'backupctl')):
                item.chmod(0o755)
        return metadata
    except BaseException:
        # Keep an unsuccessful candidate for diagnosis; never touch live images.
        raise

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('enable', 'disable', 'status', 'config', 'quick', 'checkpoint', 'tick', 'start-worker', 'worker', 'verify', 'extract', 'external'))
    parser.add_argument('arguments', nargs='*')
    args = parser.parse_args()
    required = {'verify': 1, 'extract': 2, 'external': 1}.get(args.action, 0)
    if len(args.arguments) != required:
        parser.error('Unexpected number of arguments for ' + args.action)
    if os.geteuid() != 0:
        os.execvp('sudo', ['sudo', '-n', sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    if not os.path.samefile('/', '/proc/1/root'):
        os.execvp('nsenter', ['nsenter', '--target', '1', '--mount', '--root', '--wd', sys.executable,
                             str(Path(__file__).resolve()), *sys.argv[1:]])
    os.umask(0o077)
    if args.action in ('enable', 'disable', 'external'):
        settings = policy()
        if args.action == 'external':
            if len(args.arguments) != 1:
                raise RuntimeError('Supply one independent mounted directory')
            settings['external_directory'] = str(Path(args.arguments[0]).resolve())
        else:
            settings['enabled'] = args.action == 'enable'
        atomic_json(POLICY, settings)
        if settings['enabled']:
            start_worker()
        print('Backup policy saved')
    elif args.action == 'status':
        print(json.dumps(policy(), indent=2))
        record = ROOT / 'backup-worker.json'
        print('worker:', 'running' if record.exists() and alive(json.loads(record.read_text())) else 'stopped')
        for path, data in completed(ROOT / 'backups'):
            print(path.name, 'capture_seconds=', data['capture_seconds'], 'sqlite=', data['sqlite_backups'], 'external=', data['independent_copy'])
    elif args.action == 'start-worker':
        start_worker()
    elif args.action == 'worker':
        worker()
    elif args.action == 'tick':
        tick()
    elif args.action in ('verify', 'extract'):
        generation = ROOT / 'backups' / args.arguments[0]
        if generation.parent != ROOT / 'backups':
            raise RuntimeError('Select a local completed generation name')
        if args.action == 'verify':
            verify_generation(generation)
            print('PASS: completed generation and payload checksum')
        else:
            extract_generation(generation, Path(args.arguments[1]))
            print('Recovery candidate extracted; active deployment unchanged')
    else:
        with open('/run/infra-backup-operation.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            backup('config')  # Legacy quick/checkpoint names are config-only aliases.

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, sqlite3.Error) as error:
        print('backupctl:', error, file=sys.stderr)
        sys.exit(1)
