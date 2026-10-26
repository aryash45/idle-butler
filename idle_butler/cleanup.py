"""Cleanup plan, explicit moves, automatic moves, and undo."""

import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .jobs import PARTIAL_EXTENSIONS
from .state import read_json, write_json

PLAN_VERSION = 1
LOG_VERSION = 1
RECENT_FILE_MINUTES = 10
DEFAULT_MAX_MOVES = 50


@dataclass
class PlanEntry:
    """One cleanup suggestion."""

    original: str
    folder: str
    new_name: str
    reason: str
    confidence: str = "low"


def safe_inside(path: Path, root: Path) -> bool:
    """Return whether a resolved path is inside root."""
    return path.resolve().is_relative_to(root.resolve())


def collision_name(root: Path, entry: PlanEntry, reserved: Set[Tuple[str, str]]) -> str:
    """Return a non-colliding destination name."""
    stem, suffix = Path(entry.new_name).stem, Path(entry.new_name).suffix
    candidate = entry.new_name
    number = 2
    while (root / entry.folder / candidate).exists() or (entry.folder, candidate) in reserved:
        candidate = f"{stem}-{number}{suffix}"
        number += 1
    reserved.add((entry.folder, candidate))
    return candidate


def plan_path(messy: Path) -> Path:
    """Return the cleanup plan path."""
    return messy / "cleanup-plan.json"


def log_path(messy: Path) -> Path:
    """Return the cleanup log path."""
    return messy / "cleanup-log.json"


def save_plan(messy: Path, entries: Sequence[PlanEntry]) -> None:
    """Write a versioned cleanup plan."""
    write_json(plan_path(messy), {"version": PLAN_VERSION, "entries": [asdict(entry) for entry in entries]})


def load_plan(messy: Path) -> List[PlanEntry]:
    """Read a cleanup plan defensively."""
    value = read_json(plan_path(messy), {"version": PLAN_VERSION, "entries": []})
    return [PlanEntry(item["original"], item["folder"], item["new_name"], item.get("reason", ""), item.get("confidence", "low")) for item in value.get("entries", []) if value.get("version") == PLAN_VERSION and isinstance(item, dict) and all(isinstance(item.get(key), str) for key in ("original", "folder", "new_name"))]


def write_move_log(messy: Path, move: Dict[str, str]) -> None:
    """Append one move to the versioned log atomically."""
    value = read_json(log_path(messy), {"version": LOG_VERSION, "moves": []})
    moves = value.get("moves", []) if value.get("version") == LOG_VERSION else []
    moves.append(move)
    write_json(log_path(messy), {"version": LOG_VERSION, "moves": moves})


def move_one(messy: Path, entry: PlanEntry) -> Optional[Dict[str, str]]:
    """Move one plan entry if both paths are safe and available."""
    source = (messy / entry.original).resolve()
    target = (messy / entry.folder / entry.new_name).resolve()
    if not safe_inside(source, messy) or not safe_inside(target, messy) or not source.is_file() or target.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    move = {"original": str(source), "new": str(target), "time": datetime.now().astimezone().isoformat()}
    write_move_log(messy, move)
    source.replace(target)
    return move


def apply_plan(messy: Path, confirm: bool = True) -> int:
    """Apply a reviewed cleanup plan after confirmation."""
    entries = load_plan(messy)
    for entry in entries:
        print(f"{entry.original} -> {entry.folder}/{entry.new_name}")
    if not entries:
        print("No cleanup plan found.")
        return 0
    if confirm and input(f"Apply {len(entries)} moves? [y/N] ").strip().lower() != "y":
        print("No files moved.")
        return 0
    moved = sum(move_one(messy, entry) is not None for entry in entries)
    print(f"Moved {moved} of {len(entries)} planned files.")
    return 0


def undo(messy: Path) -> int:
    """Restore logged moves newest-first."""
    value = read_json(log_path(messy), {"version": LOG_VERSION, "moves": []})
    moves = value.get("moves", []) if value.get("version") == LOG_VERSION else []
    restored = 0
    for move in reversed(moves):
        source, target = Path(move.get("new", "")).resolve(), Path(move.get("original", "")).resolve()
        if not safe_inside(source, messy) or not safe_inside(target, messy) or not source.is_file() or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        restored += 1
    write_json(log_path(messy), {"version": LOG_VERSION, "moves": []})
    print(f"Restored {restored} files.")
    return 0


def duplicate_names(messy: Path) -> Set[str]:
    """Find duplicate visible files by content hash."""
    hashes: Dict[str, str] = {}
    duplicates: Set[str] = set()
    for path in messy.iterdir():
        if not path.is_file() or path.name.startswith(".") or path.name in {"cleanup-plan.json", "cleanup-log.json"}:
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest in hashes:
            duplicates.add(path.name)
            duplicates.add(hashes[digest])
        else:
            hashes[digest] = path.name
    return duplicates


def auto_move(messy: Path, entries: Sequence[PlanEntry], max_moves: int, idle_check: Callable[[], bool]) -> List[Dict[str, str]]:
    """Move only eligible high-confidence entries during idle work."""
    moves: List[Dict[str, str]] = []
    cutoff = datetime.now().timestamp() - RECENT_FILE_MINUTES * 60
    duplicates = duplicate_names(messy)
    for entry in entries:
        if len(moves) >= max_moves or entry.confidence != "high" or not idle_check():
            continue
        source = (messy / entry.original).resolve()
        try:
            modified = source.stat()
        except OSError:
            continue
        if source.suffix.lower() in PARTIAL_EXTENSIONS or modified.st_mtime > cutoff:
            continue
        if source.stat().st_size == 0 or entry.original in duplicates:
            entry.folder = "_review"
            entry.new_name = source.name
        move = move_one(messy, entry)
        if move:
            moves.append(move)
    return moves

