"""Read agent attention metadata from Herdr's local Unix socket."""
import json
import os
from pathlib import Path
import socket
import stat
import time
import uuid
import tempfile
import threading


PINS_PATH = Path.home() / "Library/Application Support/Worktree Monitor/chat-pins.json"
PINS_LOCK = threading.Lock()


def pinned_ids():
    try:
        pins = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    if not isinstance(pins, list) or len(pins) > 1000 or any(not isinstance(p, str) or not p or len(p) > 128 for p in pins):
        raise ValueError("Chat bookmarks are invalid.")
    return set(pins)


def set_pin(pane, pinned):
    if not isinstance(pane, str) or not pane or len(pane) > 128 or type(pinned) is not bool:
        raise ValueError("Choose a chat and bookmark state.")
    if pinned and not any(chat["id"] == pane for chat in get_status()["all_chats"]):
        raise ValueError("This chat is no longer available. Refresh its status.")
    with PINS_LOCK:
        pins = pinned_ids()
        if pinned:
            pins.add(pane)
        else:
            pins.discard(pane)
        if len(pins) > 1000:
            raise ValueError("Unpin a chat before adding more bookmarks.")
        PINS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=PINS_PATH.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(sorted(pins), output)
                output.write("\n")
            os.replace(temporary, PINS_PATH)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {"pinned": pinned}


def request(method, params):
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    if not config.is_absolute():
        raise ValueError("Herdr config directory must be an absolute local path")
    path = config / "herdr/herdr.sock"
    metadata = path.stat()
    if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ValueError("Herdr socket must belong to the current user")
    identifier = uuid.uuid4().hex
    payload = {"id": identifier, "method": method, "params": params}
    deadline = time.monotonic() + 2
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(path))
        connection.sendall(json.dumps(payload).encode() + b"\n")
        response = bytearray()
        while b"\n" not in response:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Herdr status timed out")
            connection.settimeout(remaining)
            chunk = connection.recv(65536)
            if not chunk:
                raise ValueError("Herdr disconnected before replying")
            response.extend(chunk)
            if len(response) > 2 * 1024 * 1024:
                raise ValueError("Herdr response is too large")
    data = json.loads(response.split(b"\n", 1)[0])
    if not isinstance(data, dict) or data.get("id") != identifier:
        raise ValueError("Invalid Herdr response")
    if "error" in data:
        raise ValueError("Herdr could not complete the request")
    if not isinstance(data.get("result"), dict):
        raise ValueError("Invalid Herdr result")
    return data["result"]


def attention(snapshot):
    if not isinstance(snapshot, dict) or not all(isinstance(snapshot.get(key), list) for key in ("agents", "workspaces", "tabs")):
        raise ValueError("Herdr status format is unsupported")
    workspaces = {w["workspace_id"]: w.get("label", "Workspace") for w in snapshot["workspaces"]}
    tabs = {t["tab_id"]: t.get("label", "Chat") for t in snapshot["tabs"]}
    all_chats, seen = [], set()
    for agent in snapshot["agents"]:
        state = agent.get("agent_status")
        if not (agent.get("agent") or agent.get("display_agent")) or agent.get("launch_pending"):
            continue
        waiting = state == "blocked"
        ready = state == "done"
        pane = agent["pane_id"]
        if not isinstance(pane, str) or not pane or len(pane) > 128 or pane in seen:
            continue
        seen.add(pane)
        title = agent.get("name") or agent.get("title") or tabs.get(agent.get("tab_id")) or agent.get("agent") or "Chat"
        all_chats.append({"id": pane, "name": str(title)[:160],
                      "workspace": str(workspaces.get(agent.get("workspace_id"), "Workspace"))[:160],
                      "agent": str(agent.get("agent") or agent.get("display_agent"))[:40],
                      "state": "blocked" if waiting else "ready" if ready else state if state in {"working", "idle"} else "unknown",
                      "unread": state == "done"})
    all_chats.sort(key=lambda chat: (chat["state"] != "blocked", chat["workspace"].casefold(), chat["name"].casefold(), chat["id"]))
    chats = [chat for chat in all_chats if chat["state"] in {"blocked", "ready"}]
    return {"status": "available", "message": "", "chats": chats, "all_chats": all_chats,
            "waiting": sum(chat["state"] == "blocked" for chat in chats),
            "ready": sum(chat["state"] == "ready" for chat in chats)}


def snapshot():
    result = request("session.snapshot", {})
    if result.get("type") != "session_snapshot":
        raise ValueError("Unsupported Herdr snapshot response")
    return result["snapshot"]


def get_status():
    try:
        result = attention(snapshot())
        pins = pinned_ids()
        for chat in result["all_chats"]:
            chat["pinned"] = chat["id"] in pins
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"status": "unavailable", "message": "Herdr is unavailable. Open Herdr to reconnect.",
                "chats": [], "all_chats": [], "waiting": 0, "ready": 0}


def focus_chat(pane):
    if not isinstance(pane, str) or not any(chat["id"] == pane for chat in attention(snapshot())["all_chats"]):
        raise ValueError("This chat is no longer available. Refresh its status.")
    request("agent.focus", {"target": pane})
