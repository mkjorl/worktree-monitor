import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

import dashboard
import preferences


class SettingsAPITests(unittest.TestCase):
    def test_activity_reports_queued_operations_without_docker_or_workspace_reads(self):
        server=dashboard.ThreadingHTTPServer(('127.0.0.1',0),dashboard.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        connection=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            with patch.object(dashboard,'snapshot',side_effect=AssertionError('Must not inspect workspaces')):
                for active in [set(),{'docker'},{'managed:test'},{'project:/tmp/demo'}]:
                    with patch.object(dashboard,'ACTIVE_NAMES',active):
                        connection.request('GET','/api/activity');response=connection.getresponse()
                        self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read()),{'busy':bool(active)})
                connection.request('GET','/api/activity',headers={'Host':'untrusted.local'})
                response=connection.getresponse();response.read();self.assertEqual(response.status,403)
        finally:
            connection.close();server.shutdown();server.server_close();worker.join()

    def test_settings_require_local_csrf_and_persist_only_valid_arrays(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(preferences, 'SETTINGS_PATH', Path(folder) / 'settings.json'):
            server = dashboard.ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            headers = {'Origin': f'http://127.0.0.1:{server.server_port}', 'X-CSRF-Token': dashboard.TOKEN}
            try:
                connection.request('GET', '/api/settings')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())['csrf'], dashboard.TOKEN)
                for body, request_headers, status in (
                    ({'directories': [folder]}, {}, 403),
                    ({'directories': 'not-an-array'}, headers, 400),
                    ({'directories': [folder]}, headers, 200),
                ):
                    connection.request('POST', '/api/settings', json.dumps(body), request_headers)
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, status)
                connection.request('GET', '/api/settings')
                response = connection.getresponse()
                self.assertEqual(json.loads(response.read())['directories'], [str(Path(folder).resolve())])
                connection.request('POST', '/api/settings', json.dumps({'directories': []}), headers)
                response = connection.getresponse()
                self.assertEqual(json.loads(response.read())['directories'], [])
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                worker.join()

    def test_appearance_persists_independently_and_is_rendered_before_first_paint(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(preferences, 'SETTINGS_PATH', Path(folder) / 'settings.json'):
            preferences.save_settings([folder])
            server = dashboard.ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            headers = {'Origin': f'http://127.0.0.1:{server.server_port}', 'X-CSRF-Token': dashboard.TOKEN}
            try:
                connection.request('POST', '/api/settings', json.dumps({'appearance': 'dark'}))
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                for mode in ('system', 'dark', 'light'):
                    connection.request('POST', '/api/settings', json.dumps({'appearance': mode}), headers)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    data = json.loads(response.read())
                    self.assertEqual(data['appearance'], mode)
                    self.assertEqual(data['directories'], [str(Path(folder).resolve())])
                    connection.request('GET', '/')
                    response = connection.getresponse()
                    self.assertIn(f'data-appearance="{mode}"', response.read().decode())
                for invalid in (None, [], {}, 'automatic', '<script>'):
                    connection.request('POST', '/api/settings', json.dumps({'appearance': invalid}), headers)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    response.read()
                    self.assertEqual(preferences.get_settings()['appearance'], 'light')
                connection.request('POST', '/api/settings', json.dumps({'directories': []}), headers)
                response = connection.getresponse()
                self.assertEqual(json.loads(response.read())['appearance'], 'light')
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                worker.join()
