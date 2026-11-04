"""Fingerprint the source and assets consumed by the packaged app build."""
import hashlib
from pathlib import Path
import sys

APP_INPUTS = (
    'dashboard.py', 'discovery.py', 'herdr_status.py', 'phone_deck.py', 'preferences.py',
    'project_config.py', 'project_runtime.py', 'showpass_adapter.py', 'workspace_operations.py',
    'index.html', 'deck.html', 'deck.webmanifest', 'deck-icon-180.png', 'deck-icon-512.png',
)


def source_fingerprint(root):
    root = Path(root)
    inputs = [root / name for name in APP_INPUTS]
    inputs += [p for p in (root / 'native').iterdir() if p.is_file() and p.suffix in
               {'.swift', '.sh', '.py', '.json', '.txt', '.icns', '.png', '.rst'}]
    fingerprint = hashlib.sha256()
    for path in sorted(inputs):
        fingerprint.update(str(path.relative_to(root)).encode() + b'\0')
        fingerprint.update(hashlib.sha256(path.read_bytes()).digest())
    return fingerprint.hexdigest()


if __name__ == '__main__':
    print(source_fingerprint(sys.argv[1]))
