#!/usr/bin/python3
"""Record and compare private image preservation evidence; never reset or repair."""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid

DEFAULT = Path('/workspace/infra-images')

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def stat_record(s):
    def utc(ns):
        return datetime.datetime.fromtimestamp(ns / 1_000_000_000, datetime.timezone.utc).isoformat()
    return dict(size=s.st_size, allocated=s.st_blocks * 512, uid=s.st_uid,
                gid=s.st_gid, mode=stat.S_IMODE(s.st_mode), mode_octal=format(stat.S_IMODE(s.st_mode), '04o'),
                inode=s.st_ino, device=s.st_dev, links=s.st_nlink, block_size=s.st_blksize,
                mtime_ns=s.st_mtime_ns, mtime_utc=utc(s.st_mtime_ns),
                ctime_ns=s.st_ctime_ns, ctime_utc=utc(s.st_ctime_ns),
                atime_ns=s.st_atime_ns, atime_utc=utc(s.st_atime_ns),
                birthtime_ns=None, birthtime_note='not available through this stat API',
                stat_observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())

def file_record(path, hashing=True):
    if not path.exists():
        return {'missing': True}
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('Refusing non-regular file: ' + str(path))
    s = path.stat()
    return dict(stat_record(s),
                sha256=digest(path) if hashing else None)

def attached(path):
    result = subprocess.run(['losetup', '--json', '--list', '--output', 'BACK-FILE'],
                            check=True, capture_output=True, text=True)
    return any(item.get('back-file') and
               Path(item['back-file']).resolve() == path.resolve()
               for item in json.loads(result.stdout)['loopdevices'])

def readonly_attachment(path):
    result = subprocess.run(['losetup', '--json', '--list', '--output', 'BACK-FILE,RO'],
                            check=True, capture_output=True, text=True)
    matches = [item for item in json.loads(result.stdout)['loopdevices']
               if item.get('back-file') and Path(item['back-file']).resolve() == path.resolve()]
    return all(item['ro'] in (True, 1, '1') for item in matches)

def atomic_record(path, data, owner):
    # New records are exclusive: never erase the pre-reset evidence.
    if path.exists() or path.is_symlink():
        raise RuntimeError('Record already exists: ' + str(path))
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.chown(temporary, owner, -1)
        os.link(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)

def host_record():
    root = Path('/proc/1/root').stat()
    return dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                pid1_start_ticks=Path('/proc/1/stat').read_text().split(') ', 1)[1].split()[19],
                root_device=root.st_dev, root_inode=root.st_ino)

@contextlib.contextmanager
def probe_mount(image, writable=False):
    if attached(image):
        raise RuntimeError('Probe image is already attached')
    # Private namespace: temporary mounts cannot propagate to the platform.
    with tempfile.TemporaryDirectory(prefix='infra-survival-mount-') as name:
        subprocess.run(['mount', '-o', 'loop' if writable else 'loop,ro,noload',
                        str(image), name], check=True)
        try:
            yield Path(name)
        finally:
            subprocess.run(['umount', name], check=True)

def prepare(directory, owner):
    probe = directory / 'probe.ext4'
    if probe.exists() or probe.is_symlink():
        raise RuntimeError('Probe already exists; it must not be recreated before verification')
    # This is the only formatting action; it creates a NEW disposable probe only.
    with probe.open('xb') as stream:
        stream.truncate(16 * 1024 * 1024)
    probe.chmod(0o600)
    os.chown(probe, owner, -1)
    subprocess.run(['mkfs.ext4', '-q', '-F', '-m', '0', str(probe)], check=True)
    nonce = uuid.uuid4().hex + '\n'
    with probe_mount(probe, writable=True) as mount:
        folder = mount / 'root-private'
        folder.mkdir(mode=0o700)
        folder.chmod(0o700)
        inside = folder / 'proof'
        inside.write_text(nonce)
        inside.chmod(0o600)
        subprocess.run(['sync', '-f', str(mount)], check=True)
    for name, uid in [('box-control', owner), ('root-control', 0)]:
        path = directory / name
        with path.open('x') as stream:
            stream.write(nonce)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o600)
        os.chown(path, uid, -1)

