"""Check generic probes and private guard configuration without changing networking."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parent.parent / 'scripts/initialize-incus.py'
spec = importlib.util.spec_from_file_location('initialize_incus', SOURCE)
initializer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(initializer)


class ProbeTests(unittest.TestCase):
    def test_default_uses_public_endpoint(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(initializer.probe_hosts(), ('deb.debian.org',))

    def test_local_endpoint_list_and_empty_entries(self):
        with patch.dict(os.environ, {'INFRA_PROBE_HOSTS':
                                    ' deb.debian.org, management.example.invalid '}):
            self.assertEqual(initializer.probe_hosts(),
                             ('deb.debian.org', 'management.example.invalid'))
        for value in ('', ',', 'deb.debian.org,'):
            with patch.dict(os.environ, {'INFRA_PROBE_HOSTS': value}):
                with self.assertRaises(ValueError):
                    initializer.probe_hosts()

    def test_connectivity_checks_each_configured_endpoint(self):
        hosts = ('deb.debian.org', 'management.example.invalid')
        with patch.object(initializer.subprocess, 'check_output', return_value='route'), \
             patch.object(initializer.socket, 'setdefaulttimeout'), \
             patch.object(initializer.socket, 'getaddrinfo'), \
             patch.object(initializer.socket, 'create_connection') as connect:
            self.assertTrue(initializer.healthy('route', hosts))
            self.assertEqual([call.args[0] for call in connect.call_args_list],
                             [(host, 443) for host in hosts])

    def test_route_change_or_probe_failure_rejects_connectivity(self):
        with patch.object(initializer.subprocess, 'check_output', return_value='changed'), \
             patch.object(initializer.socket, 'create_connection') as connect:
            self.assertFalse(initializer.healthy('original', ('deb.debian.org',)))
            connect.assert_not_called()
        with patch.object(initializer.subprocess, 'check_output', return_value='route'), \
             patch.object(initializer.socket, 'setdefaulttimeout'), \
             patch.object(initializer.socket, 'getaddrinfo'), \
             patch.object(initializer.socket, 'create_connection', side_effect=OSError):
            self.assertFalse(initializer.healthy('route', ('deb.debian.org',)))

    def test_detached_guard_uses_saved_probes_and_stops_after_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guard.json'
            hosts = ['management.example.invalid']
            path.write_text(json.dumps({'default': 'route', 'probe_hosts': hosts}))
            def commit(*args):
                path.with_suffix('.committed').touch()
                return True
            with patch.object(initializer, 'healthy', side_effect=commit) as healthy, \
                 patch.object(initializer.time, 'sleep'), \
                 patch.object(initializer.subprocess, 'run') as run:
                initializer.guard(path)
                healthy.assert_called_once_with('route', hosts)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
