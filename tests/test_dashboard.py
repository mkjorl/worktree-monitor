import json
import concurrent.futures
import http.client
import ssl
import threading
import re
import tempfile
import unittest
from http.client import HTTPConnection
from types import SimpleNamespace
from unittest.mock import Mock, patch

import dashboard
import workspace_operations


class DashboardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = dashboard.Path(temporary.name)
        for name, path in (("OPERATIONS", self.root / "operations"), ("LOGS", self.root / "logs")):
            patcher = patch.object(workspace_operations, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        def validate_name(name):
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
                raise RuntimeError("Invalid workspace name")
            return name
        lifecycle = SimpleNamespace(
            ProjectError=RuntimeError,
            RepositoryContext=Mock(), BaseEnvironment=Mock(),
            validate_project_name=Mock(side_effect=validate_name),
            normalized_database_name=Mock(side_effect=lambda name: "showpass_" + name.replace("-", "_")),
            validate_state_context=Mock(), load_all_states=Mock(return_value=[]),
            process_resource_usage=Mock(return_value={}),
            REDIS_DATABASE_COUNT=8192, REDIS_SLOT_SIZE=64,
            PROJECT_MARKER=".showpass-workspace.json", STATE_VERSION=1,
            configured_checkout=Mock(return_value=dashboard.Path("/tmp/base")),
            state_root=Mock(return_value=self.root),
        )
        lifecycle.get_lifecycle = Mock(return_value=lifecycle)
        adapter = patch.object(dashboard, "lifecycle", lifecycle)
        adapter.start()
        self.addCleanup(adapter.stop)
        settings = patch.object(dashboard, "get_settings", return_value={"directories": ["/tmp/workspaces"], "showpass_checkout": "/tmp/base", "docker_socket": ""})
        settings.start()
        self.addCleanup(settings.stop)

    def test_http_action_runs_managed_worker_and_reports_completion(self):
        server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        port = server.server_port
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Origin": f"http://127.0.0.1:{port}", "X-CSRF-Token": dashboard.TOKEN}
        try:
            with patch.object(dashboard.lifecycle, "load_all_states", return_value=[{"name": "demo", "status": "running", "project_root": "/tmp/workspaces/demo"}]), patch.object(dashboard.POOL, "submit", side_effect=lambda fn: fn()), patch.object(dashboard.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as command:
                connection.request("POST", "/api/action", json.dumps({"action": "stop", "name": "demo"}), headers)
                response = connection.getresponse()
                result = json.loads(response.read())
                self.assertEqual(response.status, 202)
                connection.request("GET", "/api/jobs/" + result["job"])
                response = connection.getresponse()
                self.assertEqual(json.loads(response.read())["state"], "done")
                self.assertEqual(command.call_args.args[0], [dashboard.sys.executable, "-B", str(dashboard.Path(dashboard.__file__)), "--workspace-action", "stop", "demo", "--operation-id", result["job"], "--checkout", "/tmp/base"])
                self.assertEqual(command.call_args.kwargs["env"], dashboard.docker_env())
                self.assertNotIn("DOCKER_CONTEXT", command.call_args.kwargs["env"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join()

    def test_cpu_uses_total_capacity_and_handles_unavailable_docker_metrics(self):
        with patch.object(dashboard.os, "cpu_count", return_value=8):
            self.assertEqual(dashboard.cpu_percent(150.8), 18.9)
            self.assertEqual(dashboard.cpu_percent(800), 100)
            self.assertEqual(dashboard.cpu_percent(801), 100)
            self.assertEqual(dashboard.docker_cpu("200.00%"), "25.0%")
            for value in (None, "--", "NaN%", "-2%"):
                self.assertIsNone(dashboard.docker_cpu(value))
        with patch.object(dashboard.os, "cpu_count", return_value=None):
            self.assertEqual(dashboard.cpu_percent(50), 50)

    def test_job_polling_exposes_worker_stage_but_preserves_final_error(self):
        server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        job = "1" * 32
        try:
            with patch.dict(dashboard.JOBS, {job: {"state": "running", "message": "Started"}}), \
                 patch.object(workspace_operations, "read_job", return_value={"state": "running", "message": "Waiting for services"}):
                connection.request("GET", "/api/jobs/" + job)
                self.assertEqual(json.loads(connection.getresponse().read())["message"], "Waiting for services")
                dashboard.JOBS[job] = {"state": "error", "message": "Failed; see local log"}
                connection.request("GET", "/api/jobs/" + job)
                self.assertEqual(json.loads(connection.getresponse().read())["message"], "Failed; see local log")
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join()

    def test_reset_validates_state_and_dispatches_tracked_worker(self):
        for status in ('running', 'stopped', 'provisioned'):
            dashboard.validate_action('reset', 'demo', [{'name': 'demo', 'status': status}])
        for status in ('creating', 'destroying', 'unknown'):
            with self.assertRaises(ValueError):
                dashboard.validate_action('reset', 'demo', [{'name': 'demo', 'status': status}])
        with self.assertRaises(ValueError):
            dashboard.validate_action('reset', 'other', [{'name': 'demo', 'status': 'running'}])
        with patch.object(dashboard.lifecycle, 'load_all_states', return_value=[{'name': 'demo', 'status': 'running'}]), \
             patch.object(dashboard.lifecycle, 'configured_checkout', return_value=dashboard.Path('/tmp/base').resolve()), \
             patch.object(workspace_operations, 'run_tracked') as tracked:
            dashboard.workspace_worker(['--workspace-action', 'reset', 'demo', '--operation-id', '2' * 32, '--checkout', '/tmp/base'])
            self.assertEqual(tracked.call_args.args[2:], ('reset', 'demo', '2' * 32))

    def test_csrf_and_local_origin_are_required(self):
        headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765", "X-CSRF-Token": "ok"}
        with patch.object(dashboard, "TOKEN", "ok"):
            dashboard.validate_local_headers(headers, 8765, post=True)
            for change in ({"X-CSRF-Token": "bad"}, {"Origin": "http://attacker.test"}, {"Host": "localhost:8765"}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    dashboard.validate_local_headers(headers | change, 8765, post=True)

    def test_registry_allowlist_and_missing_docker_prevent_action(self):
        states = [{"name": "demo", "status": "stopped"}]
        with self.assertRaises(ValueError):
            dashboard.validate_action("stop", "unregistered", states)
        dashboard.validate_action("start", "demo", states)
        with patch.object(dashboard, "docker", side_effect=RuntimeError("Docker unavailable")):
            with self.assertRaisesRegex(ValueError, "Docker"):
                dashboard.ensure_docker_available()

    def test_destroy_requires_registered_safe_name(self):
        with self.assertRaises(ValueError):
            dashboard.validate_action("destroy", "../demo", [{"name": "../demo"}])
        with self.assertRaises(ValueError):
            dashboard.validate_action("destroy", "unregistered", [{"name": "demo"}])

    def test_destroy_dispatches_managed_worker_without_shell(self):
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch("dashboard.subprocess.run", return_value=completed) as command:
            dashboard.perform_action("d" * 32, "destroy", "demo")
        args = command.call_args.args[0]
        self.assertEqual(args, [dashboard.sys.executable, "-B", str(dashboard.Path(dashboard.__file__)), "--workspace-action", "destroy", "demo", "--operation-id", "d" * 32, "--checkout", "/tmp/base"])
        self.assertEqual(command.call_args.kwargs["env"], dashboard.docker_env())
        self.assertNotIn("shell", command.call_args.kwargs)

    def test_packaged_dispatch_uses_bundled_executable_without_python_flags(self):
        with patch.object(dashboard.sys, "frozen", True, create=True), \
             patch("dashboard.subprocess.run", return_value=SimpleNamespace(returncode=0)) as command:
            dashboard.perform_action("f" * 32, "stop", "demo")
        self.assertEqual(command.call_args.args[0], [dashboard.sys.executable, "--workspace-action", "stop", "demo", "--operation-id", "f" * 32, "--checkout", "/tmp/base"])
        self.assertTrue(command.call_args.kwargs["start_new_session"])
        self.assertEqual(command.call_args.kwargs["cwd"], dashboard.Path("/tmp/base"))

    def test_destroy_preflight_rejects_wrong_workspace_root_and_remote_native_database(self):
        context = SimpleNamespace(workspace=dashboard.Path("/tmp/workspaces"), backend=dashboard.Path("/tmp/web-app"))
        state = {"name": "demo", "project_root": "/tmp/elsewhere/demo", "base_backend_path": "/tmp/web-app",
                 "postgres_mode": "docker", "postgres_database": "showpass_demo",
                 "clickhouse_database": "showpass_demo", "centrifugo_container": "sp-centrifugo-demo", "redis_base": 64}
        with patch.object(dashboard.lifecycle.RepositoryContext, "discover", return_value=context), \
             patch.object(dashboard.lifecycle, "validate_state_context"), \
             patch.object(dashboard.lifecycle, "load_all_states", return_value=[state]):
            with self.assertRaisesRegex(ValueError, "unsafe workspace folder"):
                dashboard.validate_destroy_target(state)

            state.update(project_root="/tmp/workspaces/demo", postgres_mode="native")
            with patch.object(dashboard.lifecycle, "BaseEnvironment", return_value=SimpleNamespace(postgres_host="db.example.com")):
                with self.assertRaisesRegex(ValueError, "non-local native PostgreSQL"):
                    dashboard.validate_destroy_target(state)

    def test_destroy_http_action_requires_explicit_confirmation(self):
        server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Origin": f"http://127.0.0.1:{server.server_port}", "X-CSRF-Token": dashboard.TOKEN}
        state = {"name": "demo", "status": "stopped", "project_root": "/tmp/workspaces/demo"}
        try:
            with patch.object(dashboard.lifecycle, "load_all_states", return_value=[state]), \
                 patch.object(workspace_operations, "operation_for", return_value={"busy": False}), \
                 patch.object(dashboard, "validate_destroy_target", return_value=state), \
                 patch.object(dashboard, "ensure_docker_available"), \
                 patch.object(dashboard, "ACTIVE_NAMES", set()), \
                 patch.object(dashboard.POOL, "submit") as submit:
                for body in ({"action": "destroy", "name": "demo"},
                             {"action": "destroy", "name": "demo", "confirmed": False},
                             {"action": "destroy", "name": "demo", "confirmed": "true"}):
                    connection.request("POST", "/api/action", json.dumps(body), headers)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    self.assertIn("Confirm deletion", json.loads(response.read())["error"])
                submit.assert_not_called()

                connection.request("POST", "/api/action", json.dumps({"action": "destroy", "name": "demo", "confirmed": True}), headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 202)
                self.assertIn("job", json.loads(response.read()))
                submit.assert_called_once()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join()

    def test_bulk_destroy_validates_all_targets_before_queueing_jobs(self):
        server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Origin": f"http://127.0.0.1:{server.server_port}", "X-CSRF-Token": dashboard.TOKEN}
        states = [{"name": name, "status": "stopped", "project_root": f"/tmp/workspaces/{name}"}
                  for name in ("one", "two")]
        submitted = []
        try:
            with patch.object(dashboard.lifecycle, "load_all_states", return_value=states), \
                 patch.object(workspace_operations, "operation_for", return_value={"busy": False}), \
                 patch.object(dashboard, "validate_destroy_target", side_effect=lambda state: state) as validate_target, \
                 patch.object(dashboard, "ensure_docker_available") as ensure_docker, \
                 patch.object(dashboard, "ACTIVE_NAMES", set()) as active_names, \
                 patch.object(dashboard, "JOBS", {}), \
                 patch.object(dashboard.POOL, "submit", side_effect=submitted.append) as submit:
                connection.request("POST", "/api/bulk-destroy", json.dumps({"names": ["one"], "confirmed": True}), headers)
                response = connection.getresponse()
                result = json.loads(response.read())
                self.assertEqual(response.status, 202)
                self.assertEqual([item["name"] for item in result["jobs"]], ["one"])
                self.assertEqual(validate_target.call_count, 1)
                ensure_docker.assert_called_once()
                submit.assert_called_once()
                self.assertEqual(active_names, {"managed:one"})
                active_names.clear()

                connection.request("POST", "/api/bulk-destroy", json.dumps({"names": ["one", "two"], "confirmed": True}), headers)
                response = connection.getresponse()
                result = json.loads(response.read())
                self.assertEqual(response.status, 202)
                self.assertEqual([item["name"] for item in result["jobs"]], ["one", "two"])
                self.assertEqual(len({item["job"] for item in result["jobs"]}), 2)
                self.assertEqual(validate_target.call_count, 3)
                self.assertEqual(ensure_docker.call_count, 2)
                self.assertEqual(submit.call_count, 3)
                self.assertEqual(active_names, {"managed:one", "managed:two"})

                connection.request("POST", "/api/bulk-destroy", json.dumps({"names": ["one", "missing"], "confirmed": True}), headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                self.assertEqual(submit.call_count, 3)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join()

    def test_bulk_destroy_rejects_busy_duplicate_and_unsafe_selections(self):
        server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Origin": f"http://127.0.0.1:{server.server_port}", "X-CSRF-Token": dashboard.TOKEN}
        states = [{"name": name, "status": "stopped", "project_root": f"/tmp/workspaces/{name}"}
                  for name in ("one", "two")]
        def validate_target(state):
            if state["name"] == "two":
                raise ValueError("Unsafe target")
            return state

        try:
            with patch.object(dashboard.lifecycle, "load_all_states", return_value=states), \
                 patch.object(workspace_operations, "operation_for", return_value={"busy": False}), \
                 patch.object(dashboard, "validate_destroy_target", side_effect=validate_target), \
                 patch.object(dashboard, "ensure_docker_available") as ensure_docker, \
                 patch.object(dashboard, "ACTIVE_NAMES", set()), \
                 patch.object(dashboard.POOL, "submit") as submit:
                for body in ({"names": ["one", "two"]},
                             {"names": [], "confirmed": True},
                             {"names": ["one", "one"], "confirmed": True},
                             {"names": ["one", "two"], "confirmed": "true"}):
                    connection.request("POST", "/api/bulk-destroy", json.dumps(body), headers)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                connection.request("POST", "/api/bulk-destroy", json.dumps({"names": ["one", "two"], "confirmed": True}), headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                self.assertIn("Unsafe target", json.loads(response.read())["error"])
                submit.assert_not_called()
                ensure_docker.assert_not_called()

            with patch.object(dashboard.lifecycle, "load_all_states", return_value=states), \
                 patch.object(workspace_operations, "operation_for", return_value={"busy": True, "stage": "Busy"}), \
                 patch.object(dashboard.POOL, "submit") as submit:
                connection.request("POST", "/api/bulk-destroy", json.dumps({"names": ["one", "two"], "confirmed": True}), headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 409)
                submit.assert_not_called()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join()

    def test_destroy_rejects_shared_or_mismatched_resources(self):
        context = SimpleNamespace(workspace=dashboard.Path("/tmp/workspaces"), backend=dashboard.Path("/tmp/web-app"))
        safe = {"name": "demo", "project_root": "/tmp/workspaces/demo", "postgres_mode": "docker",
                "postgres_database": "showpass_demo", "clickhouse_database": "showpass_demo",
                "centrifugo_container": "sp-centrifugo-demo", "redis_base": 64}
        with patch.object(dashboard.lifecycle.RepositoryContext, "discover", return_value=context), \
             patch.object(dashboard.lifecycle, "validate_state_context"), \
             patch.object(dashboard.Path, "exists", return_value=False), \
             patch.object(dashboard.Path, "is_mount", return_value=False), \
             patch.object(dashboard.Path, "is_symlink", return_value=False):
            for change in ({"postgres_database": "postgres"}, {"clickhouse_database": "default"},
                           {"centrifugo_container": "sp-postgres"}, {"redis_base": 0}, {"redis_base": 65}):
                with self.subTest(change=change), patch.object(dashboard.lifecycle, "load_all_states", return_value=[safe]), self.assertRaises(ValueError):
                    dashboard.validate_destroy_target(safe | change)
            with patch.object(dashboard.lifecycle, "load_all_states", return_value=[safe, {"name": "other", "redis_base": 64}]), self.assertRaisesRegex(ValueError, "shared"):
                dashboard.validate_destroy_target(safe)

    def test_action_error_does_not_expose_cli_output(self):
        completed = type("Result", (), {"returncode": 1, "stdout": "secret=token", "stderr": "bad"})()
        with patch("dashboard.subprocess.run", return_value=completed):
            dashboard.perform_action("e" * 32, "stop", "demo")
        self.assertEqual(dashboard.JOBS["e" * 32]["state"], "error")
        self.assertNotIn("secret", dashboard.JOBS["e" * 32]["message"])

    def test_child_process_metrics_are_added_to_matching_native_service(self):
        usage = {"frontend": {"pids": [10], "cpu_percent": 2.0, "rss_kb": 1000}}
        ps = type("Result", (), {"returncode": 0, "stdout": "10 1 501 2.0 1000\n11 10 501 0.5 500\n12 2 501 9.0 9000\n"})()
        with patch.object(dashboard.lifecycle, "process_resource_usage", return_value=usage), patch("dashboard.subprocess.run", return_value=ps), patch.object(dashboard.os, "getuid", return_value=501):
            result = dashboard.native_usage({"name": "demo"})
        self.assertEqual(result["frontend"]["cpu_percent"], 2.5)
        self.assertEqual(result["frontend"]["rss_kb"], 1500)
        self.assertEqual(result["frontend"]["pids"], [10, 11])

    def test_snapshot_excludes_environment_and_unknown_state_fields(self):
        state = {"name": "demo", "project_root": "/tmp/workspaces/demo", "status": "stopped", "hostname": "demo.local", "backend_port": 1,
                 "frontend_port": 2, "centrifugo_port": 3, "postgres_mode": "native", "DATABASE_URL": "secret",
                 "base_env": {"TOKEN": "secret"}, "centrifugo_container": "sp-centrifugo-demo"}
        with patch.object(dashboard.lifecycle, "load_all_states", return_value=[state]), \
             patch.object(dashboard, "native_usage", return_value={"backend": {"cpu_percent": 150.8, "rss_kb": 1000, "pids": [10]}}), \
             patch.object(dashboard.os, "cpu_count", return_value=8), \
             patch.object(dashboard, "docker_data", return_value={"status": "available", "message": "", "containers": []}):
            data = dashboard.snapshot()
        self.assertNotIn("secret", json.dumps(data))
        self.assertNotIn("base_env", json.dumps(data))
        self.assertEqual(data["workspaces"][0]["cpu_percent"], 18.9)
        self.assertEqual(data["workspaces"][0]["services"]["backend"]["cpu_percent"], 18.9)

    def test_health_reports_actual_probe_results_and_stopped_state(self):
        self.assertEqual(dashboard.workspace_health({"status": "stopped"}, [])["status"], "stopped")
        checks = [concurrent.futures.Future() for _ in range(3)]
        for check, result in zip(checks, (True, False, True)):
            check.set_result(result)
        health = dashboard.workspace_health({"status": "running"}, checks)
        self.assertEqual(health["status"], "partial")
        self.assertEqual(health["detail"], "backend, local HTTPS route responding; frontend unavailable.")

    def test_health_http_probe_uses_loopback_and_fails_closed(self):
        response = SimpleNamespace(status=200, read=lambda count: b"", close=lambda: None)
        with patch.object(dashboard.http.client, "HTTPConnection") as connection_type:
            connection_type.return_value.getresponse.return_value = response
            self.assertTrue(dashboard.probe_health(8123, "/"))
            connection_type.assert_called_once_with("127.0.0.1", 8123, timeout=0.7)
            connection_type.return_value.request.assert_called_once_with("HEAD", "/")
        self.assertFalse(dashboard.probe_health(8123, hostname="example.com"))
        self.assertFalse(dashboard.probe_health(8123, hostname="bad\nhost", https=True))

    def test_https_health_probe_connects_loopback_with_verified_tls(self):
        raw_socket, wrapped_socket = object(), SimpleNamespace(close=lambda: None)
        context = SimpleNamespace(wrap_socket=Mock(return_value=wrapped_socket),
                                  load_verify_locations=lambda **kwargs: None)
        with (patch.object(dashboard.ssl, "create_default_context", return_value=context),
              patch.object(dashboard.socket, "create_connection", return_value=raw_socket) as create_connection,
              patch.object(dashboard.LocalHTTPSConnection, "send", autospec=True,
                           side_effect=lambda connection, data: connection.connect() if connection.sock is None else None),
              patch.object(dashboard.LocalHTTPSConnection, "getresponse", return_value=SimpleNamespace(status=200, read=lambda count: b""))):
            self.assertTrue(dashboard.probe_health(443, "/__showpass_workspace_health", "demo.local", True))
        create_connection.assert_called_once_with(("127.0.0.1", 443), 0.7, None)
        self.assertEqual(context.wrap_socket.call_args.kwargs["server_hostname"], "demo.local")


if __name__ == "__main__":
    unittest.main()


class DiscoveryDirectoryTests(unittest.TestCase):
    def test_directory_scope_excludes_siblings_and_deduplicates_nested_roots(self):
        states = [{"name": "one", "project_root": "/tmp/workspaces/one"},
                  {"name": "two", "project_root": "/tmp/workspaces-other/two"},
                  {"name": "three", "project_root": "/tmp/elsewhere/three"},
                  {"name": "missing-root"}]
        with patch.object(dashboard.lifecycle, "load_all_states", return_value=states), \
             patch.object(dashboard, "get_settings", return_value={"directories": ["/tmp/workspaces", "/tmp/workspaces/one", "/tmp/elsewhere"]}):
            self.assertEqual([s["name"] for s in dashboard.visible_states()], ["one", "three"])
        with patch.object(dashboard.lifecycle, "load_all_states", return_value=states), \
             patch.object(dashboard, "get_settings", return_value={"directories": []}):
            self.assertEqual(dashboard.visible_states(), [])
