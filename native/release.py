"""Package and sign OTA assets; optionally upload a draft GitHub Release."""
import argparse
import base64
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from source_fingerprint import source_fingerprint

ROOT = Path(__file__).resolve().parent.parent
SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
ET.register_namespace("sparkle", SPARKLE_NS)


def validate_config(config):
    if config.get("repository") != "mkjorl/worktree-monitor":
        raise ValueError("Release repository must be mkjorl/worktree-monitor.")
    if config.get("feed_url") != "https://github.com/mkjorl/worktree-monitor/releases/latest/download/appcast.xml":
        raise ValueError("Use the configured HTTPS release feed.")
    if len(base64.b64decode(config["public_key"], validate=True)) != 32:
        raise ValueError("Expected a 32-byte Ed25519 public key.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", config["signing_account"]):
        raise ValueError("Invalid signing account.")


def make_appcast(config, info, archive_name, length, signature):
    validate_config(config)
    version, build = info["CFBundleShortVersionString"], info["CFBundleVersion"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not str(build).isdigit() or int(build) <= 0:
        raise ValueError("Expected a numeric app version and positive build number.")
    if len(base64.b64decode(signature, validate=True)) != 64 or length <= 0:
        raise ValueError("Expected an Ed25519 signature and nonempty archive.")
    if archive_name != f"Worktree-Monitor-{version}-arm64.zip":
        raise ValueError("Unexpected archive name.")
    minimum = info["LSMinimumSystemVersion"].split(".")
    if not all(part.isdigit() for part in minimum) or not 1 <= len(minimum) <= 3:
        raise ValueError("Invalid minimum macOS version.")
    minimum += ["0"] * (3 - len(minimum))
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Worktree Monitor updates"
    ET.SubElement(channel, "link").text = f"https://github.com/{config['repository']}/releases"
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = f"Worktree Monitor {version}"
    ET.SubElement(item, "pubDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(item, f"{{{SPARKLE_NS}}}version").text = str(build)
    ET.SubElement(item, f"{{{SPARKLE_NS}}}shortVersionString").text = version
    ET.SubElement(item, f"{{{SPARKLE_NS}}}minimumSystemVersion").text = ".".join(minimum)
    ET.SubElement(item, f"{{{SPARKLE_NS}}}hardwareRequirements").text = "arm64"
    ET.SubElement(item, "description").text = "A new version of Worktree Monitor is available."
    ET.SubElement(item, "enclosure", {
        "url": f"https://github.com/{config['repository']}/releases/download/v{version}/{archive_name}",
        "length": str(length), "type": "application/octet-stream",
        f"{{{SPARKLE_NS}}}edSignature": signature,
    })
    ET.indent(rss)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def validate_tag_target(output, tag, head):
    refs = dict((ref, sha) for sha, ref in (line.split() for line in output.splitlines() if line.strip()))
    ref = f"refs/tags/{tag}"
    commit = refs.get(ref + "^{}", refs.get(ref))
    if commit is not None and commit != head:
        raise ValueError("The release tag points to a different source revision.")
    return commit is not None


def validate_ci_release(event, info):
    release = event["release"]
    version = info["CFBundleShortVersionString"]
    if event["repository"]["full_name"] != "mkjorl/worktree-monitor":
        raise ValueError("Release repository must be mkjorl/worktree-monitor.")
    if release["draft"] or release["prerelease"] or release.get("immutable"):
        raise ValueError("CI requires a published, mutable, stable release.")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or release["tag_name"] != f"v{version}":
        raise ValueError("Release tag must match the app version in native/build.sh (vX.Y.Z).")
    assets = {asset["name"] for asset in release["assets"]}
    if assets & {f"Worktree-Monitor-{version}-arm64.zip", "appcast.xml", "SHA256SUMS"}:
        raise ValueError("Signed release assets already exist; CI will not replace published files.")


def publish_draft(config, tag, assets, notes):
    # Read this repository's credential helper; never switch the global gh account.
    expected = f"https://github.com/{config['repository']}.git"
    remote = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=ROOT, text=True).strip()
    if remote != expected:
        raise ValueError("Origin does not match the release repository.")
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    if dirty:
        raise ValueError("Commit and push source changes before uploading a release.")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    remote_head = subprocess.check_output(["git", "ls-remote", "origin", "refs/heads/main"], cwd=ROOT, text=True).split()
    if not remote_head or remote_head[0] != head:
        raise ValueError("Push this source revision to main before uploading a release.")
    tag_output = subprocess.check_output(["git", "ls-remote", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"], cwd=ROOT, text=True)
    tagged = validate_tag_target(tag_output, tag, head)
    built_info = plistlib.loads((ROOT / "dist/Worktree Monitor.app/Contents/Info.plist").read_bytes())
    if built_info.get("WTMSourceFingerprint") != source_fingerprint(ROOT):
        raise ValueError("The packaged app does not match the current source. Rebuild before publishing.")
    credential = subprocess.check_output(["git", "credential", "fill"], cwd=ROOT, text=True,
        input=f"protocol=https\nhost=github.com\npath={config['repository']}.git\nusername=mkjorl\n\n",
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    fields = dict(line.split("=", 1) for line in credential.splitlines() if "=" in line)
    if fields.get("username") != "mkjorl" or not fields.get("password"):
        raise ValueError("Authenticate this repository as mkjorl first.")
    env = {**os.environ, "GH_TOKEN": fields["password"]}
    repo = config["repository"]
    existing = subprocess.run(["gh", "release", "view", tag, "--repo", repo, "--json", "isDraft,targetCommitish"],
                              env=env, capture_output=True, text=True)
    if existing.returncode == 0:
        if json.loads(existing.stdout).get("isDraft") is not True:
            raise ValueError("This release is already public; increment the version instead of replacing it.")
        if not tagged:
            run(["gh", "release", "edit", tag, "--repo", repo, "--target", head], env=env)
        run(["gh", "release", "upload", tag, *assets, "--repo", repo, "--clobber"], env=env)
        run(["gh", "release", "edit", tag, "--repo", repo, "--notes-file", notes], env=env)
    else:
        # Only a confirmed 404 means absent; network/auth failures must not create a release.
        probe = subprocess.run(["gh", "api", f"repos/{repo}/releases/tags/{tag}"], env=env, capture_output=True, text=True)
        if probe.returncode == 0 or "HTTP 404" not in probe.stderr:
            raise ValueError("Could not establish whether the release already exists.")
        run(["gh", "release", "create", tag, *assets, "--repo", repo, "--target", head,
             "--title", f"Worktree Monitor {tag}", "--notes-file", notes, "--draft"], env=env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish-draft", action="store_true", help="Upload a draft; publishing it activates OTA.")
    args = parser.parse_args()
    config = json.loads((ROOT / "native/update-config.json").read_text())
    validate_config(config)
    sparkle = Path(subprocess.check_output([ROOT / "native/fetch-sparkle.sh"], text=True).strip())
    account = config["signing_account"]
    public = subprocess.check_output([sparkle / "bin/generate_keys", "--account", account], text=True)
    match = re.search(r"<string>([A-Za-z0-9+/]+=*)</string>", public)
    public = match.group(1) if match else ""
    if public != config["public_key"]:
        raise ValueError("The release signing key in Keychain does not match this app's public key.")
    app = ROOT / "dist/Worktree Monitor.app"
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info.get("SUPublicEDKey") != public or info.get("SUFeedURL") != config["feed_url"] or not info.get("SURequireSignedFeed"):
        raise ValueError("Build the app with the configured signed update feed before packaging.")
    architectures = subprocess.check_output(["lipo", "-archs", app / "Contents/MacOS/WorktreeMonitor"], text=True).strip()
    if architectures != "arm64":
        raise ValueError("This release feed currently distributes Apple Silicon builds only.")
    if info.get("WTMSourceFingerprint") != source_fingerprint(ROOT):
        raise ValueError("The app was built from different source inputs. Rebuild before packaging.")
    run(["codesign", "--verify", "--deep", "--strict", app])
    version = info["CFBundleShortVersionString"]
    output = ROOT / "dist/updates"; output.mkdir(parents=True, exist_ok=True)
    archive = output / f"Worktree-Monitor-{version}-arm64.zip"
    feed = output / "appcast.xml"
    install = (f"Worktree Monitor {version}\nApple Silicon Mac, macOS {info['LSMinimumSystemVersion']} or later.\n\n"
               "Drag Worktree Monitor.app into Applications and launch it. Python is included.\n"
               "Configure folders and optional integrations in Settings. No personal settings or data are bundled.\n\n"
               "For a non-notarized build, macOS may require Apple's Open Anyway process where permitted:\n"
               "https://support.apple.com/en-us/102445\n\n"
               "Future updates: Settings > Updates > Check for Updates. Automatic checks are optional.\n")
    with tempfile.TemporaryDirectory(prefix="worktree-release-") as temporary:
        copy = Path(temporary) / app.name
        run(["ditto", "--norsrc", "--noextattr", "--noacl", app, copy])
        def pack():
            archive.unlink(missing_ok=True)
            run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", copy, archive])
            with zipfile.ZipFile(archive, "a") as zipped:
                zipped.writestr("INSTALL.txt", install)
        pack()
        profile = os.environ.get("NOTARY_PROFILE")
        if profile:
            if not os.environ.get("SIGNING_IDENTITY"):
                raise ValueError("Notarization requires an explicit Developer ID signing identity.")
            run(["xcrun", "notarytool", "submit", archive, "--keychain-profile", profile, "--wait"])
            run(["xcrun", "stapler", "staple", copy]); run(["xcrun", "stapler", "validate", copy]); pack()
    sign_tool = sparkle / "bin/sign_update"
    signature = subprocess.check_output([sign_tool, "--account", account, "-p", archive], text=True).strip()
    run([sign_tool, "--account", account, "--verify", archive, signature])
    feed.write_bytes(make_appcast(config, info, archive.name, archive.stat().st_size, signature))
    run([sign_tool, "--account", account, feed])
    run([sign_tool, "--account", account, "--verify", feed])
    checksums = output / "SHA256SUMS"
    checksums.write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in [archive, feed]))
    notes = output / "RELEASE-NOTES.md"
    notes.write_text(f"Worktree Monitor {version}\n\nDownload `{archive.name}`, unzip, and drag the app into Applications.\n\n"
                     "Apple Silicon, macOS 13+. This local build is not notarized; installation may require "
                     "[Open Anyway](https://support.apple.com/en-us/102445).\n\n"
                     "This version adds signed updates from GitHub Releases. Existing copies need one manual upgrade. "
                     "Then use Settings > Updates to check for newer releases.\n\n"
                     "Keep appcast.xml attached to every future release so installed apps can find the latest version.\n")
    if args.publish_draft:
        publish_draft(config, f"v{version}", [archive, feed, checksums], notes)
    print(f"Signed update assets: {output}")
    print("Publish the draft release only after all assets are attached. That makes it discoverable by installed apps.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Release failed: {error}") from None
