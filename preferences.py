"""Persist machine-local workspace and integration settings."""

import json
import os
from pathlib import Path
import stat
import tempfile
import threading


SETTINGS_PATH = Path.home() / "Library/Application Support/Worktree Monitor/settings.json"
DEFAULT_DIRECTORIES = []
SETTINGS_LOCK = threading.Lock()


def _validate_path(value, label, allow_empty=False):
    if allow_empty and value == "":
        return
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or "\0" in value:
        raise ValueError(f"{label} must be a nonempty path of at most 4096 characters.")
    try:
        absolute = Path(value).expanduser().is_absolute()
    except (OSError, RuntimeError) as error:
        raise ValueError(f"Cannot resolve {label}: {value}") from error
    if not absolute:
        raise ValueError(f"{label} must be an absolute path: {value}")


def _validate_directories(directories):
    if not isinstance(directories, list) or len(directories) > 32:
        raise ValueError("Directories must be an array with at most 32 entries.")
    for directory in directories:
        _validate_path(directory, "Directory")


def validate_checkout(value):
    _validate_path(value, "Showpass checkout", allow_empty=True)
    if not value:
        return ""
    path = Path(value).expanduser().resolve()
    if not path.is_dir() or not (path / ".git").exists() or not all(
        (path / "scripts/workspace" / name).is_file() for name in ("lifecycle.py", "cli")
    ):
        raise ValueError("Showpass checkout must be a Git checkout containing scripts/workspace/lifecycle.py and cli.")
    return str(path)


def _validate_socket(value):
    _validate_path(value, "Docker socket", allow_empty=True)
    if not value:
        return ""
    path = Path(value).expanduser().resolve()
    try:
        valid = stat.S_ISSOCK(path.stat().st_mode)
    except OSError:
        valid = False
    if not valid:
        raise ValueError("Docker socket must be an existing local Unix socket.")
    return str(path)


def validate_appearance(value):
    if value not in ("system", "light", "dark"):
        raise ValueError("Appearance must be system, light, or dark.")


def get_settings():
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"directories": [], "showpass_checkout": "", "docker_socket": "", "appearance": "system"}
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read settings at {SETTINGS_PATH}: {error}") from error
    try:
        if not isinstance(settings, dict) or "directories" not in settings:
            raise ValueError("Settings must contain a directories array.")
        _validate_directories(settings["directories"])
        validate_appearance(settings.get("appearance", "system"))
        for key in ("showpass_checkout", "docker_socket"):
            _validate_path(settings.get(key, ""), key, allow_empty=True)
    except ValueError as error:
        raise ValueError(f"Invalid settings at {SETTINGS_PATH}: {error}") from error
    return {"directories": settings["directories"], "showpass_checkout": settings.get("showpass_checkout", ""), "docker_socket": settings.get("docker_socket", ""), "appearance": settings.get("appearance", "system")}


def save_settings(directories, showpass_checkout=None, docker_socket=None):
    # Serialize read/modify/write across the app and browser clients.
    with SETTINGS_LOCK:
        _validate_directories(directories)
        normalized = []
        for directory in directories:
            try:
                path = Path(directory).expanduser().resolve()
            except (OSError, RuntimeError) as error:
                raise ValueError(f"Cannot resolve directory: {directory}") from error
            if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
                raise ValueError(f"Directory must exist and be readable: {directory}")
            if str(path) not in normalized:
                normalized.append(str(path))
        settings = get_settings()
        settings["directories"] = normalized
        if showpass_checkout is not None:
            settings["showpass_checkout"] = validate_checkout(showpass_checkout)
        if docker_socket is not None:
            settings["docker_socket"] = _validate_socket(docker_socket)
        return _write_settings(settings)


def save_appearance(appearance):
    # Serialize read/modify/write across the app and browser clients.
    with SETTINGS_LOCK:
        validate_appearance(appearance)
        settings = get_settings()
        settings["appearance"] = appearance
        return _write_settings(settings)


def _write_settings(settings):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=SETTINGS_PATH.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(settings, output, indent=2)
            output.write("\n")
        os.replace(temporary, SETTINGS_PATH)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return settings
