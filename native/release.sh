#!/bin/sh
# Build on each architecture; an arm64 build is not an Intel/universal release.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -n "${NOTARY_PROFILE:-}" ] && [ -z "${SIGNING_IDENTITY:-}" ]; then
    echo 'NOTARY_PROFILE requires a Developer ID SIGNING_IDENTITY.' >&2
    exit 1
fi
"$ROOT/native/build.sh"
APP="$ROOT/dist/Worktree Monitor.app"
STAGE=$(mktemp -d /private/tmp/worktree-monitor-dmg.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT HUP INT TERM
cp -R "$APP" "$STAGE/Worktree Monitor.app"
ln -s /Applications "$STAGE/Applications"
MINIMUM_OS=$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$APP/Contents/Info.plist")
VERIFICATION='This build is ad-hoc signed and has not been notarized by Apple.'
if [ -n "${SIGNING_IDENTITY:-}" ]; then VERIFICATION='Developer ID signed; check notarization before sharing.'; fi
if [ -n "${NOTARY_PROFILE:-}" ]; then VERIFICATION='Developer ID signed; release script requires successful Apple notarization.'; fi
cat > "$STAGE/INSTALL.txt" <<INSTALL
Worktree Monitor 0.1.25 — $(uname -m), macOS $MINIMUM_OS or later

Drag Worktree Monitor into Applications and launch it there.
Choose folders to scan in Settings. Python is included; Git and any project
commands or optional Showpass/Docker integration tools are installed separately.
Your settings and workspace data are not included in this disk image.

$VERIFICATION
macOS may block a downloaded copy. Only if you trust the sender and this file,
follow Apple's manual Open Anyway instructions, where permitted by your Mac:
https://support.apple.com/en-us/102445
No system-wide security changes are needed. Managed Macs may forbid exceptions.

The app can start/stop configured local commands. Managed workspace cleanup
permanently removes its checkout and local data only after explicit confirmation.
INSTALL
SUFFIX="local"
if [ -n "${SIGNING_IDENTITY:-}" ]; then SUFFIX="signed"; fi
DMG="$ROOT/dist/Worktree-Monitor-0.1.25-$(uname -m)-$SUFFIX.dmg"
hdiutil create -volname 'Worktree Monitor' -srcfolder "$STAGE" -format UDZO -ov "$DMG"
if [ -n "${SIGNING_IDENTITY:-}" ]; then
    codesign --force --timestamp --sign "$SIGNING_IDENTITY" "$DMG"
fi
if [ -n "${NOTARY_PROFILE:-}" ]; then
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
    spctl --assess --type open --context context:primary-signature --verbose "$DMG"
fi
hdiutil verify "$DMG"
(cd "$ROOT/dist" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")
echo "Packaged $DMG"
if [ -z "${NOTARY_PROFILE:-}" ]; then
    echo 'Not notarized: suitable for local testing; Gatekeeper can block downloaded copies.'
fi
