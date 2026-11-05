#!/usr/bin/env python3
"""Local-only view of Showpass workspace state and resource use."""
import json
import math
import http.client
import ipaddress
import argparse
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from preferences import get_settings, save_settings, save_appearance
from discovery import discover_projects
from project_config import get_project, save_project
from project_runtime import inspect_project, start_project, stop_project
import showpass_adapter as lifecycle
import workspace_operations
import herdr_status
import phone_deck

DOCKER_SOCKET = "unix://" + str(Path.home() / ".docker/run/docker.sock")
DOCKER_ENV = {key: value for key, value in os.environ.items() if key not in {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}}
DOCKER_ENV["DOCKER_HOST"] = DOCKER_SOCKET
TOKEN = secrets.token_urlsafe(32)
POOL = ThreadPoolExecutor(max_workers=4)
HEALTH_POOL = ThreadPoolExecutor(max_workers=8)
JOBS = {}
JOBS_LOCK = threading.Lock()
ACTIVE_NAMES = set()
# shortcut: Docker job tracking lasts for one app session; persist it if restart recovery is needed.
DOCKER_OPERATION = {}
ACTIVE_LOCK = threading.Lock()
DISCOVERY_LOCK = threading.Lock()
DISCOVERY_CACHE = {}

def docker_env():
    configured = get_settings().get("docker_socket", "")
    candidates = [Path.home() / ".docker/run/docker.sock", Path("/var/run/docker.sock")]
    socket_path = Path(configured).expanduser() if configured else next((p for p in candidates if p.is_socket()), candidates[0])
    if not socket_path.is_absolute() or str(socket_path).startswith("//"):
        raise ValueError("Docker requires an absolute local Unix socket path")
    return {**DOCKER_ENV, "DOCKER_HOST": "unix://" + str(socket_path)}


def docker(args, timeout=8, env=None):
    env = docker_env() if env is None else env
    return subprocess.run(["docker", "--host", env["DOCKER_HOST"], *args], env=env,
                          text=True, capture_output=True, timeout=timeout, check=False)


def cpu_percent(value):
    """Express per-core process usage as a share of total machine capacity."""
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("Invalid CPU measurement")
    return round(min(value / (os.cpu_count() or 1), 100), 1)


def docker_cpu(value):
    try:
        return f"{cpu_percent(value.removesuffix('%')):.1f}%" if isinstance(value, str) else None
    except ValueError:
        return None


def container_actions(state):
    return {"running": ["stop", "restart", "pause"], "paused": ["unpause"],
            "created": ["start"], "exited": ["start"], "restarting": ["stop"]}.get(state, [])


def desktop_data(env=None):
    try:
        result = docker(["desktop", "status", "--format", "json"], timeout=5, env=env)
        if result.returncode:
            if "could not retrieve status. is docker desktop running?" in result.stderr.lower():
                return {"state": "unknown", "actions": ["start"],
                        "message": "Docker Desktop is not responding. Try Start, or open Docker to check its status."}
            raise ValueError(result.stderr.strip() or "Docker Desktop status is unavailable.")
        state = json.loads(result.stdout).get("Status", "unknown").lower()
        if state not in {"running", "stopped"}:
            return {"state": "unknown", "actions": [], "message": "Docker Desktop is changing state or paused. Open Docker to check its status."}
        return {"state": state, "actions": ["start"] if state == "stopped" else ["stop", "restart"], "message": ""}
    except (OSError, ValueError, AttributeError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"state": "unknown", "actions": [], "message": "Desktop controls unavailable. Open Docker, or update Docker Desktop to enable its CLI. " + str(exc)[:300]}


def docker_data():
    desktop = desktop_data()
    try:
        result = docker(["ps", "-a", "--no-trunc", "--format", "{{json .}}"])
        if result.returncode:
            return {"status": "unavailable", "message": docker_message(result.stderr), "containers": [], "desktop": desktop}
        containers = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        stats = docker(["stats", "--no-stream", "--format", "{{json .}}"])
        metrics = {}
        if stats.returncode == 0:
            metrics = {row.get("Name"): {"cpu": docker_cpu(row.get("CPUPerc")), "memory": row.get("MemUsage")}
                       for row in (json.loads(line) for line in stats.stdout.splitlines() if line.strip())}
        return {"status": "available", "message": "", "desktop": desktop, "containers": [
            {"id": c.get("ID", ""), "name": c.get("Names", ""), "image": c.get("Image", ""), "state": c.get("State", ""),
             "actions": container_actions(c.get("State")),
             "status_text": c.get("Status", ""), "cpu": metrics.get(c.get("Names"), {}).get("cpu"),
             "memory": metrics.get(c.get("Names"), {}).get("memory")}
            for c in containers]}
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "message": str(exc), "containers": [], "desktop": desktop}


