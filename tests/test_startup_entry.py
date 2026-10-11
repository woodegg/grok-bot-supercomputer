import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('startup_entry', Path(__file__).resolve().parents[1] / 'scripts/startup-entry.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)

class StartupEntryTests(unittest.TestCase):
    def test_trigger_matches_last_argument_without_logging_arguments(self):
        self.assertEqual(entry.trigger_from(['--trigger', 'manual', '--trigger=scheduled']), 'scheduled')
        self.assertEqual(entry.trigger_from([]), 'bootstrap')
        self.assertEqual(entry.trigger_from(['--trigger', 'invalid-secret']), 'invalid')

    def test_records_are_private_and_rotation_retains_previous(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'entry'
            log = entry.EntryLog(path, os.getuid())
            log.emit('first', 'scheduled', 'entry_received')
            events = path / 'events.jsonl'
            self.assertEqual(events.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            with events.open('a') as stream:
                stream.write(' ' * (1024 * 1024))
            log.emit('second', 'scheduled', 'entry_received')
            self.assertTrue((path / 'events.jsonl.1').exists())
            self.assertEqual(json.loads(events.read_text())['run_id'], 'second')

    def test_symlink_log_is_refused(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)
            target = path / 'untouched'
            target.write_text('original')
            (path / 'events.jsonl').symlink_to(target)
            with self.assertRaises(RuntimeError):
                entry.EntryLog(path, os.getuid()).emit('one', 'scheduled', 'entry_received')
            self.assertEqual(target.read_text(), 'original')

    def run_wrapper(self, root, command=None):
        shutil.copyfile(Path(entry.__file__), root / 'startup-entry.py')
        if command:
            control = root / 'infractl'
            control.write_text(command)
            control.chmod(0o755)
        result = subprocess.run([sys.executable, str(root / 'startup-entry.py'), '--trigger', 'manual'],
                                capture_output=True, text=True)
        rows = [json.loads(s) for s in (root / 'startup-entry/events.jsonl').read_text().splitlines()]
        return result, rows

    def test_arrival_precedes_control_and_exit_code_is_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            command = '#!/bin/sh\n' + sys.executable + ' -c \'import json; from pathlib import Path; p=Path("' + name + '/startup-entry/events.jsonl"); assert json.loads(p.read_text().splitlines()[0])["event"] == "entry_received"\'\nexit 7\n'
            result, rows = self.run_wrapper(root, command)
            self.assertEqual(result.returncode, 7)
            self.assertEqual([r['event'] for r in rows], ['entry_received', 'control_spawned', 'entry_finished'])
            self.assertEqual(rows[-1]['exit_code'], 7)
            self.assertTrue(all(r['trigger'] == 'manual' for r in rows))
            self.assertEqual(len({r['run_id'] for r in rows}), 1)

    def test_missing_control_is_recorded_before_failure(self):
        with tempfile.TemporaryDirectory() as name:
            result, rows = self.run_wrapper(Path(name))
            self.assertEqual(result.returncode, 127)
            self.assertEqual([r['event'] for r in rows], ['entry_received', 'control_spawn_failed', 'entry_finished'])
