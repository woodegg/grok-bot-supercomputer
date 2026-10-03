#!/usr/bin/python3
"""Boot and control a Debian systemd container without an outer systemd manager."""
import argparse
import contextlib
import fcntl
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import tomllib
from lifecycle_events import EventLog, show_events, timestamp

DEFAULT_CONFIG = Path('/workspace/debian-runtime/etc/config.toml')

def configuration(path):
    with path.open('rb') as source:
        data = tomllib.load(source)
    for field in ('rootfs', 'state_dir'):
        value = Path(data[field])
        if not value.is_absolute() or value == Path('/'):
            raise ValueError(f'{field} must be a dedicated absolute directory')
        data[field] = value.resolve()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]{0,62}', data['machine']):
        raise ValueError('Invalid machine name')
    if data['state_dir'].is_relative_to(data['rootfs']):
        raise ValueError('Host state_dir must be outside the guest rootfs')
    data.setdefault('private_users', 'identity')
    data.setdefault('allow_tun', False)
    data.setdefault('allow_nesting', False)
    if data['private_users'] not in ('identity', 'no') or type(data['allow_tun']) is not bool:
        raise ValueError('Use private_users="identity" or "no" and a boolean allow_tun')
    if data['allow_tun'] and data['private_users'] != 'no':
        raise ValueError('Shared-network TUN requires private_users="no"')
    if type(data['allow_nesting']) is not bool or (data['allow_nesting'] and data['private_users'] != 'no'):
        raise ValueError('Trusted Incus nesting requires allow_nesting=true and private_users="no"')
    return data

def process_birth(pid):
    try:
        # Field 22; the comm field itself can contain spaces and parentheses.
        return Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()[19]
    except (OSError, IndexError):
        return None

def manager_pid(config):
    try:
        pid, birth = (config['state_dir'] / 'manager.pid').read_text().split()
        pid = int(pid)
        if process_birth(pid) != birth:
            return None
        argv = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if ('--directory=' + str(config['rootfs'])).encode() not in argv:
            return None
        return pid
    except (OSError, ValueError):
        return None

def init_pid(config):
    manager = manager_pid(config)
    if not manager:
        # nspawn can die while its guest PID1 survives. Discover only in our
        # recorded subtree, with root inode and nested namespace PID1 checks.
        try:
            group=Path((config['state_dir']/'cgroup.path').read_text().strip())
            if group.name!=config['machine'] or not group.is_relative_to('/sys/fs/cgroup'):
                return None
            expected=config['rootfs'].stat()
            for file in group.rglob('cgroup.procs'):
                for entry in file.read_text().split():
                    pid=int(entry)
                    try:
                        status=Path(f'/proc/{pid}/status').read_text()
                        nspid=next(line.split()[1:] for line in status.splitlines() if line.startswith('NSpid:'))
                        root=Path(f'/proc/{pid}/root').stat()
                        relative=next(line.split('::')[1] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if line.startswith('0::'))
                        actual=Path('/sys/fs/cgroup')/relative.lstrip('/')
                        if len(nspid)>1 and nspid[-1]=='1' and (root.st_dev,root.st_ino)==(expected.st_dev,expected.st_ino) and actual.is_relative_to(group):
                            return pid
                    except (OSError,StopIteration):
                        continue
        except (OSError,ValueError):
            pass
        return None
    # Discover within the manager's descendants, checking the guest root and PID1.
    pending = [manager]
    seen = set()
    while pending:
        parent = pending.pop()
        if parent in seen:
            continue
        seen.add(parent)
        try:
            children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
        except OSError:
            continue
        for child in children:
            pid = int(child)
            pending.append(pid)
            try:
                status = Path(f'/proc/{pid}/status').read_text()
                nspid = next(line.split()[1:] for line in status.splitlines() if line.startswith('NSpid:'))
                guest_root = Path(f'/proc/{pid}/root').stat()
                expected_root = config['rootfs'].stat()
                if len(nspid) > 1 and nspid[-1] == '1' and (guest_root.st_dev, guest_root.st_ino) == (expected_root.st_dev, expected_root.st_ino):
                    return pid
            except (OSError, StopIteration):
                pass
    return None

