"""Opt-in, paired, same-LAN chat status and focus; no workspace control routes."""
import hashlib
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import subprocess
import threading
import time

import herdr_status
from preferences import get_settings

PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def local_address():
    for interface in ("en0", "en1", "en2"):
        result = subprocess.run(["/usr/sbin/ipconfig", "getifaddr", interface], capture_output=True, text=True, timeout=1, check=False)
        try:
            address = ipaddress.IPv4Address(result.stdout.strip())
        except ValueError:
            continue
        if any(address in network for network in PRIVATE_NETWORKS):
            return str(address)
    raise ValueError("Connect your Mac to a private Wi-Fi or local network, then try again.")


def page():
    return Path(__file__).with_name("deck.html").read_bytes()


ASSETS = {
    "/deck/icon-180.png": ("deck-icon-180.png", "image/png"),
    "/deck/icon-512.png": ("deck-icon-512.png", "image/png"),
    "/deck/manifest.webmanifest": ("deck.webmanifest", "application/manifest+json"),
}


def asset(path):
    filename, content_type = ASSETS[path]
    return Path(__file__).with_name(filename).read_bytes(), content_type


def status():
    return {**herdr_status.get_status(), "appearance": get_settings().get("appearance", "system")}


def focus(pane):
    herdr_status.focus_chat(pane)
    result = subprocess.run(["/usr/bin/open", "-b", "so.pen.herdr-gpui"], capture_output=True, text=True, timeout=3, check=False)
    if result.returncode:
        raise ValueError("Chat selected, but Herdr could not be brought forward. Open Herdr on your Mac.")


class PhoneServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, service):
        self.service = service
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, PhoneHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class PhoneAccess:
    def __init__(self, status_reader=status, focus_chat=focus, pin_chat=herdr_status.set_pin):
        self.lock = threading.RLock()
        self.server = None
        self.origin = ""
        self.pair_token = ""
        self.pair_expires = 0
        self.sessions = {}
        self.status_reader = status_reader
        self.focus_chat = focus_chat
        self.pin_chat = pin_chat
        self.focusing = threading.Lock()

    def info(self):
        with self.lock:
            pair = self.origin + "/deck#pair=" + self.pair_token if self.pair_token and time.monotonic() < self.pair_expires else ""
            return {"enabled": self.server is not None, "url": self.origin + "/deck" if self.server else "", "pairing_url": pair}

    def enable(self, address=None):
        with self.lock:
            if self.server is not None:
                return self.info()
            address = local_address() if address is None else address
            ip = ipaddress.ip_address(address)
            if not (ip.is_loopback or any(ip in network for network in PRIVATE_NETWORKS)):
                raise ValueError("Phone access requires a private local-network address.")
            server = PhoneServer((address, 0), self)
            self.server = server
            self.origin = f"http://{address}:{server.server_port}"
            self.new_pairing()
            try:
                threading.Thread(target=server.serve_forever, daemon=True).start()
            except RuntimeError:
                self.server = None
                self.origin = self.pair_token = ""
                server.server_close()
                raise
            return self.info()

    def disable(self):
        with self.lock:
            server, self.server = self.server, None
            self.sessions.clear()
            self.pair_token = self.origin = ""
        if server:
            server.shutdown()
            server.server_close()
        return self.info()

    def new_pairing(self):
        with self.lock:
            if self.server is None:
                raise ValueError("Enable phone access first.")
            self.pair_token = secrets.token_urlsafe(32)
            self.pair_expires = time.monotonic() + 300
            return self.info()

    def pair(self, token, server=None):
        with self.lock:
            if self.server is None or (server is not None and self.server is not server):
                raise PermissionError("Phone access was turned off. Pair again.")
            if (not isinstance(token, str) or not token.isascii() or len(token) > 128 or not self.pair_token or time.monotonic() >= self.pair_expires
                    or not secrets.compare_digest(token, self.pair_token)):
                raise ValueError("This pairing link is invalid or expired. Create a new link on your Mac.")
            now = time.monotonic()
            self.sessions = {key: value for key, value in self.sessions.items() if value["expires"] > now}
            if len(self.sessions) >= 8:
                raise ValueError("Too many paired devices. Turn phone access off and on to reset pairing.")
            session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(session.encode()).hexdigest()] = {"csrf": csrf, "expires": now + 43200}
            self.pair_token = ""
            return session, csrf

    def authenticate(self, cookie):
        try:
            cookies = SimpleCookie(cookie)
            value = cookies.get("worktree_deck")
            token = value.value if value else ""
        except CookieError:
            return None
        if len(token) > 128:
            return None
        with self.lock:
            session = self.sessions.get(hashlib.sha256(token.encode()).hexdigest())
            return dict(session) if session and session["expires"] > time.monotonic() else None

    def select(self, pane, server, cookie, csrf):
        if not self.focusing.acquire(blocking=False):
            raise ValueError("A chat selection is already in progress.")
        try:
            with self.lock:
                session = self.authenticate(cookie)
                if self.server is not server or session is None or not secrets.compare_digest(csrf.encode(), session["csrf"].encode()):
                    raise PermissionError("Phone access expired or was turned off. Pair again.")
                self.focus_chat(pane)
        finally:
            self.focusing.release()

    def pin(self, pane, pinned, server, cookie, csrf):
        with self.lock:
            session = self.authenticate(cookie)
            if self.server is not server or session is None or not secrets.compare_digest(csrf.encode(), session["csrf"].encode()):
                raise PermissionError("Phone access expired or was turned off. Pair again.")
            return self.pin_chat(pane, pinned)


