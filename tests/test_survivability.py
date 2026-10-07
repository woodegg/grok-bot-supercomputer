import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('survivability', Path(__file__).resolve().parents[1] / 'scripts/survivability.py')
survival = importlib.util.module_from_spec(spec)
spec.loader.exec_module(survival)

class ComparisonTests(unittest.TestCase):
    def test_automatic_inspection_never_waits_for_backup_lock(self):
        with patch.object(survival.fcntl, 'flock', side_effect=BlockingIOError) as lock:
            self.assertFalse(survival.acquire_storage_lock(123, 'observe'))
            lock.assert_called_once_with(123, survival.fcntl.LOCK_EX | survival.fcntl.LOCK_NB)

    def test_manual_capture_uses_exclusive_blocking_lock(self):
        with patch.object(survival.fcntl, 'flock') as lock:
            self.assertTrue(survival.acquire_storage_lock(123, 'capture'))
            lock.assert_called_once_with(123, survival.fcntl.LOCK_EX)

    def baseline(self, live=False):
        file = dict(uid=1000, gid=1000, mode=0o600, size=123, allocated=4096, sha256='abc', uuid='uuid')
        return dict(mode='live' if live else 'offline', host={'boot_id': 'one'},
                    manifest_sha256='manifest', images={'image': dict(file, sha256=None if live else 'abc')},
                    probe=dict(file), inside_directory=dict(uid=0, gid=0, mode=0o700),
                    inside_file=dict(file, uid=0, gid=0), controls={'box-control': dict(file), 'root-control': dict(file, uid=0)})

    def test_unchanged_offline_is_complete(self):
        result = survival.compare(self.baseline(), self.baseline())
        self.assertFalse(result['deployment_mismatch'])
        self.assertTrue(result['complete_content_check'])
        self.assertEqual(result['host_observation'], 'unchanged')

    def test_hash_change_fails(self):
        after = self.baseline()
        after['images']['image']['sha256'] = 'different'
        self.assertTrue(survival.compare(self.baseline(), after)['deployment_mismatch'])

    def test_sparse_expansion_is_not_content_loss(self):
        after = self.baseline()
        after['images']['image']['allocated'] = 9999
        self.assertFalse(survival.compare(self.baseline(), after)['deployment_mismatch'])

    def test_live_content_is_unverified(self):
        result = survival.compare(self.baseline(True), self.baseline(True))
        self.assertFalse(result['complete_content_check'])
        self.assertFalse(result['deployment_mismatch'])

    def test_root_control_loss_is_separate_from_image_loss(self):
        after = self.baseline()
        after['controls']['root-control'] = {'missing': True}
        after['host']['boot_id'] = 'two'
        result = survival.compare(self.baseline(), after)
        self.assertFalse(result['deployment_mismatch'])
        self.assertEqual(result['host_observation'], 'changed')
        after['probe'] = {'missing': True}
        self.assertTrue(survival.compare(self.baseline(), after)['deployment_mismatch'])

    def test_permissions_are_part_of_consistency(self):
        after = self.baseline()
        after['inside_file']['mode'] = 0o644
        self.assertTrue(survival.compare(self.baseline(), after)['deployment_mismatch'])

    def test_timestamp_change_is_recorded_without_content_failure(self):
        before = self.baseline()
        before['probe']['mtime_ns'] = 100
        after = copy.deepcopy(before)
        after['probe']['mtime_ns'] = 200
        result = survival.compare(before, after)
        self.assertFalse(result['deployment_mismatch'])
        info = next(x for x in result['findings'] if x['item'] == 'probe/metadata')
        self.assertEqual(info['changes']['mtime_ns'], dict(before=100, after=200))

    def test_file_metadata_records_size_dates_and_inode(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'proof'
            path.write_bytes(b'contents')
            r = survival.file_record(path)
            self.assertEqual(r['size'], 8)
            self.assertEqual(r['mtime_ns'], path.stat().st_mtime_ns)
            self.assertEqual(r['inode'], path.stat().st_ino)
            self.assertIn('+00:00', r['mtime_utc'])
            self.assertIn('allocated', r)
            self.assertIsNone(r['birthtime_ns'])

    def test_evidence_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'baseline.json'
            survival.atomic_record(path, {'first': True}, os.getuid())
            with self.assertRaises(RuntimeError):
                survival.atomic_record(path, {'first': False}, os.getuid())
            self.assertEqual(json.loads(path.read_text()), {'first': True})

    def test_offline_refuses_attached_image(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'one.ext4').touch()
            with patch.object(survival, 'attached', return_value=True):
                with self.assertRaisesRegex(RuntimeError, 'ALL images detached'):
                    survival.snapshot(root, root, {'images': [{'name': 'one', 'file': 'one.ext4'}]}, 'offline')

@unittest.skipUnless(os.geteuid() == 0 and shutil.which('mkfs.ext4'), 'requires root and private mount namespace')
class ProbeTests(unittest.TestCase):
    def test_real_probe_preserves_hash_and_root_permissions(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = dict(owner_uid=os.getuid(), images=[])
            (root / 'manifest.json').write_text(json.dumps(manifest))
            survival.prepare(root, os.getuid())
            before = survival.snapshot(root, root, manifest, 'offline')
            after = survival.snapshot(root, root, manifest, 'offline')
            self.assertEqual(before['inside_directory']['mode'], 0o700)
            self.assertEqual(before['inside_file']['mode'], 0o600)
            self.assertEqual(before['inside_file']['uid'], 0)
            self.assertFalse(survival.compare(before, after)['deployment_mismatch'])
            with self.assertRaises(RuntimeError):
                survival.prepare(root, os.getuid())
