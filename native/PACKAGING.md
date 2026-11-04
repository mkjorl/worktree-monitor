# Build and publish Worktree Monitor

The app includes Sparkle 2.10.1 and uses signed updates from
`https://github.com/mkjorl/worktree-monitor/releases/latest/download/appcast.xml`.
The current release pipeline builds **Apple Silicon, macOS 13+**.
Python and Sparkle are bundled; recipients do not need Python or a GitHub account.

## Local build

Run `./native/build.sh`. This downloads a pinned relocatable Python 3.12.10 and
Sparkle distribution into `.build/`; Sparkle's SHA-256 is checked before use.
Build dependencies go into `.build/venv`. Set `BUILD_PYTHON=/path/to/python3` to
reuse a compatible local interpreter. The result is `dist/Worktree Monitor.app`.

User settings, bookmarks, workspace contents, credentials, build caches, and
release signing tools are not included in the app. Integration tools such as
Docker, Git, Herdr and the optional Showpass checkout are installed separately.
Sparkle and Python redistribution notices are included in Resources.

## Release a new version

1. Increase `CFBundleShortVersionString` and `CFBundleVersion` in `native/build.sh`.
   The internal build number must increase for every release.
2. Commit and push the source revision to `main` on `mkjorl/worktree-monitor`.
3. Run `./native/release.sh --publish-draft` on the release Mac.
4. Review the draft release and its attached ZIP, `appcast.xml`, and `SHA256SUMS`.
   Publish it in GitHub when ready. Keep all assets attached to each release.

The build records a fingerprint of its runtime source and assets. Packaging and
publishing reject stale build output; release tags must point to the pushed source
revision, including existing annotated tags and draft releases.

The release command creates a ZIP with the app and installation instructions,
signs the ZIP and appcast with the release key, verifies both signatures, then
uploads a **draft** release targeting the pushed commit. It refuses to overwrite
an already published version. The command uses this repository's Git credential
helper as `mkjorl`; it does not change the global GitHub CLI account. Publishing
the complete draft makes the signed feed available through GitHub's latest-release
asset URL. No CI service or paid account is required for this local release flow.

To only generate release files, run `./native/release.sh` or run
`.build/venv/bin/python native/release.py` against an already built app.
Files are written to `dist/updates/`. Neither command publishes a live release.

## Keep the signing key

`native/update-config.json` holds the public key and feed URL. The matching
private Ed25519 key lives in the release Mac's login Keychain under Sparkle's
service with account `worktree-monitor-mkjorl`. It is never committed or uploaded.
Keep an encrypted backup of this key: losing it can require distributing another
manual installation before users can trust a replacement key. Sparkle's
`generate_keys` utility supports export/import. Encrypt any exported backup,
protect it separately, and keep it out of this repository.
A new machine must import the existing key before publishing releases.

Installed apps require signed feeds and verify archive signatures before
extraction. Do not replace the public key for routine releases. A fork should
use its own repository and signing key instead of this application's feed.

## First installation and updates

Existing pre-updater builds need **one manual upgrade** to an updater-enabled
version. After that use Settings → App updates → Check for Updates, or opt into
automatic checks. Installation still requires approval. The normal Sparkle relaunch
path waits for local app operations; Sparkle documents other quit/relaunch paths
that do not invoke that postponement hook. Running workspaces are separate processes.
Phone pairing must be renewed after an app restart.

Local builds are ad-hoc signed, not Apple-notarized. Downloaded copies may need
Apple's Open Anyway process where the Mac permits it. Managed Macs may prohibit
exceptions. No system-wide security settings are changed.

For optional Apple Developer ID signing and notarization, use an existing
certificate and notarytool profile:

```sh
SIGNING_IDENTITY='Developer ID Application: Your Name (TEAMID)' \
NOTARY_PROFILE='your-existing-profile' ./native/release.sh --publish-draft
```

The app and nested Sparkle components are signed, the archive is submitted only
when `NOTARY_PROFILE` is explicitly provided, the app is stapled, and the final
repackaged ZIP is signed with Ed25519. The free ad-hoc flow does not contact Apple.
Clean-machine first-launch, notarized distribution, and real OTA installation
must be verified separately before broad distribution.

References:
- https://sparkle-project.org/documentation/
- https://sparkle-project.org/documentation/publishing/
- https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases
- https://support.apple.com/en-us/102445