def snapshot(root, directory, manifest, mode):
    images = {}
    # Preflight before hashing anything. Even attached-but-unmounted images
    # are refused for offline evidence. No fsck is run against active storage.
    for entry in manifest['images']:
        path = root / entry['file']
        if path.parent != root or path.is_symlink():
            raise RuntimeError('Unsafe manifest image path')
        if mode == 'offline' and path.exists() and attached(path):
            raise RuntimeError('Offline check requires ALL images detached: ' + entry['name'])
    for entry in manifest['images']:
        path = root / entry['file']
        hashing = mode == 'offline' or (entry.get('readonly', False) and readonly_attachment(path))
        record = file_record(path, hashing)
        record['expected_readonly'] = entry.get('readonly', False)
        record['expected_uuid'] = entry['uuid']
        if not record.get('missing'):
            record['uuid'] = subprocess.check_output(
                ['blkid', '-p', '-s', 'UUID', '-o', 'value', str(path)], text=True).strip()
        images[entry['name']] = record
    probe = directory / 'probe.ext4'
    result = dict(version=2, time=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  mode=mode, host=host_record(), manifest_sha256=digest(root / 'manifest.json'),
                  images=images, probe=file_record(probe),
                  controls={name: file_record(directory / name) for name in ('box-control', 'root-control')})
    if probe.exists():
        with probe_mount(probe) as mount:
            folder = mount / 'root-private'
            s = folder.stat()
            result['inside_directory'] = stat_record(s)
            result['inside_file'] = file_record(folder / 'proof')
    return result

def compare(before, after):
    findings = []
    def metadata(label, a, b):
        keys = ('allocated', 'inode', 'device', 'links', 'block_size', 'mtime_ns', 'ctime_ns', 'atime_ns')
        changes = {key: dict(before=a.get(key), after=b.get(key)) for key in keys
                   if a.get(key) != b.get(key)}
        if changes:
            findings.append(dict(item=label + '/metadata', outcome='INFO', changes=changes))
    def check(label, a, b, keys):
        if a.get('missing') or b.get('missing'):
            findings.append(dict(item=label, outcome='MISSING', before=a.get('missing', False), after=b.get('missing', False)))
            return
        changes = [key for key in keys if a.get(key) != b.get(key)]
        findings.append(dict(item=label, outcome='CHANGED' if changes else 'PASS', fields=changes,
                             changes={key: dict(before=a.get(key), after=b.get(key)) for key in changes}))
        metadata(label, a, b)
    findings.append(dict(item='manifest', outcome='PASS' if before['manifest_sha256'] == after['manifest_sha256'] else 'CHANGED'))
    for name in sorted(set(before['images']) | set(after['images'])):
        a, b = before['images'].get(name, {'missing': True}), after['images'].get(name, {'missing': True})
        keys = ['size', 'uid', 'gid', 'mode', 'uuid']
        if a.get('sha256') is not None and b.get('sha256') is not None:
            keys += ['sha256']
        check('image/' + name, a, b, keys)
        if not a.get('missing') and not b.get('missing') and (a.get('sha256') is None or b.get('sha256') is None):
            findings.append(dict(item='image/' + name + '/content', outcome='UNVERIFIED', reason='mutable image in live mode'))
        # Sparse allocation is informational; densification is not content loss.
        if a.get('allocated') != b.get('allocated'):
            findings.append(dict(item='image/' + name + '/allocation', outcome='INFO', before=a.get('allocated'), after=b.get('allocated')))
    for key in ('probe', 'inside_directory', 'inside_file'):
        check(key, before.get(key, {'missing': True}), after.get(key, {'missing': True}),
              ['uid', 'gid', 'mode'] + ([] if key == 'inside_directory' else ['size', 'sha256']))
    for name in ('box-control', 'root-control'):
        check(name, before['controls'][name], after['controls'][name], ['uid', 'gid', 'mode', 'size', 'sha256'])
    changed = [key for key in before['host'] if before['host'][key] != after['host'][key]]
    return dict(host_observation='changed' if changed else 'unchanged', host_changed_fields=changed,
                findings=findings, deployment_mismatch=any(x['outcome'] in ('CHANGED', 'MISSING')
                for x in findings if x['item'] != 'root-control'),
                complete_content_check=not any(x['outcome'] == 'UNVERIFIED' for x in findings))

def append_observation(directory, record, owner):
    path = directory / 'observations.jsonl'
    if path.exists() and path.stat().st_size > 1024 * 1024:
        path.replace(directory / 'observations.jsonl.1')
    with path.open('a') as stream:
        stream.write(json.dumps(record) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)
    os.chown(path, owner, -1)

