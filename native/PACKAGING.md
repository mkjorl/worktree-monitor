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

## Automatic releases with GitHub Actions

The `Publish Sparkle update` workflow runs when you **publish a stable GitHub
Release**, including one previously saved as a draft. It checks out that release's
tag, builds on an Apple Silicon runner, runs the source and packaged tests, signs
the ZIP and feed, and attaches them to the release. The feed uploads last so it
never advertises an archive that has not finished uploading. Prereleases and
releases that already have `appcast.xml` are skipped.

Set up the `SPARKLE_PRIVATE_KEY` repository Actions secret once with the **existing**
key exported from the release Mac. Run the following from the repository on that
Mac, authenticated to GitHub as a maintainer of `mkjorl/worktree-monitor`:

```sh
(
  set -eu
  key_dir=$(mktemp -d)
  trap 'rm -rf "$key_dir"' EXIT
  chmod 700 "$key_dir"
  sparkle=$(./native/fetch-sparkle.sh)
  "$sparkle/bin/generate_keys" --account worktree-monitor-mkjorl -x "$key_dir/private-key"
  gh secret set SPARKLE_PRIVATE_KEY --repo mkjorl/worktree-monitor < "$key_dir/private-key"
)
```

Do not generate a replacement key: installed apps trust the public key already
in `native/update-config.json`. The workflow imports the secret into the temporary
GitHub runner's Keychain, removes the exported file, and uses the existing release
script to verify the matching public key and both signatures. The private key is
never passed as a command-line argument or included in release assets.

For each release:

1. Increase `CFBundleShortVersionString` and `CFBundleVersion` in `native/build.sh`.
   The internal build number must increase for every release.
2. Commit and push to `main`, including this workflow before the first release.
3. Create and publish a stable release tagged `vX.Y.Z`, matching the app version,
   from that commit. Leave the ZIP, `appcast.xml`, and `SHA256SUMS` for the action.
4. Wait for `Publish Sparkle update` to pass before sharing the release.

The latest-release feed can return 404 while the action builds a newly published
release; installed apps can check again after it completes. A failed build leaves
that release without a usable feed. CI refuses to replace existing release files.
For a failed partial upload, remove only the incomplete assets from that release
before rerunning the job. Immutable releases cannot accept post-publication
attachments; leave release immutability disabled for this workflow.

The action uses the existing free ad-hoc signing flow. Developer ID signing and
notarization remain available through the local flow below.

## Local release alternative

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
service with account `worktree-monitor-mkjorl`. For CI, a copy is stored only in
the `SPARKLE_PRIVATE_KEY` GitHub Actions secret. It is never committed or attached
to a release.
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
