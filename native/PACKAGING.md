# macOS packaging

Run `./native/release.sh` on the Mac architecture you intend to support. The output
is `dist/Worktree-Monitor-0.1.25-arm64-local.dmg` on Apple Silicon (or x86_64 on Intel).
Drag Worktree Monitor into Applications. The default runtime supports macOS 13 or later; the manifest automatically raises
this minimum if a custom interpreter requires a newer OS. The Swift compiler receives
an explicit target so newer build machines do not silently raise the executable requirement.
The packaged smoke check verifies that the executable target agrees with the manifest.
Build with a compatible Python interpreter for your oldest supported OS and test there.
The app does not require Python to be installed on the recipient's Mac.

The build requires Xcode command line tools and uv. It downloads a pinned
relocatable Python 3.12.10 into `.build/python` (no global installation). Override the
build interpreter with `BUILD_PYTHON=/path/to/python3`. Build dependencies are
installed into `.build/venv`; that environment is never shipped. PyInstaller packages
only the dashboard modules, bundled runtime, UI and selected dependencies. User
settings, workspace state, credentials and checkout contents are not bundled.
The Showpass integration uses tooling from a checkout selected by each user;
that checkout and its dependencies are not redistributed.

## Trusted distribution

Local builds use ad-hoc signing and can be shared directly with people who trust
the sender. Downloaded copies can be rejected by Gatekeeper; INSTALL.txt includes
Apple’s supported Open Anyway process where the Mac permits it. This build has
not been reviewed by Apple. No security settings are changed by the installer.

For distribution without that manual exception, configure an Apple Developer ID Application certificate and
an existing `notarytool` Keychain profile, then run:

```
SIGNING_IDENTITY='Developer ID Application: Your Name (TEAMID)' \
NOTARY_PROFILE='your-existing-profile' ./native/release.sh
```

The build signs collected binaries through PyInstaller, then signs the native
launcher and outer app with hardened runtime. The release script signs the DMG,
submits it to Apple only when `NOTARY_PROFILE` is explicitly configured, staples
and validates the result, and writes a SHA-256 checksum. No broad hardened runtime
exceptions are enabled. Third-party redistribution notices are included in Resources.
Do not distribute a local build as a notarized release.

Build separately on Intel and Apple Silicon; this script does not produce a
universal application. Test installation, first launch, folder permissions,
launch at login, and optional integrations on a clean machine before publishing.

References:
- https://pyinstaller.org/en/stable/operating-mode.html
- https://pyinstaller.org/en/stable/feature-notes.html#macos-specific-options
- https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
- https://docs.astral.sh/uv/concepts/python-versions/
- https://support.apple.com/en-us/102445
