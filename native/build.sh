#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
APP="$ROOT/dist/Worktree Monitor.app"
CONTENTS="$APP/Contents"
BUILD="$ROOT/.build"
VENV="$BUILD/venv"
mkdir -p "$BUILD"
if [ -n "${BUILD_PYTHON:-}" ]; then
    PYTHON="$BUILD_PYTHON"
else
    UV=${UV:-uv}
    "$UV" python install --no-bin --install-dir "$BUILD/python" --cache-dir "$BUILD/uv-cache" 3.12.10
    case "$(uname -m)" in
        arm64) PYTHON="$BUILD/python/cpython-3.12.10-macos-aarch64-none/bin/python3" ;;
        x86_64) PYTHON="$BUILD/python/cpython-3.12.10-macos-x86_64-none/bin/python3" ;;
        *) echo 'Unsupported Mac architecture' >&2; exit 1 ;;
    esac
fi
BASE_PREFIX=$("$PYTHON" -c 'import sys; print(sys.base_prefix)')
if [ -x "$VENV/bin/python" ] && [ "$("$VENV/bin/python" -c 'import sys; print(sys.base_prefix)')" != "$BASE_PREFIX" ]; then
    rm -rf "$VENV"
fi
if [ ! -x "$VENV/bin/python" ]; then "$PYTHON" -m venv "$VENV"; fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -r "$ROOT/native/build-requirements.txt"
set -- --noconfirm --clean --onedir --name worktree-backend --distpath "$BUILD/backend-dist" --workpath "$BUILD/pyinstaller" --specpath "$BUILD" --add-data "$ROOT/index.html:." --add-data "$ROOT/deck.html:." --add-data "$ROOT/deck-icon-180.png:." --add-data "$ROOT/deck-icon-512.png:." --add-data "$ROOT/deck.webmanifest:." --hidden-import dotenv --hidden-import xml.sax.saxutils --hidden-import xml.etree.ElementTree --hidden-import platform --hidden-import copy --hidden-import fcntl --hidden-import plistlib
if [ -n "${SIGNING_IDENTITY:-}" ]; then
    set -- "$@" --codesign-identity "$SIGNING_IDENTITY"
fi
PYINSTALLER_CONFIG_DIR="$BUILD/pyinstaller-cache" "$VENV/bin/python" -m PyInstaller "$@" "$ROOT/dashboard.py"
rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"
MINIMUM_OS=$("$VENV/bin/python" -c 'import sysconfig; value = sysconfig.get_config_var("MACOSX_DEPLOYMENT_TARGET") or "13.0"; print(value if tuple(map(int, value.split("."))) >= (13,) else "13.0")')
swiftc -target "$(uname -m)-apple-macosx$MINIMUM_OS" -module-cache-path /private/tmp/worktree-monitor-module-cache -O "$ROOT/native/WorktreeMonitor.swift" -framework AppKit -framework ServiceManagement -framework WebKit -o "$CONTENTS/MacOS/WorktreeMonitor"
cp -R "$BUILD/backend-dist/worktree-backend" "$CONTENTS/Resources/backend"
cp "$ROOT/native/AppIcon.icns" "$CONTENTS/Resources/AppIcon.icns"
cp "$ROOT/native/MenuBarIcon.png" "$CONTENTS/Resources/MenuBarIcon.png"
"$VENV/bin/python" "$ROOT/native/collect-notices.py" "$CONTENTS/Resources/ThirdPartyNotices.txt" "$BUILD/pyinstaller/worktree-backend/Analysis-00.toc"
cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>WorktreeMonitor</string>
<key>CFBundleIconFile</key><string>AppIcon.icns</string>
<key>CFBundleIdentifier</key><string>local.showpass.worktree-monitor</string>
<key>CFBundleName</key><string>Worktree Monitor</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.1.25</string>
<key>CFBundleVersion</key><string>26</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>LSUIElement</key><true/>
<key>NSLocalNetworkUsageDescription</key><string>Allow paired phones on your Wi-Fi to view chat status and select a chat on this Mac.</string>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion $MINIMUM_OS" "$CONTENTS/Info.plist"
"$CONTENTS/MacOS/WorktreeMonitor" --self-check
"$CONTENTS/Resources/backend/worktree-backend" --self-check
plutil -lint "$CONTENTS/Info.plist"
if [ -n "${SIGNING_IDENTITY:-}" ]; then
    codesign --force --options runtime --timestamp --sign "$SIGNING_IDENTITY" "$CONTENTS/MacOS/WorktreeMonitor"
    codesign --force --options runtime --timestamp --sign "$SIGNING_IDENTITY" "$APP"
else
    codesign --force --sign - "$CONTENTS/MacOS/WorktreeMonitor"
    codesign --force --sign - "$APP"
fi
codesign --verify --deep --strict "$APP"
echo "Built $APP ($(uname -m))"
