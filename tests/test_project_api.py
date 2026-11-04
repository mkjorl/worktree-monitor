import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

import dashboard
import project_config


class ProjectAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'demo'
        self.project.mkdir()
        self.metadata = {'id': str(self.project), 'path': str(self.project), 'name': 'demo', 'branch': 'main', 'kind': 'repository'}
        for mocked in (
            patch.object(dashboard, 'git_projects', return_value={'projects': [self.metadata], 'warnings': []}),
            patch.object(dashboard.lifecycle, 'load_all_states', return_value=[]),
            patch.object(project_config, 'PROJECTS_PATH', self.root / 'profiles.json'),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_configuration_validation_and_persistence(self):
        profile = project_config.save_project(str(self.project), 'http://localhost:3000/', 'python3 -m http.server 3000')
        self.assertEqual(project_config.get_project(str(self.project)), profile)
        for url, command in [('javascript:alert(1)', ''), ('https://user:secret@example.com', ''), ('http://localhost:bad', ''), ('', 'npm run dev && rm -rf example'), ('', 'bash -c echo')]:
            with self.subTest(url=url, command=command), self.assertRaises(ValueError):
                project_config.save_project(str(self.project), url, command)
        self.assertEqual(project_config.get_project(str(self.project)), profile)
        project_config.save_project(str(self.project), '', '')
        self.assertEqual(project_config.load_projects(), {})

    def test_authority_excludes_outside_paths_and_managed_descendants(self):
        self.assertEqual(dashboard.require_project(str(self.project)), self.metadata)
        with self.assertRaises(ValueError):
            dashboard.require_project(str(self.root))
        with patch.object(dashboard.lifecycle, 'load_all_states', return_value=[{'project_root': str(self.root)}]):
            with self.assertRaises(ValueError):
                dashboard.require_project(str(self.project))

    def test_snapshot_unknown_usage_is_not_zero_and_managed_rows_are_not_duplicated(self):
        with patch.object(dashboard, 'visible_states', return_value=[]), \
             patch.object(dashboard, 'docker_data', return_value={'status': 'available', 'containers': []}), \
             patch.object(dashboard, 'inspect_project', return_value={'running': False, 'cpu_percent': 0, 'memory_mb': 0}):
            row = dashboard.snapshot()['workspaces'][0]
            self.assertFalse(row['managed'])
            self.assertIsNone(row['cpu_percent'])
            self.assertIsNone(row['memory_mb'])
            self.assertFalse(row['can_start'])
            self.assertFalse(row['can_destroy'])
            with patch.object(dashboard.lifecycle, 'load_all_states', return_value=[{'project_root': str(self.root)}]):
                self.assertEqual(dashboard.snapshot()['workspaces'], [])

    def test_http_project_controls_require_csrf_discovery_and_configuration(self):
        server = dashboard.ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        headers = {'Origin': f'http://127.0.0.1:{server.server_port}', 'X-CSRF-Token': dashboard.TOKEN}
        def post(endpoint, body, auth=headers):
            connection.request('POST', endpoint, json.dumps(body), auth)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        try:
            body = {'path': str(self.project), 'url': 'http://localhost:3000', 'command': 'python3 -m http.server 3000'}
            self.assertEqual(post('/api/project', body, {})[0], 403)
            self.assertEqual(post('/api/action', {'action': 'start', 'project': str(self.project)})[0], 400)
            self.assertEqual(post('/api/project', body)[0], 200)
            with patch.object(dashboard.POOL, 'submit', side_effect=lambda fn: fn()), \
                 patch.object(dashboard, 'start_project') as start:
                status, result = post('/api/action', {'action': 'start', 'project': str(self.project)})
                self.assertEqual(status, 202)
                self.assertEqual(dashboard.JOBS[result['job']]['state'], 'done')
                start.assert_called_once_with(str(self.project), body['command'])
            for action, path in [('destroy', self.project), ('start', self.root)]:
                self.assertEqual(post('/api/action', {'action': action, 'project': str(path)})[0], 400)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join()
