"""Run explicitly configured local projects, tracking only processes we started."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import threading
import time

RECORDS = Path.home() / 'Library/Application Support/Worktree Monitor/processes'
LOGS = Path.home() / 'Library/Logs/Worktree Monitor'
_LOCK = threading.Lock()
_CHILDREN = {}


def validate_command(command):
    if not isinstance(command, str) or not command.strip() or len(command) > 4096 or '\0' in command:
        raise ValueError('Enter an executable and arguments, up to 4096 characters.')
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>')
    lexer.whitespace_split = True
    lexer.commenters = ''
    args = list(lexer)
    if not args or not args[0] or any(arg and all(c in ';&|<>' for c in arg) for arg in args):
        raise ValueError('Shell operators are not supported; enter an executable and arguments.')
    if '=' in args[0] or args[0] in {'sh', 'bash', 'zsh', 'fish'} or Path(args[0]).name in {'sh', 'bash', 'zsh', 'fish'}:
        raise ValueError('Use an executable directly, without a shell or environment assignments.')
    return args


def _paths(path):
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Project directory does not exist.')
    key = hashlib.sha256(str(root).encode()).hexdigest()
    return root, RECORDS / (key + '.json'), LOGS / (key + '.log')


def _identity(pid):
    child = _CHILDREN.get(pid)
    if child is not None and child.poll() is not None:
        _CHILDREN.pop(pid, None)
        return None
    result = subprocess.run(['ps', '-ww', '-p', str(pid), '-o', 'uid=,pgid=,lstart=,stat=,args='], capture_output=True, text=True, timeout=3)
    fields = result.stdout.strip().split(None, 8)
    if result.returncode or len(fields) != 9 or 'Z' in fields[7]:
        return None
    try:
        return {'uid': int(fields[0]), 'pgid': int(fields[1]), 'started': ' '.join(fields[2:7]), 'command': fields[8]}
    except ValueError:
        return None


def _record(file, root):
    if not file.exists():
        return None
    try:
        record = json.loads(file.read_text())
        if (record['path'] != str(root) or type(record['pid']) is not int or record['pid'] <= 1
                or type(record['uid']) is not int or record['uid'] != os.getuid()
                or type(record['pgid']) is not int or record['pgid'] != record['pid'] or not isinstance(record['started'], str)
                or not record['started'] or not isinstance(record['command'], str) or not record['command']):
            raise ValueError()
        return record
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise ValueError('Invalid project process record; no process was controlled.') from error


def _owned(record):
    return record is not None and _identity(record['pid']) == {k: record[k] for k in ('uid', 'pgid', 'started', 'command')}


def inspect_project(path):
    with _LOCK:
        root, file, log = _paths(path)
        result = {'running': False, 'cpu_percent': 0.0, 'memory_mb': 0.0, 'log_path': str(log)}
        try:
            record = _record(file, root)
            if not _owned(record):
                return result
            result.update(running=True, pid=record['pid'])
            stats = subprocess.run(['ps', '-axo', 'pgid=,uid=,%cpu=,rss='], capture_output=True, text=True, timeout=3)
            for line in stats.stdout.splitlines():
                fields = line.split()
                if len(fields) == 4 and fields[:2] == [str(record['pgid']), str(os.getuid())]:
                    result['cpu_percent'] += float(fields[2])
                    result['memory_mb'] += int(fields[3]) / 1024
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            result['runtime_error'] = str(error)
        return result


def start_project(path, command):
    args = validate_command(command)
    with _LOCK:
        root, file, log = _paths(path)
        record = _record(file, root)
        if _owned(record):
            raise ValueError('This project is already running.')
        file.parent.mkdir(parents=True, exist_ok=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('ab') as output:
            child = subprocess.Popen(args, cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
        _CHILDREN[child.pid] = child
        temp = file.with_suffix('.tmp')
        try:
            identity = _identity(child.pid)
            if identity is None or identity['uid'] != os.getuid() or identity['pgid'] != child.pid:
                raise RuntimeError(f'Project exited before startup could be verified. See {log}')
            record = {'path': str(root), 'pid': child.pid, **identity}
            temp.write_text(json.dumps(record))
            temp.chmod(0o600)
            temp.replace(file)
        except Exception:
            # The fresh child is ours even when ps or record persistence fails.
            _discard_child(child)
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def _discard_child(child):
    def send(group_signal, direct_method):
        if child.poll() is not None:
            return
        try:
            if os.getpgid(child.pid) == child.pid:
                os.killpg(child.pid, group_signal)
            else:
                direct_method()
        except ProcessLookupError:
            pass
        except OSError:
            direct_method()

    try:
        send(signal.SIGTERM, child.terminate)
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            send(signal.SIGKILL, child.kill)
            child.wait(timeout=2)
    finally:
        _CHILDREN.pop(child.pid, None)


def stop_project(path):
    with _LOCK:
        root, file, _ = _paths(path)
        record = _record(file, root)
        if not record:
            raise ValueError('This project has no process started by Worktree Monitor.')
        if not _owned(record):
            raise ValueError('The recorded process has exited or changed; no process was stopped.')
        try:
            os.killpg(record['pgid'], signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 2
        while _owned(record):
            if time.monotonic() >= deadline:
                raise RuntimeError('The project is still stopping. Check its log and refresh shortly.')
            time.sleep(0.05)
