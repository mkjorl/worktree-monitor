"""Persist managed operation progress while the owning workspace lock stays held."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import subprocess
import time

from project_runtime import _identity

OPERATIONS = Path.home() / 'Library/Application Support/Worktree Monitor/operations'
LOGS = Path.home() / 'Library/Logs/Worktree Monitor'


def _path(checkout, name):
    key = hashlib.sha256((str(Path(checkout).resolve()) + '\0' + name).encode()).hexdigest()
    return OPERATIONS / (key + '.json')


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def log_path(job_id):
    if not re.fullmatch('[a-f0-9]{32}', job_id):
        raise ValueError('Invalid operation identifier')
    LOGS.mkdir(parents=True, exist_ok=True)
    return LOGS / ('workspace-' + job_id + '.log')


def lock_busy(module, name):
    module.validate_project_name(name)
    path = module.state_root() / ('.' + name + '.operation.lock')
    try:
        with path.open('r') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(lock, fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    return False


def _alive(data):
    pid, identity = data.get('pid'), data.get('identity')
    if type(pid) is not int or pid <= 1 or not isinstance(identity, dict) or identity.get('uid') != os.getuid():
        return False
    try:
        return _identity(pid) == identity
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False


def operation_for(module, checkout, name):
    data = _read(_path(checkout, name))
    active = data.get('state') == 'running' and _alive(data)
    busy = lock_busy(module, name) or active
    if busy:
        return {'busy': True, 'status': 'running', 'action': data.get('action') if active else None,
                'stage': data.get('stage') if active else 'Workspace operation is already in progress.'}
    if data.get('state') == 'running':
        return {'busy': False, 'status': 'error', 'stage': 'Previous operation was interrupted. You can retry.'}
    return {'busy': False, 'status': data.get('state', ''), 'stage': data.get('stage', '')}


def read_job(job_id):
    if not re.fullmatch('[a-f0-9]{32}', job_id):
        return None
    for path in OPERATIONS.glob('*.json'):
        data = _read(path)
        if data.get('job') == job_id:
            state = data.get('state', 'error')
            if state == 'running' and not _alive(data):
                return {'state': 'error', 'message': 'Operation interrupted; refresh workspace status before retrying.'}
            return {'state': state, 'message': data.get('stage', 'Workspace operation in progress.')}
    return None


def run_tracked(module, checkout, action, name, job_id):
    if action not in {'start', 'stop', 'reset', 'destroy'}:
        raise ValueError('Invalid workspace action')
    module.validate_project_name(name)
    log_path(job_id)
    path = _path(checkout, name)
    original_lock = module.locked_project
    original_functions = {}
    initial_stage = {'start': 'Starting workspace.', 'stop': 'Stopping workspace.', 'destroy': 'Deleting workspace.', 'reset': 'Resetting workspace services.'}[action]
    record = {'job': job_id, 'action': action, 'pid': os.getpid(), 'identity': _identity(os.getpid()),
              'state': 'running', 'stage': initial_stage}

    def stage(message, state='running'):
        record.update(stage=message, state=state, updated=time.time())
        _write(path, record)

    @contextmanager
    def nonblocking_lock(project, **kwargs):
        try:
            with original_lock(project, blocking=False):
                # Do not overwrite another worker's record before acquiring its lock.
                stage(initial_stage)
                yield
        except BlockingIOError as exc:
            raise RuntimeError('A workspace operation is already in progress.') from exc

    stages = {'start_project': 'Starting workspace services.',
              'health_check': 'Waiting for workspace services to become ready.',
              'stop_state': 'Stopping workspace services.',
              'cleanup_project_data': 'Removing local databases and cache.',
              'remove_project_folder': 'Removing workspace files and dependencies.',
              'unregister_pycharm_sdk': 'Finishing workspace cleanup.'}
    try:
        module.locked_project = nonblocking_lock
        for function_name, message in stages.items():
            if hasattr(module, function_name):
                original = getattr(module, function_name)
                original_functions[function_name] = original
                def wrapped(*args, _function=original, _message=message, **kwargs):
                    stage(_message)
                    return _function(*args, **kwargs)
                setattr(module, function_name, wrapped)
        if action == 'destroy':
            module.destroy_command(name, cleanup_orphans=False)
        else:
            getattr(module, ('restart' if action == 'reset' else action) + '_command')(name)
        stage('Workspace operation completed.', 'done')
    except Exception:
        # Lock contention must leave the actual owner's persisted progress intact.
        if _read(path).get('job') == job_id:
            stage('Workspace operation failed. Check the local operation log.', 'error')
        raise
    finally:
        module.locked_project = original_lock
        for function_name, original in original_functions.items():
            setattr(module, function_name, original)
