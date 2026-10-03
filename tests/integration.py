#!/usr/bin/python3
"""Exercise live boot, guest users/services and persistence; restarts the guest."""
import os
from pathlib import Path
import subprocess
import uuid

CONTROL = os.environ.get('DEBIANCTL', '/workspace/debian-runtime/bin/debianctl')

def control(*args, input=None):
    result = subprocess.run([CONTROL, *args], input=input, text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout.strip()

def guest(*args, input=None):
    return control('exec', '--', *args, input=input)

def main():
    name = 'verify-' + uuid.uuid4().hex[:8]
    unit = name + '.service'
    account_created = False
    service_created = False
    control('start')
    assert guest('ps', '-p', '1', '-o', 'comm=') == 'systemd'
    assert guest('systemctl', 'is-system-running') == 'running'
    identity = guest('cat', '/etc/machine-id')
    original_operator = guest('getent', 'passwd', 'operator')
    state_dir = Path(os.environ.get('DEBIAN_STATE', '/workspace/debian-runtime/state'))
    try:
        guest('useradd', '-m', '-s', '/bin/bash', name)
        account_created = True
        guest('sh', '-c', f'printf "persistent-test-data\\n" > /home/{name}/check; chown {name}:{name} /home/{name}/check')
        account = guest('getent', 'passwd', name)
        definition = f'''[Unit]
Description=Disposable guest persistence verification
[Service]
Type=oneshot
ExecStart=/usr/bin/test -f /home/{name}/check
RemainAfterExit=yes
PrivateTmp=yes
ProtectSystem=strict
[Install]
WantedBy=multi-user.target
'''
        guest('tee', '/etc/systemd/system/' + unit, input=definition)
        service_created = True
        guest('systemctl', 'daemon-reload')
        guest('systemctl', 'enable', '--now', unit)
        assert guest('systemctl', 'is-active', unit) == 'active'
        control('restart')
        assert guest('systemctl', 'is-system-running') == 'running'
        assert guest('cat', '/etc/machine-id') == identity
        # Kernel boot_id is host-wide and intentionally not a guest identity check.
        assert guest('getent', 'passwd', name) == account
        assert guest('getent', 'passwd', 'operator') == original_operator
        assert guest('cat', f'/home/{name}/check') == 'persistent-test-data'
        assert guest('systemctl', 'is-active', unit) == 'active'
        control('stop')
        assert 'persistently disabled' in control('startup')
        assert 'Guest stopped; disabled' in control('status')
        control('start')
        assert guest('systemctl', 'is-system-running') == 'running'
        assert guest('systemctl', 'is-active', unit) == 'active'
        if os.geteuid() == 0:
            import json
            rows = [json.loads(line) for line in (state_dir/'events.jsonl').read_text().splitlines()]
            finished = [row for row in rows if row['event'] == 'invocation_finished']
            assert any(row['action'] == 'restart' and row['outcome'] == 'started' for row in finished)
            skipped = [row for row in finished if row['action'] == 'startup' and row['outcome'] == 'skipped_disabled']
            assert skipped, 'Persistent disabled startup must be explained in the log'
            launches = [row for row in rows if row['event'] == 'guest_started']
            assert launches, 'Actual guest readiness must be logged'
            ids = {row['run_id'] for row in launches}
            assert any(row['event'] == 'launcher_exec' and row['run_id'] in ids for row in rows), 'Worker events must correlate with boot request'
        print('PASS: systemd boot, protected unit, accounts/files, machine identity, restart and persistent stop')
    finally:
        # Return the test environment to an enabled running guest, preserving unrelated users.
        control('start')
        if service_created:
            guest('systemctl', 'disable', '--now', unit)
            guest('python3', '-c', 'from pathlib import Path; Path('+repr('/etc/systemd/system/'+unit)+').unlink(missing_ok=True)')
            guest('systemctl', 'daemon-reload')
        if account_created:
            guest('userdel', '--remove', name)

if __name__ == '__main__':
    main()
