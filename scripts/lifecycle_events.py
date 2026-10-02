"""Private, bounded lifecycle events and cautious outer-environment observations."""
from contextlib import contextmanager, nullcontext
from collections import deque
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import sys
import time
import uuid

def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def read_optional(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return None

def process_ticks(pid):
    value = read_optional(f'/proc/{pid}/stat')
    try:
        return value.rpartition(')')[2].split()[19] if value else None
    except IndexError:
        return None

def parent_processes(pid):
    result = []
    for _ in range(6):
        result.append({'pid': pid, 'process': read_optional(f'/proc/{pid}/comm')})
        value = read_optional(f'/proc/{pid}/stat')
        try:
            parent = int(value.rpartition(')')[2].split()[1]) if value else 0
        except (ValueError, IndexError):
            break
        if pid <= 1 or parent <= 0 or parent == pid:
            break
        pid = parent
    return result

def outer_identity():
    """Use boot identity AND PID1/namespaces; a container shares the kernel."""
    result = {'kernel_boot_id': read_optional('/proc/sys/kernel/random/boot_id'),
              'pid1_start_ticks': process_ticks(1)}
    try:
        result['pid_namespace'] = os.readlink('/proc/1/ns/pid')
        root = Path('/').stat()
        result['root_device_inode'] = f'{root.st_dev}:{root.st_ino}'
    except OSError:
        pass
    mounts = read_optional('/proc/self/mountinfo')
    if mounts:
        for line in mounts.splitlines():
            fields = line.split()
            if len(fields) > 6 and fields[4] == '/':
                # Mount IDs change across namespaces; filesystem identity is more useful.
                identity = fields[2] + ' ' + fields[3] + ' ' + line.partition(' - ')[2]
                result['root_mount_fingerprint'] = hashlib.sha256(identity.encode()).hexdigest()
                break
    return result

def show_events(state, count=20):
    """Show recent records across rotations without dumping bulky context."""
    rows = deque(maxlen=count)
    paths = [state / f'events.jsonl.{index}' for index in range(5, 0, -1)] + [state / 'events.jsonl']
    lock_path = state / 'events.lock'
    with (lock_path.open() if lock_path.exists() else nullcontext()) as lock:
        if lock is not None:
            fcntl.flock(lock, fcntl.LOCK_SH)
        for path in paths:
            if not path.exists():
                continue
            with path.open() as source:
                for line in source:
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise ValueError('Expected event object')
                        rows.append(row)
                    except ValueError:
                        print('debianctl: unreadable event record in ' + str(path), file=sys.stderr)
    if not rows:
        print('No lifecycle events recorded yet')
    for row in rows:
        parts = [row.get('timestamp', '?'), 'run=' + row.get('run_id', '?')[:12],
                 row.get('action', '?') + '[' + row.get('trigger', '?') + ']', row.get('event', '?')]
        if row.get('event') == 'invocation_started':
            parts += ['user=' + (row.get('sudo_user') or row.get('user', '?')),
                      'via=' + '/'.join(parent.get('process') or '?' for parent in row.get('parent_processes', [{'process': row.get('parent_process')}])),
                      'cwd=' + (row.get('cwd') or '?')]
        for key in ('outcome', 'duration_ms', 'reason', 'manager_pid', 'init_pid',
                    'systemd_state', 'evidence', 'confidence', 'error', 'wait_ms'):
            if key in row:
                parts.append(key + '=' + str(row[key]))
        if row.get('changed_fields'):
            parts.append('changed=' + ','.join(row['changed_fields']))
        print(' '.join(parts))

class EventLog:
    def __init__(self, config, action, trigger, run_id=None, marker_dir=None,
                 max_bytes=2 * 1024 * 1024, backups=5):
        self.state = config['state_dir']
        self.machine = config['machine']
        self.run_id = run_id or uuid.uuid4().hex
        self.started = time.monotonic()
        self.marker_dir = Path(marker_dir) if marker_dir is not None else Path('/var/lib/debian-container-observer')
        self.max_bytes, self.backups = max_bytes, backups
        self.warned = False
        try:
            user = pwd.getpwuid(os.geteuid()).pw_name
        except KeyError:
            user = str(os.geteuid())
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = None
        sudo_user = None
        try:
            candidate = pwd.getpwuid(int(os.environ.get('SUDO_UID', ''))).pw_name
            if candidate == os.environ.get('SUDO_USER'):
                sudo_user = candidate
        except (ValueError, KeyError):
            pass
        self.context = {'run_id': self.run_id, 'machine': self.machine, 'action': action,
                        'trigger': trigger, 'pid': os.getpid(), 'ppid': os.getppid(),
                        'user': user, 'uid': os.geteuid(), 'sudo_user': sudo_user,
                        'cwd': cwd, 'stdin_is_tty': sys.stdin.isatty(),
                        'parent_process': read_optional(f'/proc/{os.getppid()}/comm'),
                        'parent_processes': parent_processes(os.getppid())}

    def warn(self, error):
        if not self.warned:
            print('debianctl: event logging unavailable: ' + str(error), file=sys.stderr)
            self.warned = True

    @contextmanager
    def locked(self):
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.chmod(0o700)
        with (self.state / 'events.lock').open('a') as lock:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def append_locked(self, event, **fields):
        path = self.state / 'events.jsonl'
        if path.exists() and path.stat().st_size >= self.max_bytes:
            for index in range(self.backups, 0, -1):
                previous = path if index == 1 else self.state / f'events.jsonl.{index-1}'
                if previous.exists():
                    previous.replace(self.state / f'events.jsonl.{index}')
        row = {'timestamp': timestamp(), **self.context, 'event': event, **fields}
        with path.open('a') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(json.dumps(row, separators=(',', ':')) + '\n')
            output.flush()

    def emit(self, event, **fields):
        try:
            with self.locked():
                self.append_locked(event, **fields)
        except (OSError, ValueError) as error:
            self.warn(error)

    def finish(self, outcome, error=None):
        fields = {'outcome': outcome, 'duration_ms': round((time.monotonic() - self.started) * 1000)}
        if error is not None:
            fields.update(error_type=type(error).__name__, error=str(error)[:2048])
        self.emit('invocation_failed' if error is not None else 'invocation_finished', **fields)

    def observe_environment(self):
        try:
            with self.locked():
                snapshot = self.state / 'outer-observation.json'
                previous = None
                if snapshot.exists():
                    try:
                        previous = json.loads(snapshot.read_text())
                        if not isinstance(previous, dict):
                            raise ValueError('Expected an observation object')
                        keys = ('kernel_boot_id', 'pid1_start_ticks', 'pid_namespace',
                                'root_device_inode', 'root_mount_fingerprint', 'root_marker')
                        if any(previous.get(key) is not None and not isinstance(previous[key], str) for key in keys):
                            raise ValueError('Invalid observation field type')
                    except ValueError:
                        previous = None
                        self.append_locked('observation_unreadable', reason='Saved observation is invalid; establishing a new baseline')
                current = outer_identity()
                current['observed_at'] = timestamp()
                marker = self.marker_dir / (self.machine + '.marker')
                try:
                    self.marker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                    self.marker_dir.chmod(0o700)
                    missing = False
                    try:
                        value = marker.read_text().strip()
                    except FileNotFoundError:
                        value = None
                        missing = True
                    if previous and previous.get('root_marker') and value != previous['root_marker']:
                        self.append_locked('outer_root_reset_suspected',
                                           evidence='root_marker_missing' if value is None else 'root_marker_changed',
                                           confidence='suspected', note='Marker loss/change can also result from manual cleanup or restore')
                    if not value:
                        value = uuid.uuid4().hex
                        with marker.open('x' if missing else 'w') as output:
                            os.fchmod(output.fileno(), 0o600)
                            output.write(value + '\n')
                    current['root_marker'] = value
                except OSError as error:
                    self.append_locked('root_marker_unavailable', error=str(error)[:1024])
                    # A temporary access problem is not evidence of a reset.
                    if previous and previous.get('root_marker'):
                        current['root_marker'] = previous['root_marker']
                if previous is None:
                    self.append_locked('environment_baseline', observation=current)
                else:
                    for event, keys in [
                            ('kernel_boot_changed', ('kernel_boot_id',)),
                            ('outer_container_changed', ('pid1_start_ticks', 'pid_namespace')),
                            ('outer_root_identity_changed', ('root_device_inode', 'root_mount_fingerprint'))]:
                        changed = [key for key in keys if previous.get(key) is not None and
                                   current.get(key) is not None and previous[key] != current[key]]
                        if changed:
                            self.append_locked(event, changed_fields=changed,
                                               previous={key: previous[key] for key in changed},
                                               current={key: current[key] for key in changed},
                                               note='Observed identity change; does not establish data loss')
                temporary = self.state / ('.outer-observation-' + self.run_id)
                try:
                    with temporary.open('w') as output:
                        os.fchmod(output.fileno(), 0o600)
                        json.dump(current, output)
                        output.write('\n'); output.flush(); os.fsync(output.fileno())
                    temporary.replace(snapshot)
                finally:
                    temporary.unlink(missing_ok=True)
        except (OSError, ValueError) as error:
            self.warn(error)