def enter_command(config, argv):
    pid = init_pid(config)
    if not pid:
        raise RuntimeError('Guest systemd is not running; use start first')
    # Join the cgroup separately: util-linux 2.41.x --join-cgroup replaces stdin.
    # setns cannot re-enter the caller's current user namespace (EINVAL).
    user = [] if Path('/proc/self/ns/user').stat().st_ino == Path(f'/proc/{pid}/ns/user').stat().st_ino else ['--user']
    return ['/usr/bin/nsenter', '--target', str(pid), '--mount', '--uts', '--ipc',
            '--pid', '--cgroup', *user, '--root', '--wd',
            '/usr/bin/env', '-i', 'PATH=/usr/sbin:/usr/bin:/sbin:/bin',
            'HOME=/root', 'LANG=C.UTF-8', 'TERM=' + os.environ.get('TERM', 'xterm'), *argv]

def guest_cgroup(config):
    pid = init_pid(config)
    if not pid:
        raise RuntimeError('Guest systemd is not running')
    relative = next(line.split('::', 1)[1] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if line.startswith('0::'))
    group = Path('/sys/fs/cgroup') / relative.lstrip('/')
    owned = Path((config['state_dir'] / 'cgroup.path').read_text().strip())
    if not group.is_relative_to(owned):
        raise RuntimeError('Guest cgroup is outside the recorded container subtree')
    return group

def guest_run(config, argv, **kwargs):
    command = enter_command(config, argv)
    group = guest_cgroup(config)
    def join_child():
        (group / 'cgroup.procs').write_text(str(os.getpid()) + '\n')
    return subprocess.run(command, preexec_fn=join_child, **kwargs)

@contextlib.contextmanager
def control_lock(config, events=None):
    state = config['state_dir']
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    with (state / 'control.lock').open('a') as lock:
        waiting = False
        started = time.monotonic()
        end = time.monotonic() + 35
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if events and not waiting:
                    events.emit('control_lock_waiting')
                    waiting = True
                if time.monotonic() > end:
                    raise RuntimeError('Another container control operation holds the lock')
                time.sleep(.1)
        if events and waiting:
            events.emit('control_lock_acquired', wait_ms=round((time.monotonic() - started) * 1000))
        yield

def prune_empty_cgroup(path):
    if not path.exists():
        return
    if 'populated 1' in (path / 'cgroup.events').read_text():
        raise RuntimeError('Container cgroup still contains processes: ' + str(path))
    for child in sorted(path.iterdir()):
        if child.is_dir():
            prune_empty_cgroup(child)
    path.rmdir()

