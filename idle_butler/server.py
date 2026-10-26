"""Token-protected localhost web server for Idle Butler."""

import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .cli import normalize_actions, sanitize_folders, validate_paths
from .idle import get_idle_seconds
from .llm import OllamaClient, RECOMMENDED_MODELS
from .state import ButlerError, read_json, write_json

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 64 * 1024
SETTINGS_VERSION = 1
UI_PATH = Path(__file__).with_name("ui") / "index.html"


class ServerState:
    """Mutable state shared by HTTP handlers and worker threads."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.lock = threading.Lock()
        self.running = False
        self.status = "stopped"
        self.error = ""
        self.worker: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.pull_thread: Optional[threading.Thread] = None
        self.pull_cancel = threading.Event()
        self.pull_status: Dict[str, Any] = {"running": False, "status": "", "completed": 0, "total": 0}

    def start(self, settings: Dict[str, Any]) -> bool:
        """Start one background engine worker."""
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.status = "starting"
            self.error = ""
            self.stop_event.clear()
            self.worker = threading.Thread(target=self._run, args=(settings,), daemon=True)
            self.worker.start()
            return True

    def _run(self, settings: Dict[str, Any]) -> None:
        """Run the shared CLI engine in the background."""
        try:
            from argparse import Namespace
            from .cli import run_command

            args = Namespace(
                folder=settings.get("folder"), messy=settings.get("messy"), folders=settings.get("folders"),
                auto=bool(settings.get("auto")), max_moves=int(settings.get("max_moves", 50)),
                idle_minutes=float(settings.get("idle_minutes", 5)), model=settings.get("model", RECOMMENDED_MODELS[0]),
                out=settings.get("out", "digest.md"), now=bool(settings.get("now", False)),
                poll_seconds=float(settings.get("poll_seconds", 10)), yes=True, verbose=False,
                actions=normalize_actions(settings.get("actions", ("summarize", "actions", "cleanup"))),
            )
            run_command(args, self.stop_event, self.set_status)
            with self.lock:
                self.status = "stopped"
        except KeyboardInterrupt:
            with self.lock:
                self.status = "stopped"
        except (ButlerError, OSError, ValueError) as exc:
            with self.lock:
                self.error = str(exc)
                self.status = "error"
        finally:
            with self.lock:
                self.running = False

    def set_status(self, status: str) -> None:
        """Update the worker status line."""
        with self.lock:
            self.status = status

    def stop(self) -> None:
        """Request a worker stop between items."""
        self.stop_event.set()

    def start_pull(self, model: str) -> bool:
        """Start one model pull worker."""
        with self.lock:
            if self.pull_thread is not None and self.pull_thread.is_alive():
                return False
            self.pull_cancel.clear()
            self.pull_status = {"running": True, "status": "starting", "completed": 0, "total": 0}
            self.pull_thread = threading.Thread(target=self._pull, args=(model,), daemon=True)
            self.pull_thread.start()
            return True

    def _pull(self, model: str) -> None:
        """Pull a model and update progress."""
        try:
            def progress(status: str, completed: int, total: int) -> None:
                with self.lock:
                    self.pull_status.update(status=status, completed=completed, total=total)

            OllamaClient().pull_model(model, progress, self.pull_cancel.is_set)
            with self.lock:
                self.pull_status.update(running=False, status="complete")
        except (ButlerError, OSError, ValueError) as exc:
            with self.lock:
                self.pull_status.update(running=False, status="error", error=str(exc))

    def snapshot(self) -> Dict[str, Any]:
        """Return a JSON-safe status snapshot."""
        with self.lock:
            return {"running": self.running, "status": self.status, "error": self.error, "pull": dict(self.pull_status)}


def json_response(handler: BaseHTTPRequestHandler, value: Dict[str, Any], status: int = 200) -> None:
    """Send a JSON response."""
    body = json.dumps(value).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def request_token(path: str) -> str:
    """Read the URL token."""
    return parse_qs(urlparse(path).query).get("token", [""])[0]


class Handler(BaseHTTPRequestHandler):
    """Serve the local UI and JSON API."""

    server: "IdleButlerServer"

    def log_message(self, format: str, *args: Any) -> None:
        """Keep HTTP logs out of normal user output."""

    def authorized(self) -> bool:
        """Validate token and Host header."""
        host = self.headers.get("Host", "").lower()
        allowed = {f"{HOST}:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        return host in allowed and request_token(self.path) == self.server.app_state.token

    def read_body(self) -> Dict[str, Any]:
        """Read a capped JSON POST body."""
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            raise ButlerError("request body too large")
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(value, dict):
            raise ButlerError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:
        """Handle authenticated GET requests."""
        if not self.authorized():
            json_response(self, {"error": "forbidden"}, 403)
            return
        route = urlparse(self.path).path
        if route == "/":
            body = UI_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if route == "/api/health":
            try:
                checks = OllamaClient().health(RECOMMENDED_MODELS[0], self.idle_available(), str(Path.cwd()))
                json_response(self, {"checks": [check.__dict__ for check in checks]})
            except (OSError, ButlerError) as exc:
                json_response(self, {"error": str(exc)}, 500)
            return
        if route == "/api/models":
            try:
                json_response(self, {"models": OllamaClient().list_models(), "recommended": list(RECOMMENDED_MODELS)})
            except ButlerError as exc:
                json_response(self, {"error": str(exc)}, 500)
            return
        if route == "/api/pull/status":
            json_response(self, self.server.app_state.snapshot()["pull"])
            return
        if route == "/api/status":
            json_response(self, self.server.app_state.snapshot())
            return
        if route == "/api/settings":
            json_response(self, read_json(Path.cwd() / "idle_butler-settings.json", {"version": SETTINGS_VERSION, "settings": {}}).get("settings", {}))
            return
        if route == "/api/digest":
            settings = read_json(Path.cwd() / "idle_butler-settings.json", {"settings": {}}).get("settings", {})
            path = Path(str(settings.get("out", "digest.md"))).expanduser().resolve()
            json_response(self, {"digest": path.read_text(encoding="utf-8") if path.exists() else ""})
            return
        json_response(self, {"error": "not found"}, 404)

    def idle_available(self) -> bool:
        """Check whether idle detection can run."""
        try:
            get_idle_seconds()
            return True
        except ButlerError:
            return False

    def do_POST(self) -> None:
        """Handle authenticated state-changing requests."""
        if not self.authorized():
            json_response(self, {"error": "forbidden"}, 403)
            return
        try:
            payload = self.read_body()
            route = urlparse(self.path).path
            if route == "/api/settings":
                validate_paths(payload.get("folder"), payload.get("messy"), payload.get("out", "digest.md"))
                sanitize_folders(payload.get("folders"))
                normalize_actions(payload.get("actions", ("summarize", "actions", "cleanup")))
                write_json(Path.cwd() / "idle_butler-settings.json", {"version": SETTINGS_VERSION, "settings": payload})
                json_response(self, {"ok": True})
            elif route == "/api/pull":
                model = str(payload.get("model", "")).strip()
                if not model or not self.server.app_state.start_pull(model):
                    json_response(self, {"error": "pull already running or model missing"}, 409)
                else:
                    json_response(self, {"ok": True})
            elif route == "/api/pull/cancel":
                self.server.app_state.pull_cancel.set()
                json_response(self, {"ok": True})
            elif route == "/api/start":
                settings = payload
                validate_paths(settings.get("folder"), settings.get("messy"), settings.get("out", "digest.md"))
                sanitize_folders(settings.get("folders"))
                actions = normalize_actions(settings.get("actions", ("summarize", "actions", "cleanup")))
                if any(item in actions for item in ("summarize", "actions")) and not settings.get("folder"):
                    raise ButlerError("This workflow requires a notes folder")
                if "cleanup" in actions and not settings.get("messy"):
                    raise ButlerError("This workflow requires a cleanup folder")
                json_response(self, {"ok": self.server.app_state.start(settings)}, 202)
            elif route == "/api/stop":
                self.server.app_state.stop()
                json_response(self, {"ok": True})
            elif route in {"/api/apply", "/api/undo"}:
                from .cleanup import apply_plan, undo

                messy = Path(str(payload.get("messy", ""))).expanduser().resolve()
                if not messy.is_dir():
                    raise ButlerError("messy folder does not exist")
                result = apply_plan(messy, confirm=False) if route.endswith("apply") else undo(messy)
                json_response(self, {"ok": result == 0})
            else:
                json_response(self, {"error": "not found"}, 404)
        except (ButlerError, OSError, ValueError, json.JSONDecodeError) as exc:
            json_response(self, {"error": str(exc)}, 400)


class IdleButlerServer(ThreadingHTTPServer):
    """Threading HTTP server with shared application state."""

    def __init__(self, port: int, token: str) -> None:
        self.app_state = ServerState(token)
        super().__init__((HOST, port), Handler)


def find_port(start: int) -> int:
    """Find an available localhost port."""
    for port in range(start, start + 100):
        try:
            server = IdleButlerServer(port, secrets.token_urlsafe(24))
            server.server_close()
            return port
        except OSError:
            continue
    raise ButlerError("No available localhost port found.")


def serve(port: int = DEFAULT_PORT) -> None:
    """Start the token-protected local UI server."""
    selected = find_port(port)
    token = secrets.token_urlsafe(24)
    server = IdleButlerServer(selected, token)
    url = f"http://{HOST}:{selected}/?token={token}"
    print(f"Idle Butler UI: {url}")
    try:
        webbrowser.open(url)
    except OSError:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nIdle Butler UI stopped.")
    finally:
        server.server_close()