def docker_message(message):
    text = message.strip()[:500]
    if any(part in text.lower() for part in ("paused", "not running", "cannot connect", "connection refused")):
        return "Local Docker is unavailable or globally paused. Start Docker Desktop below, or open Docker to resume it."
    return text or "Docker Desktop is unavailable."


def validate_docker_request(body):
    scope, action = body.get("scope"), body.get("action")
    if scope == "desktop" and set(body) == {"scope", "action"} and action in ("start", "stop", "restart"):
        return scope, action, None
    if (scope == "container" and set(body) == {"scope", "action", "id"}
            and action in ("start", "stop", "restart", "pause", "unpause")
            and isinstance(body["id"], str) and re.fullmatch(r"[a-f0-9]{64}", body["id"])):
        return scope, action, body["id"]
    raise ValueError("Choose a valid Docker Desktop or container action")


def perform_docker_action(job_id, scope, action, container_id, env):
    try:
        # Check every registered workspace, including those hidden by directory filters.
        states = lifecycle.load_all_states()
        if states:
            module, checkout = lifecycle.get_lifecycle(), lifecycle.configured_checkout()
            if any(workspace_operations.operation_for(module, checkout, state["name"])["busy"] for state in states):
                raise ValueError("Wait for the active workspace operation before changing Docker")
        if scope == "desktop":
            if action not in desktop_data(env)["actions"]:
                raise ValueError("Docker Desktop is no longer available for that action. Refresh or open Docker.")
            args, label, timeout = ["desktop", action, "--timeout", "60"], "Docker Desktop", 70
        else:
            template = '{"id":{{json .Id}},"name":{{json .Name}},"state":{{json .State.Status}}}'
            inspection = docker(["container", "inspect", "--format", template, container_id], env=env)
            if inspection.returncode:
                raise ValueError("Container is no longer available. Refresh the container list.")
            container = json.loads(inspection.stdout)
            if container.get("id") != container_id:
                raise ValueError("Container identity changed. Refresh the container list.")
            label = container["name"].lstrip("/")
            if label in {state.get("centrifugo_container") for state in states}:
                raise ValueError("Use the workspace controls for this managed container")
            if action not in container_actions(container.get("state")):
                raise ValueError("Container state changed. Refresh and choose an available action.")
            args = ["container", action]
            if action in {"stop", "restart"}:
                args += ["--timeout", "10"]
            args += [container_id]
            timeout = 25
        result = docker(args, timeout=timeout, env=env)
        if result.returncode:
            raise ValueError(result.stderr.strip()[:500] or "Docker could not complete the action.")
        completed = {"start": "started", "stop": "stopped", "restart": "restarted", "pause": "paused", "unpause": "resumed"}[action]
        outcome = {"state": "done", "message": f"{label} {completed}."}
    except subprocess.TimeoutExpired:
        outcome = {"state": "error", "message": "Docker timed out. It may still be changing state; refresh before retrying."}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
        outcome = {"state": "error", "message": str(exc)[:500]}
    with JOBS_LOCK:
        JOBS[job_id] = outcome


def validate_local_headers(headers, port, post=False):
    host = f"127.0.0.1:{port}"
    if headers.get("Host") != host or (post and headers.get("Origin") != f"http://{host}"):
        raise ValueError("Local origin required")
    if post and not secrets.compare_digest(headers.get("X-CSRF-Token", ""), TOKEN):
        raise ValueError("Invalid CSRF token")


def validate_action(action, name, states):
    if action not in {"start", "stop", "reset", "destroy"} or not isinstance(name, str):
        raise ValueError("Choose a valid workspace action")
    if action == "destroy":
        try:
            lifecycle.validate_project_name(name)
        except (lifecycle.ProjectError, TypeError) as exc:
            raise ValueError(str(exc)) from exc
    state = next((s for s in states if s.get("name") == name), None)
    if not state or (action == "start" and state.get("status") not in {"stopped", "provisioned"}) or (action == "stop" and state.get("status") != "running") or (action == "reset" and state.get("status") not in {"running", "stopped", "provisioned"}):
        raise ValueError("Workspace is not available for that action")
    return state


