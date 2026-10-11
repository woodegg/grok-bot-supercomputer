#!/usr/bin/python3
"""Record bootstrap arrival before sudo/mount work, then preserve its exit code."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import uuid

class EntryLog:
    def __init__(self, directory, owner):
        self.directory, self.owner = Path(directory), owner

    def open_private(self, path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid not in (self.owner, os.geteuid()):
            os.close(fd)
            raise RuntimeError('Unsafe entry log file')
        os.fchmod(fd, 0o600)
        if os.geteuid() == 0:
            os.fchown(fd, self.owner, -1)
        return fd

    def emit(self, run_id, trigger, event, **fields):
        if self.directory.is_symlink():
            raise RuntimeError('Unsafe entry log directory')
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.stat().st_uid != self.owner:
            if os.geteuid() != 0:
                raise RuntimeError('Entry directory has unexpected owner')
            os.chown(self.directory, self.owner, -1)
        self.directory.chmod(0o700)
        lock = self.open_private(self.directory / 'entry.lock')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.directory / 'events.jsonl'
            if path.is_symlink():
                raise RuntimeError('Unsafe entry log path')
            if path.exists() and path.stat().st_size >= 1024 * 1024:
                for index in range(3, 0, -1):
                    previous = path if index == 1 else self.directory / ('events.jsonl.' + str(index - 1))
                    if previous.exists():
                        previous.replace(self.directory / ('events.jsonl.' + str(index)))
            row = dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       run_id=run_id, trigger=trigger, event=event,
                       pid=os.getpid(), ppid=os.getppid(), uid=os.getuid(), **fields)
            fd = self.open_private(path)
            try:
                data = (json.dumps(row) + '\n').encode()
                with os.fdopen(fd, 'ab') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                raise
        finally:
            os.close(lock)

def trigger_from(arguments):
    trigger = 'bootstrap'
    for index, value in enumerate(arguments):
        if value == '--trigger' and index + 1 < len(arguments):
            trigger = arguments[index + 1]
        elif value.startswith('--trigger='):
            trigger = value.split('=', 1)[1]
    return trigger if trigger in ('manual', 'scheduled', 'bootstrap', 'host-startup') else 'invalid'

def main():
    root = Path(__file__).resolve().parent
    logger = EntryLog(root / 'startup-entry', root.stat().st_uid)
    run_id, trigger = uuid.uuid4().hex, trigger_from(sys.argv[1:])
    started = time.monotonic()
    def emit(event, **fields):
        try:
            logger.emit(run_id, trigger, event, **fields)
        except (OSError, RuntimeError) as error:
            print('startup-entry logging unavailable: ' + str(error), file=sys.stderr)
    emit('entry_received')
    child = None
    def forward(signum, frame):
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, forward)
    try:
        child = subprocess.Popen([str(root / 'infractl'), '--trigger', 'bootstrap',
                                  *sys.argv[1:], 'startup'], start_new_session=True)
        emit('control_spawned', child_pid=child.pid)
        result = child.wait()
        code = result if result >= 0 else 128 - result
    except OSError as error:
        emit('control_spawn_failed', error_type=type(error).__name__)
        print('startup-entry: ' + str(error), file=sys.stderr)
        code = 127
    emit('entry_finished', exit_code=code, duration_ms=round((time.monotonic() - started) * 1000))
    return code

if __name__ == '__main__':
    sys.exit(main())
