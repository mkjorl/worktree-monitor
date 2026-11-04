#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=2.10.1
SHA=1a856d2a9edb8e628de88121d7be04cf0e2167f8641650b78fca00b14244cf27
CACHE="$ROOT/.build/sparkle"
ARCHIVE="$CACHE/Sparkle-$VERSION.tar.xz"
DEST="$CACHE/$VERSION"
mkdir -p "$CACHE"
if [ ! -f "$ARCHIVE" ]; then
    curl --fail --location --proto '=https' --tlsv1.2 --max-time 120 "https://github.com/sparkle-project/Sparkle/releases/download/$VERSION/Sparkle-$VERSION.tar.xz" --output "$ARCHIVE.download"
    mv "$ARCHIVE.download" "$ARCHIVE"
fi
ACTUAL=$(shasum -a 256 "$ARCHIVE" | cut -d ' ' -f 1)
if [ "$ACTUAL" != "$SHA" ]; then echo 'Sparkle archive checksum mismatch.' >&2; exit 1; fi
if [ ! -d "$DEST/Sparkle.framework" ]; then
    mkdir -p "$DEST"
    tar -xJf "$ARCHIVE" -C "$DEST"
fi
codesign --verify --deep --strict "$DEST/Sparkle.framework"
printf '%s\n' "$DEST"