def worker(config, group, events=None):
    if events:
        events.emit('launcher_preparing', cgroup=str(group))
    # Move only this new worker. Never migrate platform or existing service processes.
    (group / 'cgroup.procs').write_text(str(os.getpid()) + '\n')
    # nspawn expects its outer runtime area to be memory-backed. Give only this
    # launcher a tmpfs, without mounting over the platform's own /run.
    os.unshare(os.CLONE_NEWNS)
    subprocess.run(['/usr/bin/mount', '--make-rslave', '/'], check=True)
    runtime = Path('/run/systemd/nspawn')
    runtime.mkdir(parents=True, exist_ok=True)
    subprocess.run(['/usr/bin/mount', '-t', 'tmpfs', '-o', 'mode=755,nosuid,nodev',
                    'tmpfs', str(runtime)], check=True)
    root = config['rootfs']
    loader = root / 'lib64/ld-linux-x86-64.so.2'
    library_path = ':'.join(str(root / folder) for folder in
                            ('usr/lib/x86_64-linux-gnu', 'usr/lib/x86_64-linux-gnu/systemd'))
    dropped = ['CAP_NET_RAW', 'CAP_SYS_MODULE', 'CAP_SYS_TIME', 'CAP_SYS_RAWIO',
               'CAP_AUDIT_CONTROL', 'CAP_AUDIT_READ', 'CAP_AUDIT_WRITE']
    if config.get('allow_nesting'):
        dropped.remove('CAP_NET_RAW')
    if not config['allow_tun'] and not config.get('allow_nesting'):
        dropped.append('CAP_NET_ADMIN')
    argv = [str(loader), '--library-path', library_path, str(root / 'usr/bin/systemd-nspawn'),
            '--directory=' + str(root), '--machine=' + config['machine'], '--boot',
            '--register=no', '--keep-unit', '--settings=no', '--console=pipe',
            '--private-users=' + config['private_users'], '--resolv-conf=copy-host', '--link-journal=no',
            '--drop-capability=' + ','.join(dropped)]
    if config['allow_tun']:
        argv += ['--capability=CAP_NET_ADMIN', '--bind=/dev/net/tun']
    if config.get('allow_nesting'):
        argv += ['--capability=CAP_NET_ADMIN,CAP_NET_RAW,CAP_BPF,CAP_PERFMON', '--bind=/dev/fuse']
    if events:
        events.emit('launcher_exec', cgroup=str(group), rootfs=str(root),
                    private_users=config['private_users'], allow_tun=config['allow_tun'])
    os.execve(str(loader), argv, {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                'LANG': 'C.UTF-8', 'SYSTEMD_COLORS': '0'})

