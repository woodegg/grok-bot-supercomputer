"""Exercise reset inference, concurrent log writers, privacy and bounded storage."""
import contextlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from lifecycle_events import EventLog
import lifecycle_events
import containerctl

IDENTITY = {'kernel_boot_id': 'kernel-one', 'pid1_start_ticks': '100',
            'pid_namespace': 'pid:[1]', 'root_device_inode': '1:2',
            'root_mount_fingerprint': 'mount-one'}

def writer(directory, count):
    logger = EventLog({'state_dir': Path(directory), 'machine': 'test'}, 'startup', 'scheduled')
    for index in range(count):
        logger.emit('concurrent_check', index=index)

class EventsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {'state_dir': self.root / 'state', 'machine': 'test'}

    def logger(self, **options):
        return EventLog(self.config, 'startup', 'scheduled', marker_dir=self.root / 'outer', **options)

    def rows(self):
        return [json.loads(line) for line in (self.config['state_dir'] / 'events.jsonl').read_text().splitlines()]

    def observe(self, identity=IDENTITY):
        with patch.object(lifecycle_events, 'outer_identity', return_value=dict(identity)):
            self.logger().observe_environment()

    def test_first_run_is_baseline_not_a_claimed_reset(self):
        self.observe()
        self.observe()
        self.assertEqual([row['event'] for row in self.rows()], ['environment_baseline'])
        self.assertEqual((self.root/'outer/test.marker').stat().st_mode & 0o777, 0o600)

    def test_marker_loss_is_suspected_reset_once(self):
        self.observe()
        (self.root/'outer/test.marker').unlink()
        self.observe()
        self.observe()
        resets = [row for row in self.rows() if row['event'] == 'outer_root_reset_suspected']
        self.assertEqual(len(resets), 1)
        self.assertEqual(resets[0]['confidence'], 'suspected')
        self.assertEqual(resets[0]['evidence'], 'root_marker_missing')

    def test_outer_restart_can_happen_without_kernel_reboot(self):
        self.observe()
        changed = {**IDENTITY, 'pid1_start_ticks': '200', 'pid_namespace': 'pid:[2]'}
        self.observe(changed)
        names = [row['event'] for row in self.rows()]
        self.assertIn('outer_container_changed', names)
        self.assertNotIn('kernel_boot_changed', names)
        self.assertNotIn('outer_root_reset_suspected', names)

    def test_kernel_and_root_identity_changes_have_separate_evidence(self):
        self.observe()
        self.observe({**IDENTITY, 'kernel_boot_id': 'kernel-two', 'root_mount_fingerprint': 'mount-two'})
        names = [row['event'] for row in self.rows()]
        self.assertIn('kernel_boot_changed', names)
        self.assertIn('outer_root_identity_changed', names)
        self.assertNotIn('outer_root_reset_suspected', names)

    def test_inaccessible_marker_does_not_claim_reset_or_block_logging(self):
        self.observe()
        logger = self.logger()
        original_read = Path.read_text
        def read(path, *args, **kwargs):
            if path == logger.marker_dir / 'test.marker':
                raise PermissionError('read-only outer root')
            return original_read(path, *args, **kwargs)
        with patch.object(lifecycle_events, 'outer_identity', return_value=dict(IDENTITY)), \
             patch.object(Path, 'read_text', new=read):
            logger.observe_environment()
        names = [row['event'] for row in self.rows()]
        self.assertIn('root_marker_unavailable', names)
        self.assertNotIn('outer_root_reset_suspected', names)
        self.observe()
        self.assertNotIn('outer_root_reset_suspected', [row['event'] for row in self.rows()])

    def test_invalid_saved_observation_establishes_baseline_without_blocking(self):
        for value in ('[]', '{"root_marker": 123}'):
            with self.subTest(value=value):
                self.observe()
                (self.config['state_dir']/'outer-observation.json').write_text(value)
                self.observe()
                self.assertEqual([row['event'] for row in self.rows()][-2:],
                                 ['observation_unreadable', 'environment_baseline'])
                self.assertNotIn('outer_root_reset_suspected', [row['event'] for row in self.rows()])

    def test_log_rotation_permissions_and_no_environment_or_argv_dump(self):
        with patch.dict(os.environ, {'PRIVATE_TOKEN': 'secret-should-never-appear',
                                      'SUDO_USER': 'another-secret-should-never-appear', 'SUDO_UID': 'invalid'}), \
             patch.object(sys, 'argv', ['tool', '--token', 'secret-should-never-appear']):
            logger = self.logger(max_bytes=700, backups=2)
            for index in range(10):
                logger.emit('check', index=index)
        paths = list(self.config['state_dir'].glob('events.jsonl*'))
        self.assertLessEqual(len(paths), 3)
        for path in paths:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('secret-should-never-appear', path.read_text())
            for line in path.read_text().splitlines():
                row = json.loads(line)
                self.assertTrue(row['timestamp'].endswith('Z'))

    def test_concurrent_writers_produce_complete_records(self):
        context = multiprocessing.get_context('fork')
        processes = [context.Process(target=writer, args=(str(self.config['state_dir']), 30)) for _ in range(3)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(self.rows()), 90)
        self.assertEqual(len({row['run_id'] for row in self.rows()}), 3)

    def test_readable_view_keeps_recent_events_across_rotation(self):
        logger = self.logger(max_bytes=500, backups=5)
        for index in range(8):
            logger.emit('check_' + str(index))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            lifecycle_events.show_events(self.config['state_dir'], 3)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        for index, line in zip(range(5, 8), lines):
            self.assertIn('check_' + str(index), line)
            self.assertIn('startup[scheduled]', line)

    def test_disabled_guest_check_does_not_enter_or_start_it(self):
        state = self.config['state_dir']
        state.mkdir()
        (state / 'disabled').touch()
        logger = self.logger()
        with patch.object(containerctl, 'manager_pid', side_effect=AssertionError('Must not inspect a disabled guest')):
            outcome = containerctl.start(self.config, self.root/'config.toml', automatic=True, events=logger)
        self.assertEqual(outcome, 'skipped_disabled')
        self.assertEqual(self.rows()[-1]['reason'], 'persistently_disabled')

    def test_unhealthy_guest_is_reported_without_automatic_restart(self):
        logger = self.logger()
        response = subprocess.CompletedProcess([], 1, stdout='degraded\n', stderr='')
        with patch.object(containerctl, 'manager_pid', return_value=123), \
             patch.object(containerctl, 'guest_run', return_value=response), \
             patch.object(containerctl.subprocess, 'Popen', side_effect=AssertionError('Must not restart a degraded guest')):
            with self.assertRaisesRegex(RuntimeError, 'not healthy'):
                containerctl.start(self.config, self.root/'config.toml', automatic=True, events=logger)
        self.assertEqual(self.rows()[-1]['event'], 'guest_unhealthy')

if __name__ == '__main__':
    unittest.main()
