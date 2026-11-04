"""Read-only, bounded discovery of local Git checkouts."""

from collections import deque
import os
from pathlib import Path
import shutil
import subprocess
import time


MAX_DEPTH = 3
MAX_DIRECTORIES = 2000
MAX_PROJECTS = 200
MAX_DURATION = 5
EXCLUDED = {"node_modules", "venv", "dist", "build", "Library"}


def _worktrees(path, timeout=2):
    result = subprocess.run(
        [shutil.which("git") or "/usr/bin/git", "--no-optional-locks", "-c",
         "core.fsmonitor=false", "-C", str(path), "worktree", "list", "--porcelain", "-z"],
        capture_output=True, timeout=timeout, check=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )
    records, record = [], {}
    for field in os.fsdecode(result.stdout).split("\0"):
        if not field:
            if record:
                records.append(record)
                record = {}
        else:
            key, _, value = field.partition(" ")
            record[key] = value
    if record:
        records.append(record)
    return records


def discover_projects(directories):
    deadline = time.monotonic() + MAX_DURATION
    roots, warnings, projects = [], [], {}
    for directory in directories:
        try:
            root = Path(directory).expanduser().resolve(strict=True)
            if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
                raise OSError("directory is not readable")
            if root not in roots:
                roots.append(root)
        except (OSError, RuntimeError) as error:
            warnings.append(f"Cannot scan {directory}: {error}")

    def eligible(path):
        for root in roots:
            if not path.is_relative_to(root):
                continue
            relative = path.relative_to(root)
            if len(relative.parts) > MAX_DEPTH:
                continue
            if any(part.startswith(".") or part in EXCLUDED for part in relative.parts):
                continue
            current = root
            for part in relative.parts:
                current /= part
                if current.is_symlink():
                    break
            else:
                return True
        return False

    queue = deque((root, 0) for root in roots)
    visited = set()
    depth_limited = False
    while queue:
        if time.monotonic() >= deadline:
            warnings.append(f"Discovery stopped after {MAX_DURATION} seconds; choose narrower directories.")
            break
        path, depth = queue.popleft()
        if path in visited:
            continue
        if len(visited) >= MAX_DIRECTORIES:
            warnings.append(f"Discovery stopped after {MAX_DIRECTORIES} directories; choose narrower directories.")
            break
        visited.add(path)
        try:
            marker = path / ".git"
            if marker.is_dir() or marker.is_file():
                records = _worktrees(path, timeout=min(2, max(0.001, deadline - time.monotonic())))
                valid = any(record.get("worktree") == str(path) and "bare" not in record
                            and "prunable" not in record for record in records)
                if not valid:
                    warnings.append(f"Cannot inspect Git checkout {path}: no matching worktree metadata.")
                else:
                    for record in records:
                        if "worktree" not in record or "bare" in record or "prunable" in record:
                            continue
                        checkout = Path(record["worktree"])
                        if not checkout.is_absolute() or not eligible(checkout) or not checkout.is_dir():
                            continue
                        checkout = checkout.resolve(strict=True)
                        identity = str(checkout)
                        if identity in projects:
                            continue
                        if len(projects) >= MAX_PROJECTS:
                            warnings.append(f"Discovery stopped after {MAX_PROJECTS} projects; choose narrower directories.")
                            return {"projects": list(projects.values()), "warnings": warnings}
                        branch = record.get("branch")
                        projects[identity] = {
                            "id": identity, "path": identity, "name": checkout.name,
                            "branch": branch.removeprefix("refs/heads/") if branch else None,
                            "kind": "worktree" if (checkout / ".git").is_file() else "repository",
                        }
                    continue
            children = []
            with os.scandir(path) as entries:
                for entry in entries:
                    if time.monotonic() >= deadline:
                        warnings.append(f"Discovery stopped after {MAX_DURATION} seconds; choose narrower directories.")
                        return {"projects": list(projects.values()), "warnings": warnings}
                    if not entry.name.startswith(".") and entry.name not in EXCLUDED and entry.is_dir(follow_symlinks=False):
                        if depth == MAX_DEPTH:
                            depth_limited = True
                            break
                        children.append(entry.name)
            if depth < MAX_DEPTH:
                children.sort()
                queue.extend((path / name, depth + 1) for name in children)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            warnings.append(f"Cannot inspect {path}: {error}")
    if depth_limited:
        warnings.append(f"Discovery checks up to {MAX_DEPTH} directory levels; add a deeper directory to scan further.")
    return {"projects": list(projects.values()), "warnings": warnings}
