import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from unittest.mock import patch

import dashboard
import herdr_status


def sample():
    def agent(pane, state, **values):
        return {"pane_id": pane, "agent_status": state, "agent": "codex", "workspace_id": "w1", "tab_id": "t1", **values}
    return {"workspaces": [{"workspace_id": "w1", "label": "My workspace"}],
            "tabs": [{"tab_id": "t1", "label": "Review"}],
            "agents": [agent("blocked", "blocked"), agent("done", "done"),
                       agent("completed-idle", "idle", completion_seq=1),
                       agent("untouched-idle", "idle"), agent("working", "working"),
                       agent("unknown", "unknown"), agent("launching", "done", launch_pending=True),
                       agent("shell", "idle", agent=None, completion_seq=1)]}


class HerdrTests(unittest.TestCase):
    def test_bookmarks_persist_and_are_shared_across_status_views(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(herdr_status, "PINS_PATH", Path(folder)/"pins.json"), patch.object(herdr_status, "snapshot", return_value=sample()):
            self.assertFalse(any(c["pinned"] for c in herdr_status.get_status()["all_chats"]))
            self.assertEqual(herdr_status.set_pin("done", True), {"pinned": True})
            herdr_status.set_pin("done", True)
            self.assertEqual(json.loads(herdr_status.PINS_PATH.read_text()), ["done"])
            status = herdr_status.get_status()
            self.assertTrue(next(c for c in status["chats"] if c["id"]=="done")["pinned"])
            self.assertTrue(next(c for c in status["all_chats"] if c["id"]=="done")["pinned"])
            for pane, pinned in [("missing",True),("done",1),("",False),([],True)]:
                with self.assertRaises(ValueError): herdr_status.set_pin(pane,pinned)
            self.assertEqual(herdr_status.pinned_ids(), {"done"})
            with patch.object(herdr_status, "snapshot", return_value={}):
                with self.assertRaises(ValueError): herdr_status.set_pin("working", True)
            herdr_status.set_pin("working",True)
            herdr_status.set_pin("done",False)
            self.assertEqual(herdr_status.pinned_ids(), {"working"})
            # Pin storage is independent of app integration preferences.
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["pins.json"])

    def test_only_questions_and_completed_chats_count_and_questions_sort_first(self):
        data = herdr_status.attention(sample())
        self.assertEqual((data["waiting"], data["ready"]), (1, 1))
        self.assertEqual([chat["id"] for chat in data["chats"]], ["blocked", "done"])
        self.assertEqual(data["chats"][0]["workspace"], "My workspace")
        self.assertEqual(data["chats"][0]["name"], "Review")
        self.assertEqual(set(data["chats"][0]), {"id", "name", "workspace", "agent", "state", "unread"})

    def test_unread_is_distinct_from_ready_and_all_chats_include_live_activity(self):
        data = herdr_status.attention(sample())
        all_chats = {chat["id"]: chat for chat in data["all_chats"]}
        self.assertEqual(len(all_chats), 6)
        self.assertTrue(all_chats["done"]["unread"])
        self.assertEqual(all_chats["done"]["state"], "ready")
        self.assertFalse(all_chats["completed-idle"]["unread"])
        self.assertEqual(all_chats["completed-idle"]["state"], "idle")
        self.assertEqual(all_chats["untouched-idle"]["state"], "idle")
        self.assertEqual(all_chats["working"]["state"], "working")
        self.assertEqual(all_chats["unknown"]["state"], "unknown")
        self.assertNotIn("launching", all_chats)
        self.assertNotIn("shell", all_chats)

    def test_current_status_overrides_completion_history(self):
        data = sample()
        data["agents"] = [{**data["agents"][1], "completion_seq":133}]
        for state, expected in [("done","ready"),("idle","idle"),("working","working"),("blocked","blocked"),("idle","idle")]:
            data["agents"][0]["agent_status"] = state
            result = herdr_status.attention(data)
            self.assertEqual(result["all_chats"][0]["state"],expected)
            self.assertEqual(result["ready"],int(state=="done"))
            self.assertEqual(result["all_chats"][0]["unread"],state=="done")
            self.assertEqual(bool(result["chats"]),state in {"done","blocked"})

    def test_duplicate_panes_and_false_completion_are_not_counted(self):
        data = sample()
        data["agents"] += [data["agents"][0], {**data["agents"][3], "completion_seq": True}]
        self.assertEqual(len(herdr_status.attention(data)["chats"]), 2)

    def test_unavailable_or_bad_data_is_unknown_instead_of_a_successful_zero(self):
        for error in (FileNotFoundError(), TimeoutError(), ValueError(), PermissionError()):
            with patch.object(herdr_status, "snapshot", side_effect=error):
                self.assertEqual(herdr_status.get_status()["status"], "unavailable")
        for malformed in ({}, {"agents": None}, {"agents": [None], "workspaces": [], "tabs": []}):
            with patch.object(herdr_status, "snapshot", return_value=malformed):
                self.assertEqual(herdr_status.get_status()["status"], "unavailable")

    def test_focus_rechecks_waiting_membership_and_sends_only_focus(self):
        with patch.object(herdr_status, "request", side_effect=[{"type": "session_snapshot", "snapshot": sample()}, {"type": "ok"}]) as request:
            herdr_status.focus_chat("blocked")
            self.assertEqual(request.call_args.args, ("agent.focus", {"target": "blocked"}))
        for pane in ("unknown-pane", "shell", "launching", {"method": "pane.run"}):
            with patch.object(herdr_status, "request", return_value={"type": "session_snapshot", "snapshot": sample()}) as request:
                with self.assertRaises(ValueError):
                    herdr_status.focus_chat(pane)
                self.assertLessEqual(request.call_count, 1)
        with patch.object(herdr_status, "request", side_effect=[{"type": "session_snapshot", "snapshot": sample()}, {"type": "ok"}]) as request:
            herdr_status.focus_chat("working")
            self.assertEqual(request.call_args.args, ("agent.focus", {"target": "working"}))

    def serve(self, reply):
        temporary = tempfile.TemporaryDirectory(prefix="wm-herdr-", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        config = Path(temporary.name)
        (config / "herdr").mkdir()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(config / "herdr/herdr.sock"))
        server.listen(1)
        self.addCleanup(server.close)
        received = []
        def worker():
            connection, _ = server.accept()
            with connection:
                data = json.loads(connection.makefile("rb").readline())
                received.append(data)
                response = reply(data)
                connection.sendall(response)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return config, received, thread

    def test_real_local_socket_protocol_without_cli_or_remote_environment(self):
        config, received, thread = self.serve(lambda data: json.dumps({"id": data["id"], "result": {"type": "session_snapshot", "snapshot": sample()}}).encode() + b"\n")
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config), "HERDR_REMOTE": "prod", "HERDR_SOCKET": "/not-the-local-socket"}):
            status = herdr_status.get_status()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(status["waiting"], 1)
        self.assertEqual(received[0]["method"], "session.snapshot")
        self.assertEqual(received[0]["params"], {})

    def test_invalid_transport_responses_are_rejected(self):
        for reply in (lambda data: b'not-json\n',
                      lambda data: b'{"id":"wrong","result":{}}\n',
                      lambda data: json.dumps({"id": data["id"], "error": {"message": "No"}}).encode() + b"\n"):
            config, _, thread = self.serve(reply)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config)}):
                self.assertEqual(herdr_status.get_status()["status"], "unavailable")
            thread.join(3)

    def test_non_socket_file_is_never_opened_as_an_api(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            config = Path(folder)
            (config / "herdr").mkdir()
            (config / "herdr/herdr.sock").write_text("not a socket")
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": folder}), patch.object(herdr_status.socket, "socket") as connection:
                self.assertEqual(herdr_status.get_status()["status"], "unavailable")
                connection.assert_not_called()

    def test_http_poll_and_focus_keep_origin_csrf_and_payload_guards(self):
        server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Origin": f"http://127.0.0.1:{server.server_port}", "X-CSRF-Token": dashboard.TOKEN}
        try:
            with patch.object(herdr_status, "get_status", return_value=herdr_status.attention(sample())), patch.object(herdr_status, "focus_chat") as focus:
                connection.request("GET", "/api/herdr")
                data = json.loads(connection.getresponse().read())
                self.assertEqual(data["csrf"], dashboard.TOKEN)
                self.assertEqual(data["waiting"], 1)
                for body, request_headers, status in (({"id": "blocked"}, {}, 403), ({"id": "blocked", "method": "pane.run"}, headers, 400)):
                    connection.request("POST", "/api/herdr/focus", json.dumps(body), request_headers)
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, status)
                    focus.assert_not_called()
                connection.request("POST", "/api/herdr/focus", '{"id":"blocked"}', headers)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                focus.assert_called_once_with("blocked")
                with patch.object(herdr_status, "set_pin", return_value={"pinned":True}) as pin:
                    for path in ["/api/herdr/pin", "/deck/api/pin"]:
                        for body, request_headers, code in [({"id":"blocked","pinned":True},{},403),({"id":"blocked","pinned":True,"method":"run"},headers,400),({"id":"blocked","pinned":True},headers,200)]:
                            connection.request("POST",path,json.dumps(body),request_headers)
                            response=connection.getresponse();response.read();self.assertEqual(response.status,code)
                    self.assertEqual(pin.call_count,2)
                    pin.assert_called_with("blocked",True)
                    self.assertEqual(focus.call_count,1)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join()
