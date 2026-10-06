"""Real disposable ext4 checks; run as root in a private mount namespace."""
import importlib.util
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('image_storage', Path(__file__).resolve().parents[1] / 'scripts/image-storage.py')
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)

@unittest.skipUnless(os.geteuid() == 0 and shutil.which('mkfs.ext4'), 'requires root and ext4 tools; use unshare --mount')
class ImageStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='image-storage-check-')
        self.directory = Path(self.temp.name)
        self.manifest = {'owner_uid': os.getuid(), 'images': []}

    def tearDown(self):
        for entry in reversed(self.manifest['images']):
            for path in [Path(entry['target']), self.directory / 'mounts' / entry['name']]:
                if storage.mount_info(path):
                    subprocess.run(['umount', str(path)], check=True)
        self.temp.cleanup()

    def image(self, name, readonly=False):
        image = self.directory / (name + '.ext4')
        with image.open('xb') as output:
            output.truncate(32 * 1024 * 1024)
        subprocess.run(['mkfs.ext4', '-q', '-F', str(image)], check=True)
        uuid = subprocess.check_output(['blkid', '-p', '-s', 'UUID', '-o', 'value', str(image)], text=True).strip()
        mounted = self.directory / 'mounts' / name
        mounted.mkdir(parents=True)
        subprocess.run(['mount', '-o', 'loop', str(image), str(mounted)], check=True)
        (mounted / 'payload').mkdir()
        (mounted / 'payload/proof').write_text('original')
        subprocess.run(['umount', str(mounted)], check=True)
        entry = dict(name=name, file=image.name, uuid=uuid, payload='payload', target=str(self.directory / ('target-' + name)), readonly=readonly)
        self.manifest['images'].append(entry)
        return entry

    def test_idempotent_mount_preserves_writes(self):
        entry = self.image('one')
        storage.ensure(self.manifest, self.directory)
        proof = Path(entry['target']) / 'proof'
        proof.write_text('changed')
        self.assertEqual(storage.ensure(self.manifest, self.directory), [])
        self.assertEqual(proof.read_text(), 'changed')

    def test_missing_image_refuses_before_any_mount(self):
        self.image('one')
        entry = self.image('two')
        (self.directory / entry['file']).unlink()
        with self.assertRaises(RuntimeError):
            storage.ensure(self.manifest, self.directory)
        self.assertIsNone(storage.mount_info(self.directory / 'mounts/one'))

    def test_failure_rolls_back_only_new_mounts(self):
        self.image('one')
        entry = self.image('two')
        entry['payload'] = 'missing'
        with self.assertRaises(RuntimeError):
            storage.ensure(self.manifest, self.directory)
        self.assertIsNone(storage.mount_info(self.directory / 'mounts/one'))
        self.assertIsNone(storage.mount_info(self.directory / 'mounts/two'))

    def test_readonly_base_refuses_write(self):
        entry = self.image('base', readonly=True)
        storage.ensure(self.manifest, self.directory)
        with self.assertRaises(OSError):
            (Path(entry['target']) / 'proof').write_text('changed')

    def test_uuid_mismatch_refuses_mount(self):
        entry = self.image('one')
        entry['uuid'] = 'wrong'
        with self.assertRaises(RuntimeError):
            storage.ensure(self.manifest, self.directory)
        self.assertIsNone(storage.mount_info(self.directory / 'mounts/one'))

    def test_changed_readonly_image_refuses_mount(self):
        entry = self.image('base', readonly=True)
        image = self.directory / entry['file']
        with image.open('rb') as source:
            entry['sha256'] = hashlib.file_digest(source, 'sha256').hexdigest()
        with image.open('r+b') as output:
            output.seek(-1, 2)
            output.write(b'\x01')
        with self.assertRaisesRegex(RuntimeError, 'checksum differs'):
            storage.ensure(self.manifest, self.directory)
        self.assertIsNone(storage.mount_info(self.directory / 'mounts/base'))

    def test_interrupted_backup_freeze_is_recovered(self):
        entry = self.image('home')
        storage.ensure(self.manifest, self.directory)
        import json
        (self.directory / 'manifest.json').write_text(json.dumps(self.manifest))
        (self.directory / 'image-storage.py').write_text((Path(__file__).resolve().parents[1] / 'scripts/image-storage.py').read_text())
        backup_spec = importlib.util.spec_from_file_location('backup_freeze_recovery', Path(__file__).resolve().parents[1] / 'scripts/backupctl.py')
        backup = importlib.util.module_from_spec(backup_spec)
        backup_spec.loader.exec_module(backup)
        record = dict(pid=99999999, boot='previous-boot', ticks='0', freeze_intents=['home'], pause_intents=[])
        (self.directory / 'backup-maintenance.json').write_text(json.dumps(record))
        subprocess.run(['/usr/sbin/fsfreeze', '-f', entry['target']], check=True)
        try:
            backup.recover_storage(self.manifest, self.directory)
            subprocess.run(['timeout', '3', 'python3', '-c', 'from pathlib import Path; Path(' + repr(entry['target'] + '/proof') + ').write_text("recovered")'], check=True)
            self.assertEqual((Path(entry['target']) / 'proof').read_text(), 'recovered')
            recovered = json.loads((self.directory / 'backup-maintenance.json').read_text())
            self.assertEqual(recovered['freeze_intents'], [])
        finally:
            subprocess.run(['/usr/sbin/fsfreeze', '-u', entry['target']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
