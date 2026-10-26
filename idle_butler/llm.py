"""Small standard-library Ollama HTTP client."""

import json
import logging
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .state import ButlerError

OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 120
HEALTH_TIMEOUT_SECONDS = 10
RETRY_COUNT = 2
RECOMMENDED_MODELS = ("llama3.2:3b", "llama3.2:1b")
LOGGER = logging.getLogger("idle_butler")


@dataclass
class HealthResult:
    """One startup health check."""

    name: str
    ok: bool
    detail: str
    fix: str


class OllamaClient:
    """Call the local Ollama HTTP API."""

    def __init__(self, base_url: str = OLLAMA_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def _request(self, path: str, data: Optional[Dict[str, Any]] = None, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> bytes:
        """Make one JSON HTTP request."""
        body = json.dumps(data).encode("utf-8") if data is not None else None
        request = urllib.request.Request(f"{self.base_url}{path}", data=body, headers={"Content-Type": "application/json"} if body else {})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def list_models(self) -> List[str]:
        """Return locally available model names."""
        try:
            value = json.loads(self._request("/api/tags", timeout=HEALTH_TIMEOUT_SECONDS).decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ButlerError("Ollama is not reachable; install it from ollama.com and start it.") from exc
        return [item.get("name", "") for item in value.get("models", []) if isinstance(item, dict) and item.get("name")]

    def generate(self, model: str, prompt: str, json_mode: bool = False) -> str:
        """Generate one local response, retrying once."""
        payload: Dict[str, Any] = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}}
        if json_mode:
            payload["format"] = "json"
        last_error: Optional[BaseException] = None
        for attempt in range(RETRY_COUNT):
            try:
                value = json.loads(self._request("/api/generate", payload).decode("utf-8"))
                answer = value.get("response", "").strip()
                if not answer:
                    raise ButlerError("Ollama returned an empty response.")
                return answer
            except (OSError, urllib.error.URLError, json.JSONDecodeError, ButlerError) as exc:
                last_error = exc
                LOGGER.debug("Ollama generation attempt %d failed", attempt + 1)
        raise ButlerError(f"Ollama failed after {RETRY_COUNT} attempts: {last_error}") from last_error

    def pull_model(
        self,
        name: str,
        on_progress: Callable[[str, int, int], None],
        cancel: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Pull a model and report streamed progress."""
        for field in ("model", "name"):
            body = json.dumps({field: name, "stream": True}).encode("utf-8")
            request = urllib.request.Request(f"{self.base_url}/api/pull", data=body, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                    for line in response:
                        if cancel is not None and cancel():
                            return
                        if not line.strip():
                            continue
                        value = json.loads(line.decode("utf-8"))
                        on_progress(str(value.get("status", "")), int(value.get("completed", 0)), int(value.get("total", 0)))
                return
            except urllib.error.HTTPError as exc:
                if field == "model":
                    continue
                raise ButlerError(f"Could not pull model '{name}': {exc}") from exc
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                raise ButlerError(f"Could not pull model '{name}': {exc}") from exc

    def health(self, model: str, idle_ok: bool, path: str) -> List[HealthResult]:
        """Return structured local health checks."""
        try:
            models = self.list_models()
            ollama = HealthResult("Ollama", True, "reachable", "")
            model_result = HealthResult("Model", model in models, model if model in models else f"{model} is not downloaded", f"Run ollama pull {model}" if model not in models else "")
        except ButlerError as exc:
            ollama = HealthResult("Ollama", False, str(exc), "Install Ollama from ollama.com and start it.")
            model_result = HealthResult("Model", False, "not checked", f"Start Ollama, then run ollama pull {model}")
        usage = shutil.disk_usage(path)
        return [ollama, model_result, HealthResult("Idle detection", idle_ok, "available" if idle_ok else "unavailable", "Use --now or install xprintidle"), HealthResult("Disk space", usage.free > 0, f"{usage.free} bytes free", "Free disk space if the model cannot be downloaded")]

