"""Shared records and atomic versioned JSON state."""

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

LOGGER = logging.getLogger("idle_butler")
STATE_VERSION = 1


class ButlerError(Exception):
    """An expected user-facing error."""


@dataclass
class FileResult:
    """A completed text-file job."""

    name: str
    answer: str
    modified: str
    truncated: bool = False
    action_items: str = "NONE"


@dataclass
class SessionStats:
    """Counts for one run."""

    summarized: int = 0
    skipped: int = 0
    failed: int = 0
    cleanup_suggestions: int = 0
    action_items: int = 0
    organized: int = 0
    stopped: bool = False


def atomic_write(path: Path, content: str) -> None:
    """Write content through a same-directory temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Optional[str] = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                LOGGER.debug("Could not fsync %s", path)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                LOGGER.debug("Could not remove temporary file %s", temporary)


def write_json(path: Path, value: Dict[str, Any]) -> None:
    """Write JSON atomically."""
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    """Read a JSON object or return a fresh default."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return default.copy()
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Starting fresh because %s is unreadable: %s", path, exc)
        return default.copy()
    return value if isinstance(value, dict) else default.copy()

