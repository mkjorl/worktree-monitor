import json
import subprocess
import threading
import unittest
from http.client import HTTPConnection
from types import SimpleNamespace
from unittest.mock import Mock, patch

import dashboard


ID = "a" * 64
ENV = {"DOCKER_HOST": "unix:///tmp/test-docker.sock", "PATH": "/usr/bin"}


def result(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def inspection(state="running", name="/postgres", container_id=ID):
    return result(json.dumps({"id": container_id, "name": name, "state": state}))


class DockerTests(unittest.TestCase):
    def setUp(self):
        self.docker = self.enterContext(patch.object(dashboard, "docker"))
        self.docker.side_effect = AssertionError("Unexpected Docker call")
        self.lifecycle = self.enterContext(patch.object(dashboard, "lifecycle", Mock()))
        self.lifecycle.load_all_states.return_value = []
        self.enterContext(patch.dict(dashboard.JOBS, clear=True))
        self.enterContext(patch.object(dashboard, "ACTIVE_NAMES", set()))
        self.enterContext(patch.object(dashboard, "DOCKER_OPERATION", {}))

    def test_requests_reject_flags_remote_targets_and_unrecognized_actions(self):
        self.assertEqual(dashboard.validate_docker_request({"scope": "container", "action": "pause", "id": ID}), ("container", "pause", ID))
        self.assertEqual(dashboard.validate_docker_request({"scope": "desktop", "action": "start"}), ("desktop", "start", None))
        for body in ({"scope": "container", "action": "stop", "id": "--all"},
                     {"scope": "container", "action": "stop", "id": "a" * 12},
                     {"scope": "container", "action": "rm", "id": ID},
                     {"scope": "container", "action": "start", "id": ID, "host": "ssh://prod"},
                     {"scope": "desktop", "action": "pause"},
                     {"scope": "desktop", "action": []},
                     {"scope": "desktop", "action": "start", "id": ID}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                dashboard.validate_docker_request(body)

    def test_command_always_pins_local_socket_and_strips_remote_context(self):
        with patch.object(dashboard, "get_settings", return_value={"docker_socket": "/tmp/local.sock"}), \
             patch.object(dashboard.subprocess, "run", return_value=result()) as run:
            # Call the real helper through the patch's saved original.
            DockerTests.real_docker(["container", "start", ID])
            args, options = run.call_args.args[0], run.call_args.kwargs
            self.assertEqual(args, ["docker", "--host", "unix:///tmp/local.sock", "container", "start", ID])
            self.assertEqual(options["env"]["DOCKER_HOST"], "unix:///tmp/local.sock")
            self.assertFalse({"DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"} & options["env"].keys())
            self.assertNotIn("shell", options)
        with patch.object(dashboard, "get_settings", return_value={"docker_socket": "ssh://prod"}), self.assertRaises(ValueError):
            dashboard.docker_env()

    def test_desktop_states_and_unsupported_cli(self):
        self.docker.side_effect = [result('{"Status":"running"}'), result('{"Status":"stopped"}'),
                                   result('{"Status":"starting"}'), result("not json"),
                                   result(stderr="unknown command desktop", returncode=1)]
        self.assertEqual(dashboard.desktop_data()["actions"], ["stop", "restart"])
        self.assertEqual(dashboard.desktop_data()["actions"], ["start"])
        for _ in range(3):
            self.assertEqual(dashboard.desktop_data()["actions"], [])
        self.docker.side_effect = [result(stderr="Could not retrieve status. Is Docker Desktop running?", returncode=1), result()]
        dashboard.perform_docker_action("job", "desktop", "start", None, ENV)
        self.assertEqual(dashboard.JOBS["job"]["state"], "done")

    def test_snapshot_supplies_full_identity_and_state_actions_without_stats(self):
        self.docker.side_effect = [result('{"Status":"running"}'),
                                   result(json.dumps({"ID": ID, "Names": "db", "State": "paused"})),
                                   result(returncode=1)]
        data = dashboard.docker_data()
        self.assertEqual(data["containers"][0]["id"], ID)
        self.assertEqual(data["containers"][0]["actions"], ["unpause"])
        self.assertIsNone(data["containers"][0]["cpu"])
        self.assertIn("--no-trunc", self.docker.call_args_list[1].args[0])

    def test_container_actions_reinspect_identity_and_state_before_mutating(self):
        for action, state in (("start", "exited"), ("start", "created"), ("stop", "running"),
                              ("restart", "running"), ("pause", "running"), ("unpause", "paused")):
            with self.subTest(action=action, state=state):
                self.docker.reset_mock()
                self.docker.side_effect = [inspection(state), result()]
                dashboard.perform_docker_action("job", "container", action, ID, ENV)
                self.assertEqual(dashboard.JOBS["job"]["state"], "done")
                command = ["container", action] + (["--timeout", "10"] if action in {"stop", "restart"} else []) + [ID]
                self.assertEqual(self.docker.call_args.args[0], command)
                self.assertIs(self.docker.call_args.kwargs["env"], ENV)
                self.assertEqual(self.docker.call_args_list[0].args[0][0:2], ["container", "inspect"])
        for data in (inspection("exited"), inspection(container_id="b" * 64), result(returncode=1), result("not json")):
            self.docker.reset_mock()
            self.docker.side_effect = [data]
            dashboard.perform_docker_action("job", "container", "stop", ID, ENV)
            self.assertEqual(dashboard.JOBS["job"]["state"], "error")
            self.assertEqual(self.docker.call_count, 1)

    def test_managed_container_and_busy_workspace_are_protected(self):
        self.lifecycle.load_all_states.return_value = [{"name": "hidden-workspace", "centrifugo_container": "sp-centrifugo-hidden"}]
        with patch.object(dashboard.workspace_operations, "operation_for", return_value={"busy": False}):
            self.docker.side_effect = [inspection(name="/sp-centrifugo-hidden")]
            dashboard.perform_docker_action("job", "container", "stop", ID, ENV)
            self.assertIn("workspace controls", dashboard.JOBS["job"]["message"])
        self.docker.reset_mock()
        with patch.object(dashboard.workspace_operations, "operation_for", return_value={"busy": True}):
            dashboard.perform_docker_action("job", "desktop", "stop", None, ENV)
            self.assertIn("active workspace", dashboard.JOBS["job"]["message"])
            self.docker.assert_not_called()

    def test_desktop_dispatch_and_errors(self):
        for action, status in (("start", "stopped"), ("stop", "running"), ("restart", "running")):
            self.docker.side_effect = [result(json.dumps({"Status": status})), result()]
            dashboard.perform_docker_action("job", "desktop", action, None, ENV)
            self.assertEqual(dashboard.JOBS["job"]["state"], "done")
            self.assertEqual(self.docker.call_args.args[0], ["desktop", action, "--timeout", "60"])
        for failure in (result(stderr="service refused", returncode=1), subprocess.TimeoutExpired("docker", 25)):
            self.docker.side_effect = [inspection(), failure]
            dashboard.perform_docker_action("job", "container", "stop", ID, ENV)
            self.assertEqual(dashboard.JOBS["job"]["state"], "error")
            self.assertTrue(dashboard.JOBS["job"]["message"])

    def test_http_origin_validation_job_tracking_and_conflict_cleanup(self):
        server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Origin": f"http://127.0.0.1:{server.server_port}", "X-CSRF-Token": dashboard.TOKEN}
        body = {"scope": "container", "action": "pause", "id": ID}
        def post(payload=body, custom_headers=headers):
            connection.request("POST", "/api/docker/action", json.dumps(payload), custom_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        try:
            with patch.object(dashboard, "docker_env", return_value=ENV), patch.object(dashboard.POOL, "submit") as submit:
                self.assertEqual(post(custom_headers={})[0], 403)
                self.assertEqual(post(body | {"id": "--all"})[0], 400)
                submit.assert_not_called()
                status, data = post()
                self.assertEqual(status, 202)
                self.assertEqual(dashboard.DOCKER_OPERATION["job"], data["job"])
                self.assertEqual(post()[0], 409)
                handler = dashboard.Handler.__new__(dashboard.Handler)
                handler.respond = Mock()
                handler.queue_action("start", "example")
                self.assertEqual(handler.respond.call_args.args[0], 409)
                self.docker.side_effect = [inspection(), result()]
                submit.call_args.args[0]()
                self.assertNotIn("docker", dashboard.ACTIVE_NAMES)
                connection.request("GET", "/api/jobs/" + data["job"])
                self.assertEqual(json.loads(connection.getresponse().read())["state"], "done")
                dashboard.ACTIVE_NAMES.add("managed:demo")
                self.assertEqual(post()[0], 409)
                dashboard.ACTIVE_NAMES.clear()
                submit.side_effect = RuntimeError("pool closed")
                self.assertEqual(post()[0], 503)
                self.assertNotIn("docker", dashboard.ACTIVE_NAMES)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join()


DockerTests.real_docker = staticmethod(dashboard.docker)