def start(config, config_path, automatic=False, events=None):
    state = config['state_dir']
    with control_lock(config, events):
        if automatic and (state / 'disabled').exists():
            if events:
                events.emit('startup_skipped', reason='persistently_disabled')
            print('Guest is persistently disabled; automatic startup skipped')
            return 'skipped_disabled'
        if manager_pid(config):
            result = guest_run(config, ['/usr/bin/systemctl', 'is-system-running'],
                               capture_output=True, text=True, timeout=5)
            if result.stdout.strip() != 'running':
                if events:
                    events.emit('guest_unhealthy', manager_pid=manager_pid(config),
                                systemd_state=result.stdout.strip(), return_code=result.returncode)
                raise RuntimeError('Existing guest is not healthy: ' + result.stdout.strip())
            if events:
                events.emit('guest_already_running', manager_pid=manager_pid(config),
                            init_pid=init_pid(config), systemd_state='running')
            print('Guest already running; manager PID', manager_pid(config))
            return 'already_running'
        orphan=init_pid(config)
        if orphan:
            if events:
                events.emit('orphan_guest_shutdown_requested',init_pid=orphan)
            # systemd's documented SIGRTMIN+4 requests orderly poweroff.
            birth=process_birth(orphan)
            os.kill(orphan,signal.SIGRTMIN+4)
            deadline=time.monotonic()+50
            while process_birth(orphan)==birth and time.monotonic()<deadline:
                time.sleep(.2)
            if process_birth(orphan)==birth:
                raise RuntimeError('Orphan guest did not power off; no forced kill issued')
            group=Path((state/'cgroup.path').read_text().strip())
            deadline=time.monotonic()+10
            while group.exists() and 'populated 1' in (group/'cgroup.events').read_text():
                if time.monotonic()>deadline:
                    raise RuntimeError('Orphan guest subtree did not drain')
                time.sleep(.1)
        if events:
            events.emit('guest_start_requested', reason='not_running', automatic=automatic,
                        manager_record_present=(state / 'manager.pid').exists())
        root = config['rootfs']
        if not (root / 'etc/debian-container-provisioned').is_file():
            raise RuntimeError('Rootfs has not completed provision.sh')
        if (root / 'usr/sbin/policy-rc.d').exists():
            raise RuntimeError('Guest provisioning autostart guard was not removed')
        relative = next(line.split('::', 1)[1] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
        parent = Path('/sys/fs/cgroup') / relative.lstrip('/')
        group = parent / config['machine']
        if group.exists():
            recorded = state / 'cgroup.path'
            if not recorded.exists() or recorded.read_text().strip() != str(group):
                raise RuntimeError('Refusing an existing unowned cgroup: ' + str(group))
            prune_empty_cgroup(group)
            if events:
                events.emit('stale_cgroup_removed', cgroup=str(group))
        group.mkdir()
        # Kernfs resolves namespace mount roots through their outer ancestors.
        # Mapped Incus root needs traversal, never write access to this parent.
        if config.get('allow_nesting', False):
            group.chmod(0o755)
        (state / 'cgroup.path').write_text(str(group) + '\n')
        if not automatic:
            was_disabled = (state / 'disabled').exists()
            (state / 'disabled').unlink(missing_ok=True)
            if events and was_disabled:
                events.emit('guest_enabled')
        log_path = state / 'boot.log'
        # Bound the host-side console log; journald separately retains persistent logs.
        if log_path.exists() and log_path.stat().st_size > 8 * 1024 * 1024:
            log_path.replace(state / 'boot.log.1')
        with log_path.open('a') as log:
            if events:
                log.write(f'{timestamp()} run_id={events.run_id} launching machine={config["machine"]}\n')
                log.flush()
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                        '--config', str(config_path),
                                        *(['--trigger', events.context['trigger']] if events else []),
                                        '_worker', str(group),
                                        *([events.run_id] if events else [])],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True, close_fds=True)
        birth = process_birth(process.pid)
        (state / 'manager.pid').write_text(f'{process.pid} {birth}\n')
        if events:
            events.emit('guest_launcher_started', manager_pid=process.pid, cgroup=str(group))
        deadline = time.monotonic() + 50
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('systemd-nspawn exited; see ' + str(log_path))
                if init_pid(config):
                    result = guest_run(config, ['/usr/bin/systemctl', 'is-system-running'],
                                       capture_output=True, text=True, timeout=5)
                    if result.stdout.strip() in ('running', 'degraded'):
                        print('Guest systemd:', result.stdout.strip(), '; namespace PID1 host PID', init_pid(config))
                        if result.stdout.strip() == 'degraded':
                            if events:
                                events.emit('guest_unhealthy', manager_pid=process.pid,
                                            init_pid=init_pid(config), systemd_state='degraded')
                            raise RuntimeError('Guest boot is degraded; inspect systemctl --failed via exec')
                        if events:
                            events.emit('guest_started', manager_pid=process.pid,
                                        init_pid=init_pid(config), systemd_state='running')
                        return 'started'
                time.sleep(.3)
            raise RuntimeError('Guest boot readiness timed out; see ' + str(log_path))
        except BaseException:
            # Keep a live degraded guest for diagnosis; a dead manager can be cleaned.
            if process.poll() is not None:
                (state / 'manager.pid').unlink(missing_ok=True)
                prune_empty_cgroup(group)
            raise

