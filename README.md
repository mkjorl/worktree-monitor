# Worktree Monitor

A macOS menu bar app for finding local Git projects and worktrees, viewing resource use, and controlling explicitly configured development processes. Showpass workspace management is an optional integration.

## Install and share

The release is a self-contained `.app` inside an architecture-specific DMG. Open the DMG and drag **Worktree Monitor** into **Applications**. Recipients do not need Python, uv, or this source repository. Git is required for Git discovery; project commands still need their own tools, such as Node. Docker is optional.

The current tested release is **Apple Silicon**, targeting **macOS 13 or later**. Intel needs a separate build on Intel; this is not a universal app. Older macOS versions and a second physical Mac have not been tested.

Builds without a Developer ID are ad-hoc signed and **not notarized**. Downloaded copies may be blocked by macOS. Recipients should review the source and release checksum, then follow [Apple's documented per-app approval instructions](https://support.apple.com/en-us/102445) if their Mac permits an exception. Managed Macs may prohibit this. No installer changes security settings.

## First launch

Open **Settings**, choose one or more workspace directories with the folder button, and select **Save settings**. New installations begin with no directories. Paths and preferences belong to each Mac; none of the developer's settings, credentials, registry, logs, or process records are shipped.

Allow the app to access the selected folders if macOS asks. Removing a directory hides its projects and controls; it does not stop or delete them. An empty list disables project discovery.

The optional settings are:

- **Appearance:** System (the default) follows macOS light/dark appearance. Choose Light or Dark to override it for Worktree Monitor. Changes apply and save immediately.

- **Showpass checkout:** select the base Git checkout containing `scripts/workspace/lifecycle.py` and `cli`. Blank disables the integration. The app loads tooling from the explicitly selected checkout and uses that checkout's own workspace registry and conventions.
- **Docker socket:** leave blank to detect a local Docker Desktop or `/var/run/docker.sock` Unix socket, or select a local socket path. Remote Docker contexts are never used. Docker infrastructure is shown machine-wide.
- **Launch at login:** enable startup through macOS. Approval, if needed, is shown in Settings.

Settings, project configurations, ownership records, and managed operation progress live under `~/Library/Application Support/Worktree Monitor/`. Logs live under `~/Library/Logs/Worktree Monitor/`.

## Discovery and controls

Discovery combines registered Showpass workspaces inside selected directories with local Git repositories and linked worktrees. Read-only Git scans check up to three directory levels, skip hidden/dependency/build folders and directory symlinks, and stop at repository boundaries. Linked worktrees outside selected directories are excluded. Scans are cached for 15 seconds and capped at 2,000 directories, 200 projects, and five seconds; notices explain partial results.

New Git projects are view-only. Their card's gear lets you save a URL and run command, such as `npm run dev`. Commands accept an executable and arguments, without shell operators, shell wrappers, or environment-assignment prefixes. Discovery never executes repository scripts or reads environment files. Configured URLs are links and are not probed automatically.

Start launches the command in its checkout. Stop verifies saved process identity before signalling only the process group started by this app. Processes started elsewhere are not adopted. Unknown CPU and memory appear as dashes; tracked running process groups show measured totals. Commands must stay in the foreground: detached groups and changed/exited leaders cannot be controlled reliably. Quitting the app leaves projects running; saved identities let later launches recover control where the identity still matches.

Managed Showpass workspaces retain their existing start, stop, health, and cleanup behavior. Health uses loopback-only HEAD requests that do not reset idle-stop timers. Cleanup asks for a Yes or No confirmation and permanently removes the selected workspace's checkout, uncommitted changes, local databases, Redis data, and dedicated container. Use Bulk delete to select one or more eligible workspaces. The app validates every selection before it queues individual deletion jobs. Jobs run independently, so a failure on one does not undo workspaces deleted by other jobs. Other workspaces and shared infrastructure are retained. Generic Git projects cannot be deleted through the app.

CPU percentages show the share of total machine capacity across all logical cores (including Docker container figures). Raw process measurements are normalized once before display.

**Reset** (the circular arrow on managed workspaces) stops and restarts workspace services and waits for their health checks. It preserves code changes, databases, dependencies, and project configuration; it uses the checkout’s existing restart routine and does not run cleanup. Reset is available for running, stopped, or provisioned workspaces when no operation is active. If startup fails, the failure is shown and you can retry.

Managed operations save progress and use nonblocking workspace locks. Restarting the app retains operation visibility, and duplicate requests are rejected while the original lock is held. Large dependency folders can take minutes to remove; a phase label is shown without an unreliable percentage or ETA. Detailed operation output stays in the local log.

## Docker controls

Infrastructure shows containers on the selected local Unix socket. Start stopped containers, pause or resume running containers, and stop or restart services. Controls follow the current container state and recheck its full ID before acting. Dedicated Showpass containers stay under their workspace controls, including workspaces hidden by directory filters.

Docker Desktop has separate Start, Stop, and Restart buttons when its CLI is available. Stop and Restart affect all containers in Docker Desktop; container Stop, Restart, and Pause can interrupt shared services. A confirmation explains the impact. Progress and failures appear in Infrastructure. Docker changes run one at a time and are blocked during managed workspace operations. Remote Docker contexts are never used; no delete or prune command is provided.

Docker's own policies still apply: stopping containers created with auto-remove can remove their containers and anonymous volumes.

Globally paused Docker Desktop must be resumed in Docker itself; **Open Docker** opens it from the Mac app. Older installations without the Desktop CLI can still control containers on a working local engine. Docker job tracking lasts for the current app session; reopening the app refreshes actual Docker state.

## Herdr desktop panel

The Mac app includes a movable **Herdr chats** panel that stays above other windows. It shares the dashboard’s paper background, mint card accents, and coral highlights. Questions and approvals show a coral **YOUR TURN** banner and large count; completed replies use a quieter mint banner. A brief indicator pulse marks newly waiting chats, respecting macOS Reduce Motion. It shows questions and approvals as **Needs input**, and completed chats as **Ready for reply**. Idle chats stay idle even when they have completed replies in their history, and are excluded from the attention and ready filters. The menu-bar icon also shows the combined count. Unknown status shows `?` rather than a successful zero.

Select a chat to focus its pane and bring Herdr forward. No reply, approval, or terminal command is sent. Close the panel to hide it; **Settings → Herdr desktop panel** brings it back. Visibility and placement are remembered on this Mac, and appearance follows the app's existing System/Light/Dark setting.

Choose a **Panel mode** in Settings or the panel’s **Show** menu. The selection is remembered and also controls the menu-bar count:

- **Needs attention** (default): questions, approvals, and completed chats.
- **Unread replies**: completed turns Herdr has not marked viewed (`done`).
- **Ready for reply**: chats Herdr currently marks as completed.
- **Needs input**: questions and approval prompts only.
- **All chats**: every detected chat, including working, idle, and unknown states.

Unread follows Herdr’s server-side seen state, which may differ from a particular Herdr client’s badge. Opening a chat marks it viewed in Herdr; changing the panel mode or polling status does not. The attention pulse only applies to newly matching questions or completed chats, not ordinary working/idle activity.

Status refreshes every five seconds using the current user's local `~/.config/herdr/herdr.sock` (or the local `XDG_CONFIG_HOME` equivalent). The integration reads agent, tab, and workspace metadata rather than message text. It monitors the default local Herdr session; named sessions and remote-host sessions are not included. Counts follow Herdr's agent detection, so “Ready for reply” means completed work, not proof that the agent asked a question.

## Phone chat buttons

**Settings → Phone chat buttons → Enable phone access** creates a paired, same-Wi-Fi view. Choose **Show QR code** and scan it with your phone, or copy the pairing link. The phone uses desktop-style chat cards with a left status icon, a workspace/chat title, a second line for agent and status, and a star centered inside the right edge, one per row in portrait and a grid in landscape, and the same six filters. The phone top bar contains only the visible chat count and filter. On phone-sized landscape screens the header is condensed and the grid fits two visible rows (six to eight buttons depending on width); additional rows scroll within the grid. Very short viewports or enlarged text may need scrolling. Tapping a button selects that chat and brings Herdr forward on this Mac. **Preview chat buttons on this Mac** works locally without enabling phone access.

Phone access starts off each time Worktree Monitor launches. Pairing links expire after five minutes and work once; paired sessions last twelve hours. Turning access off or quitting the app invalidates sessions. If the Mac’s network address changes, turn access off and on, then pair again.

This first version uses unencrypted HTTP on a trusted private Wi-Fi/local network, so use it only on networks you trust and keep pairing links private. It binds a separate listener to the Mac’s detected private IPv4 address. That listener exposes only the deck page, pairing, chat-status metadata, and chat selection; workspace, Docker, settings, and terminal-input APIs remain unavailable to phones. No replies or approvals are sent. Phone selection can bring Herdr to the foreground, so it may interrupt the person using the Mac.

Status marks follow Herdr’s outline style: amber spinning arc (working), pink circled cross (blocked), teal circled check (completed/ready), green crescent (idle), and muted dot (unknown). The same marks appear in desktop chat rows. Light appearance uses darker shades for contrast; Reduce Motion stops the working animation.

The phone’s filter is remembered in that browser independently of the desktop panel. You can save the page to your phone’s home screen after pairing. The Mac must stay awake and Worktree Monitor and Herdr must stay running. Visual browser/device QA has not been performed.

## Build and test

Builds require macOS, Xcode Command Line Tools, uv, and network access to fetch pinned build dependencies. The build downloads its portable CPython runtime into `.build/`, freezes the backend with PyInstaller, and bundles its interpreter and required libraries. It does not reuse a developer's virtual environment. Third-party license notices are included.

```sh
./native/release.sh
.build/venv/bin/python -B -m unittest discover -s tests -v
node tests/test_ui.js
node tests/test_docker_ui.js
node tests/test_deck_ui.js
node tests/test_phone_settings_ui.js
```

Outputs are in `dist/`: the app, DMG, and SHA-256 checksum. `native/PACKAGING.md` describes release signing, optional notarization, and clean-machine verification. No user data is included in those outputs.

For source development, `./run` uses the built backend when available. Otherwise it uses `WORKTREE_PYTHON` or `python3`; install `python-dotenv` if using the optional Showpass adapter. It binds to loopback, default port 8765; `DASHBOARD_PORT=0` selects a free port. The native app always uses its own private loopback port.

The floating desktop chat panel uses the same compact count-and-filter header as the phone deck, with compact full-width chat rows.

Star a chat on the desktop panel or phone to bookmark it. Pins are saved on this Mac and shared between both views. Pinned chats sort first within the current filter; use “Pinned chats” to include pinned working and idle chats. Bookmarks follow Herdr chat IDs; closing/recreating a chat can change its ID.

## Updates from GitHub Releases

The Mac app uses Sparkle to find signed releases on `mkjorl/worktree-monitor`.
Settings → **App updates** offers **Check for Updates** and an optional automatic
check toggle. Automatic checks start off; installation requires approval. Existing
builds without the updater require one manual upgrade first.

Release maintainers: configure the `SPARKLE_PRIVATE_KEY` Actions secret once, then
increment the version/build in `native/build.sh`, commit and push, and publish a
stable GitHub Release tagged `vX.Y.Z` matching the app version. The
**Publish Sparkle update** action builds, tests, signs, and attaches the ZIP,
`SHA256SUMS`, and signed `appcast.xml`. Wait for it to pass before sharing the
release. The local `./native/release.sh --publish-draft` flow also remains available.
See [CI setup, packaging, and key-backup instructions](native/PACKAGING.md).
