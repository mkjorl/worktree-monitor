import hashlib
from http.client import HTTPConnection, HTTPResponse
import json
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch

import dashboard
import phone_deck


class PhoneTests(unittest.TestCase):
    def setUp(self):
        self.status = {"status": "available", "all_chats": [{"id": "w1:p1", "state": "ready"}], "appearance": "dark"}
        self.focus = Mock()
        self.pin = Mock(return_value={"pinned":True})
        self.service = phone_deck.PhoneAccess(status_reader=lambda: self.status, focus_chat=self.focus, pin_chat=self.pin)
        self.addCleanup(self.service.disable)
        self.assertFalse(self.service.info()["enabled"])
        self.assertIsNone(self.service.server)

    def start(self):
        self.service.enable("127.0.0.1")
        self.origin = self.service.origin
        self.port = self.service.server.server_port

    def call(self, path, body=None, cookie="", csrf="", headers=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        values = {"Origin": self.origin, "Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf}
        values.update(headers or {})
        try:
            connection.request("POST" if body is not None else "GET", path, json.dumps(body) if body is not None else None, values)
            response = connection.getresponse()
            raw = response.read()
            data = json.loads(raw) if "application/json" in response.getheader("Content-Type", "") else raw
            return response.status, data, dict(response.getheaders())
        finally:
            connection.close()

    def pair(self):
        code, data, headers = self.call("/deck/api/pair", {"token": self.service.pair_token})
        self.assertEqual(code, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        return headers["Set-Cookie"].split(";", 1)[0], data["csrf"]

    def test_pairing_authenticated_status_and_exact_focus(self):
        self.start()
        code, page, headers = self.call("/deck")
        self.assertEqual(code, 200)
        self.assertIn(b"Chat Deck", page)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(self.call("/deck/api/status")[0], 401)
        cookie, csrf = self.pair()
        code, data, _ = self.call("/deck/api/status", cookie=cookie)
        self.assertEqual(code, 200)
        self.assertEqual(data["csrf"], csrf)
        self.assertEqual(data["all_chats"], self.status["all_chats"])
        code, _, _ = self.call("/deck/api/focus", {"id": "w1:p1"}, cookie, csrf)
        self.assertEqual(code, 200)
        self.focus.assert_called_once_with("w1:p1")

    def test_bookmarks_require_pairing_csrf_and_exact_fields_without_focus(self):
        self.start()
        body={"id":"w1:p1","pinned":True}
        self.assertEqual(self.call("/deck/api/pin",body)[0],401)
        cookie,csrf=self.pair()
        self.assertEqual(self.call("/deck/api/pin",body,cookie,"wrong")[0],403)
        for invalid in [{"id":"w1:p1"},{**body,"action":"run"},{"id":"w1:p1","pinned":1}]:
            self.assertEqual(self.call("/deck/api/pin",invalid,cookie,csrf)[0],400)
        self.pin.assert_not_called()
        code,data,_=self.call("/deck/api/pin",body,cookie,csrf)
        self.assertEqual((code,data),(200,{"pinned":True}))
        self.pin.assert_called_once_with("w1:p1",True)
        self.focus.assert_not_called()
        old=self.service.server
        self.service.disable();self.start()
        with self.assertRaises(PermissionError):self.service.pin("w1:p1",False,old,cookie,csrf)
        self.assertEqual(self.pin.call_count,1)

    def test_home_screen_assets_are_public_but_chat_status_requires_pairing(self):
        self.start()
        for path, size in [("/deck/icon-180.png", 180), ("/deck/icon-512.png", 512)]:
            code, body, headers = self.call(path)
            self.assertEqual(code, 200)
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(int.from_bytes(body[16:20], "big"), size)
            self.assertEqual(int.from_bytes(body[20:24], "big"), size)
        code, body, headers = self.call("/deck/manifest.webmanifest")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "application/manifest+json")
        manifest = json.loads(body)
        self.assertEqual(self.call(manifest["icons"][0]["src"])[0], 200)
        self.assertEqual(self.call("/deck/../dashboard.py")[0], 404)
        self.assertEqual(self.call("/deck/api/status")[0], 401)
        self.focus.assert_not_called()

    def test_pairing_is_one_use_expires_and_disable_revokes_sessions(self):
        self.start()
        token = self.service.pair_token
        cookie, csrf = self.pair()
        self.assertEqual(self.call("/deck/api/pair", {"token": token})[0], 400)
        self.service.new_pairing()
        self.service.pair_expires = time.monotonic() - 1
        self.assertEqual(self.call("/deck/api/pair", {"token": self.service.pair_token})[0], 400)
        old_server = self.service.server
        self.service.disable()
        self.assertIsNone(self.service.authenticate(cookie))
        self.assertEqual(self.service.info()["pairing_url"], "")
        self.start()
        self.assertEqual(self.call("/deck/api/status", cookie=cookie)[0], 401)
        with self.assertRaises(PermissionError):
            self.service.select("w1:p1", old_server, cookie, csrf)
        self.focus.assert_not_called()

    def test_expired_session_and_wrong_cookie_are_denied(self):
        self.start()
        cookie, csrf = self.pair()
        token = cookie.partition("=")[2]
        self.service.sessions[hashlib.sha256(token.encode()).hexdigest()]["expires"] = time.monotonic() - 1
        self.assertEqual(self.call("/deck/api/status", cookie=cookie)[0], 401)
        self.assertEqual(self.call("/deck/api/focus", {"id": "w1:p1"}, cookie, csrf)[0], 401)
        self.assertEqual(self.call("/deck/api/status", cookie="worktree_deck=wrong")[0], 401)
        self.focus.assert_not_called()

    def test_network_listener_has_no_workspace_settings_or_docker_routes(self):
        self.start()
        cookie, csrf = self.pair()
        for path in ("/api", "/api/phone", "/api/settings", "/api/action", "/api/herdr", "/api/docker/action", "/"):
            self.assertEqual(self.call(path, cookie=cookie)[0], 404, path)
            self.assertEqual(self.call(path, {"action": "stop"}, cookie, csrf)[0], 404, path)
        self.focus.assert_not_called()

    def test_origin_host_csrf_and_exact_fields_guard_focus(self):
        self.start()
        cookie, csrf = self.pair()
        cases = (({}, "", 403), ({"Origin": "http://attacker.test"}, csrf, 403), ({"Host": "attacker.test"}, csrf, 403))
        for headers, token, status in cases:
            self.assertEqual(self.call("/deck/api/focus", {"id": "w1:p1"}, cookie, token, headers)[0], status)
        for body in ({"id": "w1:p1", "action": "stop"}, [], {"method": "pane.run"}):
            self.assertEqual(self.call("/deck/api/focus", body, cookie, csrf)[0], 400)
        self.assertEqual(self.call("/deck/api/focus", {"id": "w1:p1"}, cookie, csrf, {"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.call("/deck/api/pair", {"token": "nonascii-\u00e9"})[0], 400)
        self.focus.assert_not_called()

    def test_retired_listener_request_cannot_focus_after_disable_and_reenable(self):
        self.start()
        cookie, csrf = self.pair()
        passed_auth = threading.Event()
        original = self.service.authenticate
        def record_auth(value):
            result = original(value)
            if result is not None:
                passed_auth.set()
            return result
        body = b'{"id":"w1:p1"}'
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as client, patch.object(self.service, "authenticate", side_effect=record_auth):
            request = f"POST /deck/api/focus HTTP/1.0\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {self.origin}\r\nCookie: {cookie}\r\nX-CSRF-Token: {csrf}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
            client.sendall(request.encode())
            self.assertTrue(passed_auth.wait(1))
            self.service.disable()
            self.start()
            client.sendall(body)
            response = HTTPResponse(client)
            response.begin()
            response.read()
            self.assertEqual(response.status, 401)
        self.focus.assert_not_called()

    def test_private_address_boundary_and_no_implicit_enable(self):
        with self.assertRaises(ValueError):
            self.service.enable("8.8.8.8")
        self.assertFalse(self.service.info()["enabled"])
        self.assertIsNone(self.service.server)
        with self.assertRaises(ValueError):
            self.service.new_pairing()

    def test_focus_uses_only_allowlisted_chat_and_fixed_local_app_command(self):
        with patch.object(phone_deck.herdr_status, "focus_chat", side_effect=ValueError("stale")), patch.object(phone_deck.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                phone_deck.focus("stale")
            run.assert_not_called()
        with patch.object(phone_deck.herdr_status, "focus_chat") as focus, patch.object(phone_deck.subprocess, "run", return_value=Mock(returncode=0)) as run:
            phone_deck.focus("w1:p1")
            focus.assert_called_once_with("w1:p1")
            self.assertEqual(run.call_args.args[0], ["/usr/bin/open", "-b", "so.pen.herdr-gpui"])
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_local_management_requires_existing_origin_and_csrf(self):
        server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Origin": f"http://127.0.0.1:{server.server_port}", "X-CSRF-Token": dashboard.TOKEN}
        try:
            with patch.object(phone_deck, "ACCESS", self.service), patch.object(phone_deck, "local_address", return_value="127.0.0.1"):
                for custom_headers, expected in (({}, 403), (headers, 200)):
                    connection.request("POST", "/api/phone", '{"action":"enable"}', custom_headers)
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, expected)
                self.assertTrue(self.service.info()["enabled"])
                connection.request("POST", "/api/phone", '{"action":"disable"}', headers)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                self.assertFalse(self.service.info()["enabled"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join()
