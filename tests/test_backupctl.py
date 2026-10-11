"""Configuration backup publication, scheduling, integrity and safe extraction."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('backupctl_tests', Path(__file__).resolve().parents[1] / 'scripts/backupctl.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='backup-catalog-check-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def generation(self, name='quick-one', complete=True):
        directory = self.root / name
        directory.mkdir()
        payload = directory / 'payload.tar.gz'
        with tarfile.open(payload, 'w:gz') as archive:
            item = tarfile.TarInfo('proof')
            data = b'preserved'
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        data = dict(kind='quick', created_epoch=100, hashes={'payload.tar.gz': hashlib.sha256(payload.read_bytes()).hexdigest()})
        (directory / 'metadata.json').write_text(json.dumps(data))
        if complete:
            (directory / 'COMPLETE').write_text('yes')
        return directory

    def test_incomplete_generation_is_not_a_recovery_point(self):
        directory = self.generation(complete=False)
        self.assertEqual(backup.completed(self.root), [])
        with self.assertRaises(RuntimeError):
            backup.verify_generation(directory)

    def test_corrupt_payload_is_refused(self):
        directory = self.generation()
        backup.verify_generation(directory)
        with (directory / 'payload.tar.gz').open('ab') as output:
            output.write(b'corruption')
        with self.assertRaisesRegex(RuntimeError, 'checksum differs'):
            backup.verify_generation(directory)

    def test_due_uses_completed_generation_time(self):
        self.generation()
        self.assertFalse(backup.due(self.root, 'quick', 300, now=399))
        self.assertTrue(backup.due(self.root, 'quick', 300, now=400))
        self.assertTrue(backup.due(self.root, 'checkpoint', 300, now=101))

    def test_extract_never_overwrites_existing_destination(self):
        directory = self.generation()
        existing = self.root / 'live'
        existing.mkdir()
        (existing / 'proof').write_text('live')
        with self.assertRaises(RuntimeError):
            backup.extract_generation(directory, existing)
        self.assertEqual((existing / 'proof').read_text(), 'live')

    def test_extract_to_new_candidate_keeps_contents(self):
        directory = self.generation()
        destination = self.root / 'candidate'
        with patch.object(backup, 'account', return_value=pwd.getpwuid(os.getuid())):
            backup.extract_generation(directory, destination)
        self.assertEqual((destination / 'proof').read_text(), 'preserved')

    def test_archive_cannot_write_outside_candidate(self):
        directory = self.generation()
        payload = directory / 'payload.tar.gz'
        with tarfile.open(payload, 'w:gz') as archive:
            item = tarfile.TarInfo('../escape')
            item.size = 1
            archive.addfile(item, io.BytesIO(b'x'))
        data = json.loads((directory / 'metadata.json').read_text())
        data['hashes']['payload.tar.gz'] = hashlib.sha256(payload.read_bytes()).hexdigest()
        (directory / 'metadata.json').write_text(json.dumps(data))
        with self.assertRaises(tarfile.FilterError):
            backup.extract_generation(directory, self.root / 'candidate')
        self.assertFalse((self.root / 'escape').exists())

class ConfigurationTests(unittest.TestCase):
    def test_config_capture_never_stops_or_copies_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            def query(*args):
                return '{}' if args[1] == '/1.0' else '[]'
            with patch.object(backup, 'command') as command, patch.object(backup, 'incus', side_effect=query):
                backup.capture_configs(stage, workspace=stage)
            calls = [list(map(str, call.args)) for call in command.call_args_list]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], 'tar')
            self.assertFalse(any('supervise' in arg or '/home/' in arg or '/var/lib/' in arg or arg.endswith('.ext4') for arg in calls[0]))
            data = json.loads((stage / 'incus-configuration.json').read_text())
            self.assertEqual(data['instances'], [])

    def test_old_policy_migrates_to_configuration_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy = Path(temporary) / 'policy.json'
            policy.write_text(json.dumps(dict(enabled=True, quick_interval_seconds=600, quick_keep=8, checkpoint_keep=3)))
            with patch.object(backup, 'POLICY', policy):
                result = backup.policy()
            self.assertEqual(result['config_interval_seconds'], 600)
            self.assertEqual(result['config_keep'], 8)
            self.assertNotIn('checkpoint_keep', result)