def observe(root, directory, manifest, trigger):
    # Bootstrap never formats probes. Missing evidence must remain visible.
    baseline = directory / 'bootstrap-live.json'
    try:
        if not baseline.exists():
            raise RuntimeError('No bootstrap-live baseline; run prepare then capture bootstrap-live')
        before = json.loads(baseline.read_text())
        if before['mode'] != 'live':
            raise RuntimeError('Bootstrap baseline must use live mode')
        after = snapshot(root, directory, manifest, 'live')
        report = compare(before, after)
        summary = dict(time=after['time'], trigger=trigger,
                       outcome='mismatch' if report['deployment_mismatch'] else 'partial_pass',
                       host_observation=report['host_observation'],
                       host_changed_fields=report['host_changed_fields'],
                       findings=report['findings'], snapshot=after)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        summary = dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       trigger=trigger, outcome='error', error=str(error))
    append_observation(directory, summary, manifest['owner_uid'])
    print('Survivability:', summary['outcome'])
    return 1 if summary['outcome'] in ('error', 'mismatch') else 0

def acquire_storage_lock(lock, action):
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if action == 'observe' else 0))
        return True
    except BlockingIOError:
        return False

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'capture', 'verify', 'observe'))
    parser.add_argument('label', nargs='?', default='baseline')
    parser.add_argument('--root', type=Path, default=DEFAULT)
    parser.add_argument('--mode', choices=('live', 'offline'), default='live')
    parser.add_argument('--trigger', choices=('manual', 'scheduled', 'bootstrap', 'host-startup'), default='manual')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', args.label):
        parser.error('Use a simple record label')
    if os.geteuid() != 0:
        os.execvp('sudo', ['sudo', '-n', sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    # Enter host mount view, then make it private before inspecting probe mounts.
    if os.environ.get('INFRA_SURVIVAL_PRIVATE') != '1':
        env = dict(os.environ, INFRA_SURVIVAL_PRIVATE='1')
        os.execvpe('nsenter', ['nsenter', '--target', '1', '--mount', '--root', '--wd',
                  'unshare', '--mount', '--propagation', 'private', sys.executable,
                  str(Path(__file__).resolve()), *sys.argv[1:]], env)
    os.umask(0o077)
    root = args.root.resolve()
    manifest = json.loads((root / 'manifest.json').read_text())
    directory = root / 'survivability'
    if directory.is_symlink():
        raise RuntimeError('Unsafe evidence directory')
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chown(directory, manifest['owner_uid'], -1)
    directory.chmod(0o700)
    with open('/run/infra-image-storage.lock', 'a') as lock:
        if not acquire_storage_lock(lock, args.action):
            append_observation(directory, dict(
                time=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                trigger=args.trigger, outcome='deferred', reason='storage maintenance lock busy'),
                manifest['owner_uid'])
            print('Survivability: deferred')
            return
        if args.action == 'prepare':
            prepare(directory, manifest['owner_uid'])
            print('Created disposable probe and ownership controls; no deployment images changed')
            return
        if args.action == 'observe':
            sys.exit(observe(root, directory, manifest, args.trigger))
        baseline = directory / (args.label + '.json')
        if args.action == 'capture' and baseline.exists():
            raise RuntimeError('Baseline exists; choose a new label')
        if not (directory / 'probe.ext4').exists() and args.action == 'capture':
            raise RuntimeError('Run prepare first')
        before = json.loads(baseline.read_text()) if args.action == 'verify' else None
        if before and args.mode != before['mode']:
            raise RuntimeError('Use the same mode as the baseline: ' + before['mode'])
        after = snapshot(root, directory, manifest, args.mode)
        if args.action == 'capture':
            atomic_record(baseline, after, manifest['owner_uid'])
            print('Saved baseline:', baseline)
            return
        report = compare(before, after)
        report['after'] = after
        path = directory / (args.label + '-verify-' + uuid.uuid4().hex[:12] + '.json')
        atomic_record(path, report, manifest['owner_uid'])
        for finding in report['findings']:
            print(finding['outcome'], finding['item'], ','.join(finding.get('fields', [])))
        print('Host evidence:', report['host_observation'], ','.join(report['host_changed_fields']))
        print('Report:', path)
        print('This comparison alone does not prove a platform reset or application consistency.')
        sys.exit(1 if report['deployment_mismatch'] else 2 if not report['complete_content_check'] else 0)

if __name__ == '__main__':
    main()