class PhoneHandler(BaseHTTPRequestHandler):
    def respond(self, code, value, cookie=None, content_type=None):
        html = isinstance(value, bytes)
        body = value if html else json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type or ("text/html; charset=utf-8" if html else "application/json; charset=utf-8"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            # The opt-in LAN listener is HTTP, restricted to chat metadata and focus.
            self.send_header("Set-Cookie", f"worktree_deck={cookie}; HttpOnly; SameSite=Strict; Path=/deck; Max-Age=43200")
        self.end_headers()
        self.wfile.write(body)

    def validate(self, post=False):
        service = self.server.service
        if service.server is not self.server:
            raise ValueError("Phone access is off.")
        address = ipaddress.ip_address(self.client_address[0])
        if not (address.is_loopback or any(address in network for network in PRIVATE_NETWORKS)):
            raise ValueError("Private local-network access only.")
        if not service.origin or self.headers.get("Host") != service.origin.removeprefix("http://"):
            raise ValueError("Invalid phone access host.")
        if post and self.headers.get("Origin") != service.origin:
            raise ValueError("Same-origin request required.")

    def do_GET(self):
        try:
            self.validate()
        except ValueError as error:
            return self.respond(403, {"error": str(error)})
        if self.path in {"/deck", "/deck/"}:
            return self.respond(200, page())
        if self.path in ASSETS:
            body, content_type = asset(self.path)
            return self.respond(200, body, content_type=content_type)
        if self.path != "/deck/api/status":
            return self.respond(404, {"error": "Not found"})
        session = self.server.service.authenticate(self.headers.get("Cookie", ""))
        if session is None:
            return self.respond(401, {"error": "Pair this phone from Worktree Monitor on your Mac."})
        try:
            self.respond(200, {**self.server.service.status_reader(), "csrf": session["csrf"]})
        except (OSError, ValueError, RuntimeError):
            self.respond(503, {"error": "Chat status is unavailable. Try again."})

    def do_POST(self):
        try:
            self.validate(post=True)
        except ValueError as error:
            return self.respond(403, {"error": str(error)})
        if self.path not in {"/deck/api/pair", "/deck/api/focus", "/deck/api/pin"}:
            return self.respond(404, {"error": "Not found"})
        service = self.server.service
        session = service.authenticate(self.headers.get("Cookie", ""))
        if self.path != "/deck/api/pair":
            if session is None:
                return self.respond(401, {"error": "Pair this phone from your Mac again."})
            if not secrets.compare_digest(self.headers.get("X-CSRF-Token", "").encode(), session["csrf"].encode()):
                return self.respond(403, {"error": "Invalid request token."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192 or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Invalid JSON request.")
            body = json.loads(self.rfile.read(length))
            expected = "token" if self.path == "/deck/api/pair" else "id"
            fields = {"id", "pinned"} if self.path == "/deck/api/pin" else {expected}
            if not isinstance(body, dict) or set(body) != fields:
                raise ValueError("Invalid request fields.")
            if expected == "token":
                cookie, csrf = service.pair(body["token"], server=self.server)
                return self.respond(200, {"csrf": csrf}, cookie=cookie)
            if self.path == "/deck/api/pin":
                if not isinstance(body["id"], str) or type(body["pinned"]) is not bool:
                    raise ValueError("Invalid bookmark.")
                return self.respond(200, service.pin(body["id"], body["pinned"], self.server, self.headers.get("Cookie", ""), self.headers.get("X-CSRF-Token", "")))
            service.select(body["id"], self.server, self.headers.get("Cookie", ""), self.headers.get("X-CSRF-Token", ""))
            return self.respond(200, {"message": "Chat selected on your Mac."})
        except PermissionError:
            self.respond(401, {"error": "Phone access expired or was turned off. Pair again from your Mac."})
        except (ValueError, TypeError, OSError, RuntimeError, subprocess.SubprocessError):
            self.respond(400, {"error": "Could not complete the request. The pairing link or chat may have changed."})

    def log_message(self, *args):
        pass


ACCESS = PhoneAccess()