def validate_destroy_target(state):
    context = lifecycle.RepositoryContext.discover()
    name = lifecycle.validate_project_name(state["name"])
    lifecycle.validate_state_context(state, context)
    database = lifecycle.normalized_database_name(name)
    if (state.get("postgres_database") != database or state.get("clickhouse_database") != database
            or state.get("centrifugo_container") != f"sp-centrifugo-{name}"):
        raise ValueError("Refusing to delete workspace resources with unexpected names")
    redis_base = state.get("redis_base")
    if type(redis_base) is not int or not 0 < redis_base < lifecycle.REDIS_DATABASE_COUNT or redis_base % lifecycle.REDIS_SLOT_SIZE:
        raise ValueError("Refusing to delete an invalid workspace Redis range")
    if any(other.get("name") != name and other.get("redis_base") == redis_base
           for other in lifecycle.load_all_states()):
        raise ValueError("Refusing to delete a Redis range shared with another workspace")
    root = Path(state["project_root"]).absolute()
    workspace = context.workspace.absolute()
    if root != workspace / state["name"] or root.parent != workspace or root.is_symlink() or root.is_mount():
        raise ValueError("Refusing to delete an unsafe workspace folder")
    if root.exists():
        marker = root / lifecycle.PROJECT_MARKER
        if not marker.is_file() or marker.is_symlink() or json.loads(marker.read_text()) != {"name": state["name"], "version": lifecycle.STATE_VERSION}:
            raise ValueError("Refusing to delete an unmarked or invalid workspace folder")
    if state.get("postgres_mode", "docker") == "native":
        host = lifecycle.BaseEnvironment(context).postgres_host
        try:
            local = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local:
            raise ValueError("Refusing to delete a workspace using non-local native PostgreSQL")
    elif state.get("postgres_mode", "docker") != "docker":
        raise ValueError("Workspace has an unsupported PostgreSQL mode")
    return state


def ensure_docker_available():
    try:
        result = docker(["info"], timeout=4)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise ValueError(docker_message(str(exc))) from exc
    if result.returncode:
        raise ValueError(docker_message(result.stderr))


def native_usage(state):
    usage = lifecycle.process_resource_usage(state)
    roots = {pid: service for service, item in usage.items() for pid in item["pids"]}
    if not roots:
        return usage
    try:
        result = subprocess.run(["ps", "-axo", "pid=,ppid=,uid=,%cpu=,rss="], text=True,
                                capture_output=True, timeout=5, check=False)
        if result.returncode:
            return usage
        children = {}
        resources = {}
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) != 5:
                continue
            try:
                pid, ppid, uid = map(int, fields[:3])
                cpu, rss = float(fields[3]), int(fields[4])
            except ValueError:
                continue
            if uid != os.getuid():
                continue
            children.setdefault(ppid, []).append(pid)
            resources[pid] = (cpu, rss)
        seen = set(roots)
        stack = [(pid, service) for pid, service in roots.items()]
        while stack:
            parent, service = stack.pop()
            for pid in children.get(parent, ()):
                if pid in seen:
                    continue
                seen.add(pid)
                stack.append((pid, service))
                cpu, rss = resources[pid]
                usage[service]["cpu_percent"] += cpu
                usage[service]["rss_kb"] += rss
                usage[service]["pids"].append(pid)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return usage


class LocalHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = socket.create_connection(("127.0.0.1", self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def probe_health(port, path="/", hostname=None, https=False):
    try:
        if type(port) is not int or not 1 <= port <= 65535:
            return False
        if https:
            if not hostname or not hostname.replace(".", "").replace("-", "").isalnum():
                return False
            context = ssl.create_default_context()
            for mkcert_root in (Path.home() / "Library/Application Support/mkcert/rootCA.pem",
                                Path.home() / ".local/share/mkcert/rootCA.pem"):
                if mkcert_root.is_file():
                    context.load_verify_locations(cafile=str(mkcert_root))
                    break
            connection = LocalHTTPSConnection(hostname, 443, timeout=0.7, context=context)
        else:
            if hostname:
                return False
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.7)
        connection.request("HEAD", path)  # GET requests would keep idle workspaces alive.
        response = connection.getresponse()
        response.read(1)
        return 200 <= response.status < 400
    except (OSError, http.client.HTTPException, ssl.SSLError, ValueError):
        return False
    finally:
        if "connection" in locals():
            try:
                connection.close()
            except OSError:
                pass


def workspace_health(state, checks):
    if state.get("status") == "stopped":
        return {"status": "stopped", "detail": "Workspace is not running."}
    if state.get("status") != "running":
        return {"status": "unknown", "detail": "Workspace setup is incomplete."}
    if len(checks) != 3:
        return {"status": "unknown", "detail": "Health probes are unavailable."}
    labels = ("backend", "frontend", "local HTTPS route")
    results = [future.result() for future in checks]
    passed = [label for label, healthy in zip(labels, results) if healthy]
    failed = [label for label, healthy in zip(labels, results) if not healthy]
    if not failed:
        return {"status": "healthy", "detail": "Backend, frontend, and local HTTPS route are responding."}
    if passed:
        return {"status": "partial", "detail": f"{', '.join(passed)} responding; {', '.join(failed)} unavailable."}
    return {"status": "unknown", "detail": "No local health probes responded."}


def visible_states(registered=None):
    roots = [Path(directory).expanduser().resolve() for directory in get_settings()["directories"]]
    states = []
    for state in (lifecycle.load_all_states() if registered is None else registered):
        if not state.get("project_root"):
            continue
        project = Path(state["project_root"]).resolve()
        if any(project.is_relative_to(root) for root in roots):
            states.append(state)
    return states


def git_projects():
    directories = tuple(get_settings()["directories"])
    with DISCOVERY_LOCK:
        if DISCOVERY_CACHE.get("directories") != directories or time.monotonic() - DISCOVERY_CACHE.get("updated", 0) >= 15:
            result = discover_projects(directories)
            DISCOVERY_CACHE.update(directories=directories, updated=time.monotonic(), result=result)
        return DISCOVERY_CACHE["result"]


def unmanaged_projects(registered=None):
    states = lifecycle.load_all_states() if registered is None else registered
    roots = [Path(state["project_root"]).resolve() for state in states if state.get("project_root")]
    return [project for project in git_projects()["projects"]
            if not any(Path(project["path"]).is_relative_to(root) for root in roots)]


def require_project(path):
    if not isinstance(path, str) or len(path) > 4096 or not Path(path).is_absolute():
        raise ValueError("Choose a discovered Git project")
    canonical = str(Path(path).resolve(strict=True))
    project = next((p for p in unmanaged_projects() if p["path"] == canonical), None)
    if project is None:
        raise ValueError("Project is outside discovery or belongs to a managed workspace")
    return project


def perform_project_action(job_id, action, path):
    try:
        # Recheck the directory boundary after queuing, before launching anything.
        project = require_project(path)
        if action == "start":
            start_project(project["path"], get_project(project["path"])["command"])
        else:
            stop_project(project["path"])
        result = {"state": "done", "message": "Project started." if action == "start" else "Project stopped."}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        result = {"state": "error", "message": str(exc)[:500]}
    with JOBS_LOCK:
        JOBS[job_id] = result


def snapshot():
    warnings = []
    registry_available = True
    try:
        registered = lifecycle.load_all_states()
        states = visible_states(registered)
    except (OSError, ValueError, RuntimeError) as exc:
        states, registered = [], []
        registry_available = False
        warnings.append(f"Workspace registry is unavailable: {exc}")
    health_checks = {
        state.get("name"): [
            HEALTH_POOL.submit(probe_health, state.get("backend_port")),
            HEALTH_POOL.submit(probe_health, state.get("frontend_port")),
            HEALTH_POOL.submit(probe_health, 443, "/__showpass_workspace_health", state.get("hostname"), True),
        ]
        for state in states if state.get("status") == "running"
    }
    docker_status = docker_data()
    containers = docker_status["containers"]
    by_name = {c["name"]: c for c in containers}
    centrifugo_names = {s.get("centrifugo_container") for s in registered}
    rows = []
    for state in states:
        usage = native_usage(state)
        services = {}
        for name, resource in usage.items():
            services[name] = {"cpu_percent": cpu_percent(resource["cpu_percent"]),
                              "memory_mb": round(resource["rss_kb"] / 1024, 1),
                              "pids": len(resource["pids"])}
        centrifugo = by_name.get(state.get("centrifugo_container"), {})
        safe = {key: state.get(key) for key in ("name", "status", "hostname", "backend_port", "frontend_port", "centrifugo_port", "postgres_mode")}
        safe.update(id="managed:" + state["name"], managed=True, path=state.get("project_root"))
        safe["url"] = "https://" + str(safe.pop("hostname")) if state.get("hostname") else None
        safe["services"] = services
        safe["cpu_percent"] = cpu_percent(sum(resource["cpu_percent"] for resource in usage.values()))
        safe["memory_mb"] = round(sum(s["memory_mb"] for s in services.values()), 1)
        safe["centrifugo"] = {"state": centrifugo.get("state", "unknown"), "cpu": centrifugo.get("cpu"), "memory": centrifugo.get("memory")}
        safe["can_start"] = state.get("status") in {"stopped", "provisioned"}
        safe["can_stop"] = state.get("status") == "running"
        safe["can_reset"] = state.get("status") in {"running", "stopped", "provisioned"}
        try:
            validate_destroy_target(state)
            safe["can_destroy"] = True
        except (KeyError, OSError, TypeError, ValueError, lifecycle.ProjectError, json.JSONDecodeError):
            safe["can_destroy"] = False
        try:
            module = lifecycle.get_lifecycle()
            safe["can_reset"] = safe["can_reset"] and callable(getattr(module, "restart_command", None))
            safe["operation"] = workspace_operations.operation_for(module, lifecycle.configured_checkout(), state["name"])
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            safe["operation"] = {"busy": True, "status": "running", "stage": "Workspace operation status is unavailable."}
        if safe["operation"]["busy"]:
            safe.update(can_start=False, can_stop=False, can_reset=False, can_destroy=False)
        safe["health"] = workspace_health(state, health_checks.get(state.get("name"), []))
        rows.append(safe)
    try:
        discovered = git_projects()
        warnings.extend(discovered["warnings"])
        for project in unmanaged_projects(registered):
            try:
                profile = get_project(project["path"])
                runtime = inspect_project(project["path"])
                if runtime.get("runtime_error"):
                    warnings.append(f"{project['name']}: {runtime['runtime_error']}")
                running = runtime["running"]
                rows.append({**project, "managed": False, "status": "running" if running else "detected",
                             "url": profile["url"] or None, "command": profile["command"],
                             "cpu_percent": cpu_percent(runtime["cpu_percent"]) if running else None,
                             "memory_mb": round(runtime["memory_mb"], 1) if running else None,
                             "services": {}, "centrifugo": {}, "can_start": registry_available and bool(profile["command"]) and not running,
                             "can_stop": registry_available and running, "can_destroy": False,
                             "health": {"status": "unknown", "detail": "User-configured Git project."}})
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                warnings.append(f"Cannot inspect {project['path']}: {exc}")
    except (OSError, ValueError, RuntimeError) as exc:
        warnings.append(f"Git project discovery is unavailable: {exc}")
    shared = [c for c in containers if c["name"] not in centrifugo_names]
    with ACTIVE_LOCK:
        operation = dict(DOCKER_OPERATION) if "docker" in ACTIVE_NAMES else {"busy": False}
    return {"csrf": TOKEN, "workspaces": rows, "discovery_warnings": warnings,
            "docker": {**docker_status, "containers": shared, "operation": operation}}


def worker_command(job_id, action, name, checkout):
    prefix = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-B", str(Path(__file__))]
    return [*prefix, "--workspace-action", action, name, "--operation-id", job_id, "--checkout", str(checkout)]


def perform_action(job_id, action, name):
    try:
        checkout = lifecycle.configured_checkout()
        if checkout is None:
            raise ValueError("Configure the Showpass checkout in Settings first")
        command = worker_command(job_id, action, name, checkout)
        log = workspace_operations.log_path(job_id)
        with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "w") as output:
            proc = subprocess.run(command, cwd=checkout, env=docker_env(), stdout=output,
                                  stderr=subprocess.STDOUT, start_new_session=True, check=False)
        result = {"state": "done" if proc.returncode == 0 else "error",
                  "message": "Workspace operation completed." if proc.returncode == 0 else
                  f"Workspace operation failed. Details: {log}"}
    except (OSError, ValueError, RuntimeError) as exc:
        result = {"state": "error", "message": str(exc)}
    with JOBS_LOCK:
        JOBS[job_id] = result


