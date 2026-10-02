#!/usr/bin/python3
"""Runit foreground owner for a persistent nspawn environment."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

config_path, controller_dir, service_path, sv = sys.argv[1:]
sys.path.insert(0, controller_dir)
from containerctl import configuration, manager_pid, start, stop
from lifecycle_events import EventLog

os.umask(0o077)
config = configuration(Path(config_path))
events = EventLog(config, 'startup', 'bootstrap')
stopping = False
def request_stop(signum, frame):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)
events.emit('invocation_started', owner='runit', config_path=config_path)
try:
    outcome = start(config, Path(config_path), automatic=True, events=events)
    events.finish(outcome)
    if outcome == 'skipped_disabled':
        subprocess.run([sv, 'down', service_path], check=True)
    else:
        while not stopping and manager_pid(config):
            time.sleep(.25)
        if stopping:
            stop(config, persistent=False, events=events)
        elif (config['state_dir']/'disabled').exists():
            subprocess.run([sv, 'down', service_path], check=True)
        else:
            raise RuntimeError('Guest manager exited unexpectedly; runit will retry')
except Exception as error:
    events.finish('failed', error)
    print(error, file=sys.stderr)
    # Avoid rapid retry loops for unavailable prerequisites or a degraded guest.
    deadline=time.monotonic()+5
    while not stopping and time.monotonic()<deadline:
        time.sleep(.2)
    raise SystemExit(1)
