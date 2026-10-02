#!/usr/bin/python3
"""Preserve the approved no-exit-node, no-DNS, no-subnet-route baseline."""
import json
from pathlib import Path
import subprocess
import sys
import time

def check(value):
    if isinstance(value, dict):
        if (value.get('ExitNodeID') or value.get('ExitNodeIP') or
                value.get('CorpDNS') is True or value.get('RouteAll') is True):
            raise RuntimeError('Saved Tailscale routing preferences violate the approved baseline')
        for child in value.values():
            check(child)
    elif isinstance(value, list):
        for child in value:
            check(child)
    elif isinstance(value, str) and value.lstrip().startswith('{'):
        try:
            child = json.loads(value)
        except ValueError:
            return
        check(child)

def main():
    if sys.argv[1:] == ['check']:
        state = Path('/var/lib/tailscale/tailscaled.state')
        if state.exists():
            check(json.loads(state.read_text()))
        return
    if sys.argv[1:] != ['apply']:
        raise RuntimeError('Use check or apply')
    command = ['/usr/bin/tailscale', '--socket=/run/tailscale/tailscaled.sock']
    deadline = time.monotonic() + 20
    while True:
        result = subprocess.run(command + ['set', '--exit-node=', '--accept-dns=false',
                                          '--accept-routes=false'],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            break
        if time.monotonic() > deadline:
            raise RuntimeError('Tailscale API did not accept the approved baseline')
        time.sleep(.2)
    prefs = json.loads(subprocess.check_output(command + ['debug', 'prefs'], text=True, timeout=5))
    check(prefs)
    if prefs.get('CorpDNS') is not False or prefs.get('RouteAll') is not False:
        raise RuntimeError('Tailscale did not apply the approved baseline')

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
