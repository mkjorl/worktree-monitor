"""Load Showpass workspace tooling only from the user's selected checkout."""

import hashlib
import importlib.util
from pathlib import Path
import sys
import threading

from preferences import get_settings, validate_checkout

ProjectError = RuntimeError


class AdapterUnavailable(RuntimeError):
    pass


_modules = {}
_lock = threading.Lock()


def configured_checkout():
    value = get_settings()["showpass_checkout"]
    return Path(value).expanduser().resolve() if value else None


def get_lifecycle():
    checkout = configured_checkout()
    if checkout is None:
        raise AdapterUnavailable("Select a Showpass checkout in Settings to manage Showpass workspaces.")
    try:
        validate_checkout(str(checkout))
        with _lock:
            if checkout not in _modules:
                name = "_worktree_monitor_showpass_" + hashlib.sha256(str(checkout).encode()).hexdigest()[:16]
                spec = importlib.util.spec_from_file_location(name, checkout / "scripts/workspace/lifecycle.py")
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    sys.modules.pop(name, None)
                    raise
                _modules[checkout] = module
            return _modules[checkout]
    except (OSError, ValueError, ImportError) as error:
        raise AdapterUnavailable(f"Cannot load Showpass workspace tooling: {error}") from error


def load_all_states():
    if configured_checkout() is None:
        return []
    return get_lifecycle().load_all_states()


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(get_lifecycle(), name)
