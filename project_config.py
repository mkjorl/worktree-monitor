"""User-supplied settings for discovered Git projects."""
import json
import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

from project_runtime import validate_command

PROJECTS_PATH = Path.home() / 'Library/Application Support/Worktree Monitor/projects.json'
LOCK = threading.Lock()


def load_projects():
    try:
        profiles = json.loads(PROJECTS_PATH.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(f'Cannot read project settings: {exc}') from exc
    if not isinstance(profiles, dict) or any(not isinstance(key, str) or not isinstance(value, dict) for key, value in profiles.items()):
        raise ValueError('Invalid project settings file')
    return profiles


def validated_profile(url, command):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise ValueError('Enter a valid HTTP or HTTPS URL')
    url = url.strip()
    if url:
        try:
            parsed = urlsplit(url)
            parsed.port  # Validate the port without making a network request.
            valid = parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('Use an HTTP or HTTPS URL without credentials')
    if not isinstance(command, str) or len(command) > 4096 or '\0' in command:
        raise ValueError('Enter a command of at most 4096 characters')
    command = command.strip()
    if command:
        validate_command(command)
    return {'url': url, 'command': command}


def get_project(path):
    profile = load_projects().get(path, {})
    return validated_profile(**{key: profile.get(key, '') for key in ('url', 'command')})


def save_project(path, url, command):
    profile = validated_profile(url, command)
    with LOCK:
        profiles = load_projects()
        if any(profile.values()):
            profiles[path] = profile
        else:
            profiles.pop(path, None)
        PROJECTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=PROJECTS_PATH.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(profiles, output, indent=2)
                output.write('\n')
            os.replace(temporary, PROJECTS_PATH)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return profile
