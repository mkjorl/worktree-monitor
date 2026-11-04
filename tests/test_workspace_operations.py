from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import workspace_operations as operations


class WorkspaceOperationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for key in ('OPERATIONS', 'LOGS'):
            patched = patch.object(operations, key, self.root / key)
            patched.start()
            self.addCleanup(patched.stop)
        self.module = SimpleNamespace(validate_project_name=lambda name: name, state_root=lambda: self.root)
        @contextmanager
        def locked_project(name, blocking=True):
            with (self.root / ('.' + name + '.operation.lock')).open('a+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                yield
        self.module.locked_project = locked_project
        self.identity = {'uid': os.getuid(), 'pgid': os.getpid(), 'started': 'test-start', 'command': 'test-worker'}
        patched = patch.object(operations, '_identity', return_value=self.identity)
        patched.start()
        self.addCleanup(patched.stop)

    def test_persistent_status_survives_new_reader_and_stale_pid_is_detected(self):
        job = 'a' * 32
        self.module.stop_state = lambda state: None
        def destroy(name, cleanup_orphans):
            self.assertFalse(cleanup_orphans)
            with self.module.locked_project(name):
                self.module.stop_state({})
                status = operations.operation_for(self.module, self.root, name)
                self.assertTrue(status['busy'])
                self.assertEqual(status['stage'], 'Stopping workspace services.')
                self.assertEqual(operations.read_job(job)['state'], 'running')
        self.module.destroy_command = destroy
        original_stop = self.module.stop_state
        operations.run_tracked(self.module, self.root, 'destroy', 'demo', job)
        self.assertIs(self.module.stop_state, original_stop)
        self.assertEqual(operations.read_job(job)['state'], 'done')
        path = operations._path(self.root, 'demo')
        record = json.loads(path.read_text())
        record['state'] = 'running'
        path.write_text(json.dumps(record))
        with patch.object(operations, '_identity', return_value=None):
            self.assertFalse(operations.operation_for(self.module, self.root, 'demo')['busy'])
            self.assertIn('interrupted', operations.read_job(job)['message'])

    def test_lock_from_another_worker_is_busy_and_retry_does_not_replace_owner(self):
        owner = {'job': 'b' * 32, 'state': 'running', 'stage': 'Removing workspace files.',
                 'pid': os.getpid(), 'identity': self.identity}
        path = operations._path(self.root, 'demo')
        operations._write(path, owner)
        self.module.destroy_command = lambda name, cleanup_orphans: self._attempt_lock(name)
        with self.module.locked_project('demo'):
            self.assertTrue(operations.operation_for(self.module, self.root, 'demo')['busy'])
            with self.assertRaisesRegex(RuntimeError, 'already in progress'):
                operations.run_tracked(self.module, self.root, 'destroy', 'demo', 'c' * 32)
        self.assertEqual(json.loads(path.read_text()), owner)

    def _attempt_lock(self, name):
        with self.module.locked_project(name):
            self.fail('A contended lock must not be acquired')

    def test_failure_is_persisted_and_callbacks_restored(self):
        original = self.module.locked_project
        def stop(name):
            with self.module.locked_project(name):
                raise RuntimeError('private error details')
        self.module.stop_command = stop
        with self.assertRaises(RuntimeError):
            operations.run_tracked(self.module, self.root, 'stop', 'demo', 'd' * 32)
        self.assertIs(self.module.locked_project, original)
        result = operations.read_job('d' * 32)
        self.assertEqual(result['state'], 'error')
        self.assertNotIn('private error', result['message'])

    def test_start_reports_action_and_readiness_phase(self):
        self.module.health_check = lambda: None
        def start(name):
            with self.module.locked_project(name):
                self.module.health_check()
                status = operations.operation_for(self.module, self.root, name)
                self.assertEqual(status['action'], 'start')
                self.assertEqual(status['stage'], 'Waiting for workspace services to become ready.')
        self.module.start_command = start
        operations.run_tracked(self.module, self.root, 'start', 'demo', 'e' * 32)
        with self.module.locked_project('demo'):
            status = operations.operation_for(self.module, self.root, 'demo')
            self.assertTrue(status['busy'])
            self.assertIsNone(status['action'])
            self.assertIn('already in progress', status['stage'])

    def test_reset_reuses_restart_and_tracks_phases_without_cleanup(self):
        calls = []
        state = {'name': 'demo', 'code': 'uncommitted work', 'database': 'existing data'}
        self.module.stop_state = lambda value: calls.append('stop')
        def health(value):
            calls.append('health')
            status = operations.operation_for(self.module, self.root, 'demo')
            self.assertTrue(status['busy'])
            self.assertEqual(status['action'], 'reset')
            self.assertIn('Waiting', status['stage'])
        self.module.health_check = health
        def start(value):
            calls.append('start')
            self.module.health_check(value)
        self.module.start_project = start
        def restart(name):
            with self.module.locked_project(name):
                self.module.stop_state(state)
                self.module.start_project(state)
        self.module.restart_command = restart
        operations.run_tracked(self.module, self.root, 'reset', 'demo', 'f' * 32)
        self.assertEqual(calls, ['stop', 'start', 'health'])
        self.assertEqual(state, {'name': 'demo', 'code': 'uncommitted work', 'database': 'existing data'})
        self.assertEqual(operations.read_job('f' * 32)['state'], 'done')
        self.module.stop_state = lambda value: (_ for _ in ()).throw(RuntimeError('stop failed'))
        with self.assertRaisesRegex(RuntimeError, 'stop failed'):
            operations.run_tracked(self.module, self.root, 'reset', 'demo', '1' * 32)
        self.assertEqual(calls, ['stop', 'start', 'health'])
        self.assertEqual(operations.read_job('1' * 32)['state'], 'error')

    def test_invalid_job_identifier_cannot_address_files(self):
        self.assertIsNone(operations.read_job('../outside'))
        with self.assertRaises(ValueError):
            operations.log_path('../outside')
