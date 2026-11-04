import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import project_runtime as runtime


class ProjectRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        for name, directory in [('RECORDS', 'records'), ('LOGS', 'logs')]:
            patcher = patch.object(runtime, name, self.root / directory)
            patcher.start()
            self.addCleanup(patcher.stop)
        runtime._CHILDREN.clear()
        self.identity = {'uid': os.getuid(), 'pgid': 9876, 'started': 'Mon Oct 19 12:00:00 2026', 'command': 'python app.py'}

    def record(self, **changes):
        _, file, _ = runtime._paths(self.project)
        file.parent.mkdir(exist_ok=True)
        file.write_text(json.dumps({'path': str(self.project), 'pid': 9876, **self.identity, **changes}))
        return file

    def test_command_is_parsed_without_shell(self):
        self.assertEqual(runtime.validate_command('python "my app.py" --port 8765'), ['python', 'my app.py', '--port', '8765'])
        for command in ('', 'python app.py | cat', 'python app.py; echo hi', 'PORT=1 python app.py', 'bash -c ls', '/bin/zsh -c ls', 'x\0y', 'x' * 4097):
            with self.subTest(command=command), self.assertRaises(ValueError):
                runtime.validate_command(command)

    def test_start_persists_ownership_and_uses_no_shell(self):
        child = Mock(pid=9876)
        child.poll.return_value = None
        with patch.object(runtime.subprocess, 'Popen', return_value=child) as popen, patch.object(runtime, '_identity', return_value=self.identity):
            runtime.start_project(self.project, 'python "my app.py"')
        _, file, log = runtime._paths(self.project)
        self.assertEqual(json.loads(file.read_text())['pid'], 9876)
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertTrue(log.exists())
        self.assertEqual(popen.call_args.args[0], ['python', 'my app.py'])
        self.assertEqual(popen.call_args.kwargs['cwd'], self.project)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertNotIn('shell', popen.call_args.kwargs)
        with patch.object(runtime, '_identity', return_value=self.identity), self.assertRaisesRegex(ValueError, 'already running'):
            runtime.start_project(self.project, 'python app.py')

    def test_failed_identity_cleans_up_fresh_child(self):
        child = Mock(pid=9876)
        child.poll.return_value = None
        failure = runtime.subprocess.TimeoutExpired('ps', 3)
        with patch.object(runtime.subprocess, 'Popen', return_value=child), patch.object(runtime, '_identity', side_effect=failure), patch.object(runtime.os, 'getpgid', side_effect=OSError('unavailable')):
            with self.assertRaises(runtime.subprocess.TimeoutExpired):
                runtime.start_project(self.project, 'python app.py')
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=2)
        self.assertNotIn(9876, runtime._CHILDREN)
        _, file, _ = runtime._paths(self.project)
        self.assertFalse(file.exists())
        self.assertFalse(file.with_suffix('.tmp').exists())

    def test_stale_or_foreign_identity_is_never_killed(self):
        self.record()
        for identity in (None, self.identity | {'started': 'different'}, self.identity | {'uid': os.getuid() + 1}, self.identity | {'pgid': 333}, self.identity | {'command': 'unrelated'}):
            with self.subTest(identity=identity), patch.object(runtime, '_identity', return_value=identity), patch.object(runtime.os, 'killpg') as kill:
                self.assertFalse(runtime.inspect_project(self.project)['running'])
                with self.assertRaisesRegex(ValueError, 'exited or changed'):
                    runtime.stop_project(self.project)
                kill.assert_not_called()

    def test_malformed_record_and_missing_record_do_not_kill(self):
        with patch.object(runtime.os, 'killpg') as kill:
            with self.assertRaisesRegex(ValueError, 'no process'):
                runtime.stop_project(self.project)
            file = self.record(uid=os.getuid() + 1)
            self.assertFalse(runtime.inspect_project(self.project)['running'])
            with self.assertRaisesRegex(ValueError, 'Invalid'):
                runtime.stop_project(self.project)
            file.write_text('[]')
            with self.assertRaises(ValueError):
                runtime.start_project(self.project, 'python app.py')
            kill.assert_not_called()

    def test_verified_group_stop_and_group_resource_totals(self):
        self.record()
        stats = SimpleNamespace(stdout=f'9876 {os.getuid()} 3.5 2048\n9876 {os.getuid()} 1.0 1024\n9999 {os.getuid()} 60 99999\n9876 {os.getuid()+1} 80 99999\n')
        with patch.object(runtime, '_identity', return_value=self.identity), patch.object(runtime.subprocess, 'run', return_value=stats):
            result = runtime.inspect_project(self.project)
            self.assertTrue(result['running'])
            self.assertEqual(result['cpu_percent'], 4.5)
            self.assertEqual(result['memory_mb'], 3)
        with patch.object(runtime, '_identity', side_effect=[self.identity, None]), patch.object(runtime.os, 'killpg') as kill:
            runtime.stop_project(self.project)
            kill.assert_called_once_with(9876, runtime.signal.SIGTERM)

    def test_ps_identity_requires_full_start_time_and_live_process(self):
        output = f'{os.getuid()} 9876 Mon Oct 19 12:00:00 2026 S python app.py\n'
        with patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=output)) as ps:
            self.assertEqual(runtime._identity(9876), self.identity)
            self.assertEqual(ps.call_args.args[0][:4], ['ps', '-ww', '-p', '9876'])
        with patch.object(runtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=output.replace(' S ', ' Z '))):
            self.assertIsNone(runtime._identity(9876))


if __name__ == '__main__':
    unittest.main()
