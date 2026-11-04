#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$ROOT/native/build.sh"
exec "$ROOT/.build/venv/bin/python" "$ROOT/native/release.py" "$@"