def stop(config, persistent=True, events=None):
    with control_lock(config, events):
        state = config['state_dir']
        if persistent:
            (state / 'disabled').touch(mode=0o600)
            if events:
                events.emit('guest_disabled')
        manager = manager_pid(config)
        if events:
            events.emit('guest_stop_requested', manager_pid=manager, persistent=persistent)
        if manager:
            # nspawn maps SIGTERM to an orderly guest systemd poweroff.
            os.kill(manager, signal.SIGTERM)
            deadline = time.monotonic() + 40
            while manager_pid(config) and time.monotonic() < deadline:
                time.sleep(.2)
            if manager_pid(config):
                raise RuntimeError('Guest did not stop cleanly; inspect boot.log; no forced kill was issued')
        else:
            orphan=init_pid(config)
            if orphan:
                if events:
                    events.emit('orphan_guest_shutdown_requested',init_pid=orphan)
                birth=process_birth(orphan)
                os.kill(orphan,signal.SIGRTMIN+4)
                deadline=time.monotonic()+50
                while process_birth(orphan)==birth and time.monotonic()<deadline:
                    time.sleep(.2)
                if process_birth(orphan)==birth:
                    raise RuntimeError('Orphan guest did not power off; no forced kill issued')
        (state / 'manager.pid').unlink(missing_ok=True)
        group_file = state / 'cgroup.path'
        if group_file.exists():
            group = Path(group_file.read_text().strip())
            if group.name != config['machine'] or not group.is_relative_to('/sys/fs/cgroup'):
                raise RuntimeError('Invalid recorded cgroup path')
            deadline = time.monotonic() + 10
            while group.exists() and 'populated 1' in (group / 'cgroup.events').read_text():
                if time.monotonic() > deadline:
                    raise RuntimeError('Guest cgroup did not finish draining; no forced kill was issued')
                time.sleep(.1)
            prune_empty_cgroup(group)
        print('Guest stopped' + (' and persistently disabled' if persistent else ''))
        if events:
            events.emit('guest_stopped', was_running=bool(manager), persistent=persistent)
        return 'stopped' if manager else 'already_stopped'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--trigger', choices=['manual', 'scheduled', 'host-startup', 'bootstrap', 'unspecified'])
    parser.add_argument('action', choices=['start', 'startup', 'stop', 'restart', 'status', 'events', 'exec', 'shell', '_worker'])
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if os.geteuid() != 0:
        os.execv('/usr/bin/sudo', ['sudo', '-n', sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    os.umask(0o077)
    config = configuration(args.config)
    if args.action == '_worker':
        events = EventLog(config, '_worker', args.trigger or 'bootstrap',
                          run_id=args.arguments[1] if len(args.arguments) > 1 else None)
        try:
            worker(config, Path(args.arguments[0]), events)
        except Exception as error:
            events.finish('failed', error)
            raise
    elif args.action in ('start', 'startup', 'stop', 'restart'):
        trigger = args.trigger or ('unspecified' if args.action == 'startup' else 'manual')
        events = EventLog(config, args.action, trigger)
        events.emit('invocation_started', config_path=str(args.config), rootfs=str(config['rootfs']))
        try:
            events.observe_environment()
            if args.action in ('start', 'startup'):
                outcome = start(config, args.config, automatic=args.action == 'startup', events=events)
            elif args.action == 'stop':
                outcome = stop(config, events=events)
            else:
                if (config['state_dir'] / 'disabled').exists():
                    raise RuntimeError('Guest is disabled; use start explicitly')
                stop(config, persistent=False, events=events)
                outcome = start(config, args.config, events=events)
            events.finish(outcome)
        except BaseException as error:
            events.finish('failed', error)
            raise
    elif args.action == 'events':
        if len(args.arguments) > 1:
            raise RuntimeError('Usage: events [COUNT]')
        count = int(args.arguments[0]) if args.arguments else 20
        if not 1 <= count <= 1000:
            raise RuntimeError('Event count must be between 1 and 1000')
        show_events(config['state_dir'], count)
    elif args.action == 'status':
        pid = init_pid(config)
        if not pid:
            print('Guest stopped; ' + ('disabled' if (config['state_dir'] / 'disabled').exists() else 'enabled'))
            return
        print('Manager host PID:', manager_pid(config), '; guest PID1 host PID:', pid)
        result = guest_run(config, ['/usr/bin/systemctl', 'is-system-running'], capture_output=True, text=True, timeout=5)
        print(result.stdout.strip() or result.stderr.strip())
        raise SystemExit(result.returncode)
    elif args.action in ('exec', 'shell'):
        command = args.arguments
        if command and command[0] == '--':
            command = command[1:]
        if args.action == 'shell':
            if len(command) > 1:
                raise RuntimeError('Usage: shell [USER]')
            command = ['/usr/sbin/runuser', '--login', command[0] if command else 'operator']
        if not command:
            raise RuntimeError('exec requires a guest command')
        argv = enter_command(config, command)
        (guest_cgroup(config) / 'cgroup.procs').write_text(str(os.getpid()) + '\n')
        os.execv(argv[0], argv)

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit('debianctl: ' + str(error))