def workspace_worker(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-action", choices=("start", "stop", "reset", "destroy"), required=True)
    parser.add_argument("name")
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--checkout", required=True)
    options = parser.parse_args(arguments)
    checkout = lifecycle.configured_checkout()
    if checkout is None or checkout != Path(options.checkout).resolve():
        raise ValueError("Showpass configuration changed; operation was not started")
    state = validate_action(options.workspace_action, options.name, lifecycle.load_all_states())
    if options.workspace_action == "destroy":
        validate_destroy_target(state)
    module = lifecycle.get_lifecycle()
    workspace_operations.run_tracked(module, checkout, options.workspace_action, options.name, options.operation_id)



class Handler(BaseHTTPRequestHandler):
    def respond(self, code, data, content_type="application/json; charset=utf-8"):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            validate_local_headers(self.headers, self.server.server_port)
        except ValueError:
            return self.respond(403, {"error": "Local host required"})
        path = urlparse(self.path).path
        if path == "/api":
            try:
                self.respond(200, snapshot())
            except Exception as exc:
                self.respond(500, {"error": str(exc)[:500]})
        elif path == "/api/activity":
            with ACTIVE_LOCK:
                busy = bool(ACTIVE_NAMES)
            self.respond(200, {"busy": busy})
        elif path == "/api/herdr":
            self.respond(200, {**herdr_status.get_status(), "csrf": TOKEN})
        elif path == "/api/phone":
            self.respond(200, {**phone_deck.ACCESS.info(), "csrf": TOKEN})
        elif path in {"/deck", "/deck/"}:
            self.respond(200, phone_deck.page(), "text/html; charset=utf-8")
        elif path in phone_deck.ASSETS:
            body, content_type = phone_deck.asset(path)
            self.respond(200, body, content_type)
        elif path == "/deck/api/status":
            try:
                self.respond(200, {**phone_deck.status(), "csrf": TOKEN})
            except (OSError, ValueError):
                self.respond(503, {"error": "Chat status is unavailable."})
        elif path == "/api/settings":
            try:
                self.respond(200, {**get_settings(), "csrf": TOKEN})
            except (OSError, ValueError) as exc:
                self.respond(400, {"error": str(exc)})
        elif path == "/api/project":
            try:
                values = parse_qs(urlparse(self.path).query).get("path", [])
                if len(values) != 1:
                    raise ValueError("Choose one discovered project")
                project = require_project(values[0])
                self.respond(200, {"path": project["path"], **get_project(project["path"]), "csrf": TOKEN})
            except (OSError, ValueError, RuntimeError) as exc:
                self.respond(400, {"error": str(exc)})
        elif path.startswith("/api/jobs/"):
            with JOBS_LOCK:
                result = JOBS.get(path.rsplit("/", 1)[-1])
            if not result or result.get("state") == "running":
                result = workspace_operations.read_job(path.rsplit("/", 1)[-1]) or result
            self.respond(200 if result else 404, result or {"error": "Unknown job"})
        elif path == "/" or path == "/index.html":
            # Render the saved theme before first paint, including in a browser.
            try:
                appearance = get_settings().get("appearance", "system")
            except (OSError, ValueError):
                appearance = "system"
            html = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
            html = html.replace('<html lang="en">', f'<html lang="en" data-appearance="{appearance}">', 1)
            self.respond(200, html.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self.respond(404, {"error": "Not found"})

    def do_POST(self):
        if self.path not in {"/api/action", "/api/bulk-destroy", "/api/settings", "/api/project", "/api/docker/action", "/api/herdr/focus", "/api/herdr/pin", "/api/phone", "/deck/api/focus", "/deck/api/pin"}:
            return self.respond(404, {"error": "Not found"})
        try:
            validate_local_headers(self.headers, self.server.server_port, post=True)
        except ValueError as exc:
            return self.respond(403, {"error": str(exc)})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > (140000 if self.path == "/api/settings" else 16384):
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Request must be an object")
            if self.path == "/api/phone":
                if set(body) != {"action"} or body["action"] not in ("enable", "disable", "pair"):
                    raise ValueError("Choose a phone access action")
                action = {"enable": phone_deck.ACCESS.enable, "disable": phone_deck.ACCESS.disable, "pair": phone_deck.ACCESS.new_pairing}[body["action"]]
                return self.respond(200, {**action(), "csrf": TOKEN})
            if self.path in {"/api/herdr/pin", "/deck/api/pin"}:
                if set(body) != {"id", "pinned"}:
                    raise ValueError("Provide a chat and bookmark state")
                return self.respond(200, herdr_status.set_pin(body["id"], body["pinned"]))
            if self.path == "/deck/api/focus":
                if set(body) != {"id"}:
                    raise ValueError("Choose one chat")
                phone_deck.focus(body["id"])
                return self.respond(200, {"message": "Chat selected on your Mac."})
            if self.path == "/api/herdr/focus":
                if set(body) != {"id"}:
                    raise ValueError("Choose one waiting Herdr chat")
                herdr_status.focus_chat(body["id"])
                return self.respond(200, {"message": "Chat selected in Herdr."})
            if self.path == "/api/docker/action":
                return self.queue_docker_action(*validate_docker_request(body))
            if self.path == "/api/settings":
                if set(body) == {"appearance"}:
                    return self.respond(200, {**save_appearance(body["appearance"]), "csrf": TOKEN})
                if "directories" not in body or set(body) - {"directories", "showpass_checkout", "docker_socket"}:
                    raise ValueError("Provide directories and optional integration settings")
                settings = save_settings(body["directories"], body.get("showpass_checkout"), body.get("docker_socket"))
                return self.respond(200, {**settings, "csrf": TOKEN})
            if self.path == "/api/project":
                if set(body) != {"path", "url", "command"}:
                    raise ValueError("Provide a project path, URL, and command")
                project = require_project(body["path"])
                return self.respond(200, {"path": project["path"], **save_project(project["path"], body["url"], body["command"]), "csrf": TOKEN})
            if self.path == "/api/bulk-destroy":
                if set(body) != {"names", "confirmed"} or body["confirmed"] is not True:
                    raise ValueError("Confirm deletion to continue")
                return self.queue_bulk_destroy(body["names"])
            action, name = body.get("action"), body.get("name")
            project_path = None
            if "project" in body:
                if set(body) != {"action", "project"} or action not in {"start", "stop"}:
                    raise ValueError("Git projects support start and stop only")
                project_path = require_project(body["project"])["path"]
                if action == "start" and not get_project(project_path)["command"]:
                    raise ValueError("Configure a run command first")
                return self.queue_action(action, project_path, project=True)
            state = validate_action(action, name, visible_states())
            operation = workspace_operations.operation_for(lifecycle.get_lifecycle(), lifecycle.configured_checkout(), name)
            if operation["busy"]:
                return self.respond(409, {"error": operation["stage"] or "Workspace operation is already in progress"})
            if action == "destroy":
                if body.get("confirmed") is not True:
                    raise ValueError("Confirm deletion to continue")
                validate_destroy_target(state)
                ensure_docker_available()
            if action in {"start", "reset"}:
                ensure_docker_available()
        except (ValueError, TypeError, json.JSONDecodeError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return self.respond(400, {"error": str(exc)})
        self.queue_action(action, name)

    def queue_docker_action(self, scope, action, container_id):
        # Pin the selected local socket now; a settings change cannot retarget a queued action.
        env = docker_env()
        job_id = uuid.uuid4().hex
        with ACTIVE_LOCK:
            if "docker" in ACTIVE_NAMES or any(key.startswith("managed:") for key in ACTIVE_NAMES):
                return self.respond(409, {"error": "Wait for the active Docker or workspace operation to finish"})
            ACTIVE_NAMES.add("docker")
            DOCKER_OPERATION.clear()
            DOCKER_OPERATION.update(busy=True, job=job_id, stage=f"Docker {action} in progress…")
        with JOBS_LOCK:
            JOBS[job_id] = {"state": "running", "message": DOCKER_OPERATION["stage"]}
        def run_action():
            try:
                perform_docker_action(job_id, scope, action, container_id, env)
            finally:
                with ACTIVE_LOCK:
                    ACTIVE_NAMES.discard("docker")
        try:
            POOL.submit(run_action)
        except RuntimeError:
            with ACTIVE_LOCK:
                ACTIVE_NAMES.discard("docker")
            with JOBS_LOCK:
                JOBS[job_id] = {"state": "error", "message": "Could not queue the Docker action. Try again."}
            return self.respond(503, {"error": "Could not queue the Docker action. Try again."})
        return self.respond(202, {"job": job_id})

    def queue_action(self, action, target, project=False):
        key = ("project:" if project else "managed:") + target
        job_id = uuid.uuid4().hex
        with ACTIVE_LOCK:
            if not project and "docker" in ACTIVE_NAMES:
                return self.respond(409, {"error": "Wait for the Docker operation to finish"})
            if key in ACTIVE_NAMES:
                return self.respond(409, {"error": "An operation is already running for this project"})
            ACTIVE_NAMES.add(key)
        with JOBS_LOCK:
            JOBS[job_id] = {"state": "running", "message": "Project operation started."}
        def run_action():
            try:
                if project:
                    perform_project_action(job_id, action, target)
                else:
                    perform_action(job_id, action, target)
            finally:
                with ACTIVE_LOCK:
                    ACTIVE_NAMES.discard(key)
        POOL.submit(run_action)
        self.respond(202, {"job": job_id})

    def queue_bulk_destroy(self, names):
        if not isinstance(names, list) or not names:
            raise ValueError("Select at least one workspace")
        if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
            raise ValueError("Choose each workspace once")
        states = visible_states()
        targets = [validate_action("destroy", name, states) for name in names]
        module = lifecycle.get_lifecycle()
        checkout = lifecycle.configured_checkout()
        for state in targets:
            operation = workspace_operations.operation_for(module, checkout, state["name"])
            if operation["busy"]:
                return self.respond(409, {"error": operation["stage"] or "Workspace operation is already in progress"})
            validate_destroy_target(state)
        ensure_docker_available()

        jobs = [{"name": state["name"], "job": uuid.uuid4().hex,
                 "key": "managed:" + state["name"]} for state in targets]
        with ACTIVE_LOCK:
            if "docker" in ACTIVE_NAMES:
                return self.respond(409, {"error": "Wait for the Docker operation to finish"})
            if any(job["key"] in ACTIVE_NAMES for job in jobs):
                return self.respond(409, {"error": "A workspace operation is already running"})
            ACTIVE_NAMES.update(job["key"] for job in jobs)
        with JOBS_LOCK:
            for job in jobs:
                JOBS[job["job"]] = {"state": "running", "message": "Workspace deletion queued."}

        for index, job in enumerate(jobs):
            def run_action(job_id=job["job"], name=job["name"], key=job["key"]):
                try:
                    perform_action(job_id, "destroy", name)
                finally:
                    with ACTIVE_LOCK:
                        ACTIVE_NAMES.discard(key)
            try:
                POOL.submit(run_action)
            except RuntimeError:
                with JOBS_LOCK:
                    for queued in jobs[index:]:
                        JOBS[queued["job"]] = {"state": "error", "message": "Could not queue this workspace deletion. Try again."}
                with ACTIVE_LOCK:
                    for queued in jobs[index:]:
                        ACTIVE_NAMES.discard(queued["key"])
                break
        return self.respond(202, {"jobs": [{"name": job["name"], "job": job["job"]} for job in jobs]})

    def log_message(self, fmt, *args):
        print("[dashboard] " + fmt % args, flush=True)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("DASHBOARD_PORT", "8765"))), Handler)
    print(f"Workspace dashboard: http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        from project_runtime import validate_command
        import importlib
        for module in ("dotenv", "xml.sax.saxutils", "xml.etree.ElementTree", "platform", "copy", "fcntl", "plistlib"):
            importlib.import_module(module)
        assert validate_command("git status") == ["git", "status"]
        assert Path(__file__).with_name("index.html").is_file()
        assert Path(__file__).with_name("deck.html").is_file()
        for path in phone_deck.ASSETS:
            assert phone_deck.asset(path)[0]
        print("Bundled backend checks passed")
    elif "--workspace-action" in sys.argv:
        try:
            workspace_worker(sys.argv[1:])
        except Exception as exc:
            print(f"Workspace operation failed: {exc}", file=sys.stderr, flush=True)
            sys.exit(1)
    else:
        main()
