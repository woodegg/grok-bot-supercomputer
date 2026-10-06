"""Backup publication, scheduling, corruption detection and real WAL recovery."""
import hashlib
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
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

@unittest.skipUnless(os.geteuid() == 0 and shutil.which('mkfs.ext4'), 'requires root and private mount namespace')
class FrozenSQLiteTests(unittest.TestCase):
    def test_frozen_image_wal_is_included_in_sqlite_backup(self):
        with tempfile.TemporaryDirectory(prefix='backup-wal-check-') as temporary:
            root = Path(temporary)
            image = root / 'source.ext4'
            with image.open('wb') as output:
                output.truncate(32 * 1024 * 1024)
            subprocess.run(['mkfs.ext4', '-q', '-F', str(image)], check=True)
            mounted = root / 'mounted'
            mounted.mkdir()
            subprocess.run(['mount', '-o', 'loop', str(image), str(mounted)], check=True)
            db = None
            frozen = False
            try:
                (mounted / 'payload').mkdir()
                db = sqlite3.connect(mounted / 'payload/app.sqlite')
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('PRAGMA wal_autocheckpoint=0')
                db.execute('CREATE TABLE proof(value TEXT)')
                db.execute("INSERT INTO proof VALUES('committed in WAL')")
                db.commit()
                self.assertTrue((mounted / 'payload/app.sqlite-wal').exists())
                subprocess.run(['/usr/sbin/fsfreeze', '-f', str(mounted)], check=True)
                frozen = True
                capture = root / 'capture'
                capture.mkdir()
                backup.copy_image(image, capture / 'home-test.ext4')
                subprocess.run(['/usr/sbin/fsfreeze', '-u', str(mounted)], check=True)
                frozen = False
                backup.sqlite_from_image(capture, 'home-test.ext4', 'payload')
                with contextlib.closing(sqlite3.connect(capture / 'sqlite/home-test/app.sqlite')) as restored:
                    self.assertEqual(restored.execute('SELECT value FROM proof').fetchone()[0], 'committed in WAL')
                    self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            finally:
                if frozen:
                    subprocess.run(['/usr/sbin/fsfreeze', '-u', str(mounted)], check=True)
                if db:
                    db.close()
                subprocess.run(['umount', str(mounted)], check=True)
