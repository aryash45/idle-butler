"""Phase 1 command-line orchestration."""

import argparse
import logging
import platform
import shutil
import subprocess
import sys
import time
from threading import Event
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .cleanup import DEFAULT_MAX_MOVES, PlanEntry, apply_plan, auto_move, collision_name, load_plan, save_plan, undo
from .digest import render_digest, save_digest
from .idle import get_idle_seconds
from .jobs import DEFAULT_FOLDERS, cleanup_candidates, cleanup_prompt, parse_cleanup, summarize_file, text_files
from .llm import OllamaClient
from .state import ButlerError, SessionStats, read_json, write_json

LOGGER = logging.getLogger("idle_butler")
RETURNED_IDLE_SECONDS = 10.0
POLL_SECONDS = 10.0
PLAN_STATE_VERSION = 1
WORKFLOW_ACTIONS = {"summarize", "actions", "cleanup"}


def sanitize_folders(raw: Optional[str]) -> Tuple[str, ...]:
    """Validate custom cleanup folder names."""
    if not raw:
        return DEFAULT_FOLDERS
    names = tuple(name.strip() for name in raw.split(",") if name.strip())
    if not names or any(len(name) > 30 or "/" in name or "\\" in name or ".." in name for name in names):
        raise ButlerError("--folders must be comma-separated names of at most 30 characters without separators.")
    return names


def validate_paths(folder_arg: Optional[str], messy_arg: Optional[str], output_arg: str) -> Tuple[Optional[Path], Optional[Path], Path]:
    """Resolve and validate CLI paths."""
    folder = Path(folder_arg).expanduser().resolve() if folder_arg else None
    messy = Path(messy_arg).expanduser().resolve() if messy_arg else None
    output = Path(output_arg).expanduser().resolve()
    if folder is None and messy is None:
        raise ButlerError("run requires --folder, --messy, or both")
    for label, path in (("folder", folder), ("messy folder", messy)):
        if path is not None and not path.is_dir():
            raise ButlerError(f"{label} does not exist: {path}")
    for watched in (folder, messy):
        if watched is not None and output.is_relative_to(watched):
            raise ButlerError("The output path must be outside watched folders.")
    if folder is not None and messy is not None and folder == messy:
        raise ButlerError("--folder and --messy must be different folders.")
    return folder, messy, output


def normalize_actions(raw: object) -> Tuple[str, ...]:
    """Validate the built-in workflow actions."""
    if isinstance(raw, str):
        values = tuple(item.strip() for item in raw.split(",") if item.strip())
    elif isinstance(raw, (list, tuple)):
        values = tuple(str(item).strip() for item in raw if str(item).strip())
    else:
        values = ("summarize", "actions", "cleanup")
    if not values or any(item not in WORKFLOW_ACTIONS for item in values):
        raise ButlerError("Workflow actions must be summarize, actions, or cleanup.")
    return tuple(dict.fromkeys(values))


def wait_for_idle(now: bool, minutes: float, poll_seconds: float, stop_event: Optional[Event] = None, status_callback: Optional[object] = None) -> str:
    """Wait for the configured idle threshold."""
    if now:
        return "manual --now"
    target = minutes * 60
    while True:
        if stop_event is not None and stop_event.is_set():
            raise KeyboardInterrupt
        idle = get_idle_seconds()
        if status_callback is not None:
            status_callback(f"waiting: {idle:.0f}s / {target:.0f}s")
        print(f"\rWaiting for idle time: {idle:.0f}s / {target:.0f}s", end="", flush=True)
        if idle >= target:
            print()
            return f"{minutes:g} min"
        time.sleep(poll_seconds)


def notify(message: str) -> None:
    """Attempt a supported desktop notification."""
    try:
        if platform.system() == "Darwin":
            subprocess.run(["osascript", "-e", f'display notification "{message}" with title "Idle Butler"'], timeout=5, check=False)
        elif platform.system() == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", "Idle Butler", message], timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        LOGGER.debug("Notification unavailable")


def run_command(args: argparse.Namespace, stop_event: Optional[Event] = None, status_callback: Optional[object] = None) -> int:
    """Run the Phase 1 engine."""
    folder, messy, output = validate_paths(args.folder, args.messy, args.out)
    folders = sanitize_folders(args.folders)
    actions = normalize_actions(getattr(args, "actions", ("summarize", "actions", "cleanup")))
    if ("summarize" in actions or "actions" in actions) and folder is None:
        raise ButlerError("This workflow requires a summary folder.")
    if "cleanup" in actions and messy is None:
        raise ButlerError("This workflow requires a messy folder.")
    client = OllamaClient()
    models = client.list_models()
    if args.model not in models:
        if not args.yes and input(f"Model '{args.model}' is missing. Download it? [y/N] ").strip().lower() != "y":
            raise ButlerError(f"Model '{args.model}' is not available. Run ollama pull {args.model}.")
        client.pull_model(args.model, lambda status, completed, total: print(f"\r{status} {completed}/{total}", end="", flush=True))
        print()
    trigger = wait_for_idle(args.now, args.idle_minutes, args.poll_seconds, stop_event, status_callback)
    stats = SessionStats()
    started = time.monotonic()
    results = []
    state_path = output.parent / ".idle_butler_state.json"
    state = read_json(state_path, {"version": 1, "files": {}})
    for path in text_files(folder) if folder and ("summarize" in actions or "actions" in actions) else []:
        if stop_event is not None and stop_event.is_set():
            stats.stopped = True
            break
        if status_callback is not None:
            status_callback(f"summarizing: {path.name}")
        if state.get("files", {}).get(path.name) == path.stat().st_mtime:
            stats.skipped += 1
            continue
        if get_idle_seconds() < RETURNED_IDLE_SECONDS:
            stats.stopped = True
            break
        try:
            results.append(summarize_file(path, client, args.model, stats, "summarize" in actions, "actions" in actions))
            state.setdefault("files", {})[path.name] = path.stat().st_mtime
            stats.summarized += 1
        except (OSError, ButlerError) as exc:
            LOGGER.warning("Could not process %s: %s", path.name, exc)
            stats.failed += 1
        write_json(state_path, {"version": 1, "files": state.get("files", {})})
    plans: List[PlanEntry] = []
    organized = []
    if messy is not None and "cleanup" in actions and not stats.stopped:
        plan_state_path = messy / ".idle_butler_plan_state.json"
        plan_state = read_json(plan_state_path, {"version": PLAN_STATE_VERSION, "files": {}})
        plans = load_plan(messy)
        existing = {item.original: item for item in plans}
        reserved = {(item.folder, item.new_name) for item in plans}
        for path in cleanup_candidates(messy, {"cleanup-plan.json", "cleanup-log.json", plan_state_path.name}):
            if stop_event is not None and stop_event.is_set():
                stats.stopped = True
                break
            if status_callback is not None:
                status_callback(f"planning: {path.name}")
            if plan_state.get("files", {}).get(path.name) == path.stat().st_mtime:
                continue
            if get_idle_seconds() < RETURNED_IDLE_SECONDS:
                stats.stopped = True
                break
            try:
                item = parse_cleanup(client.generate(args.model, cleanup_prompt(path, folders), json_mode=True), path.name, folders)
                old_entry = existing.get(path.name)
                if old_entry is not None:
                    reserved.discard((old_entry.folder, old_entry.new_name))
                entry = PlanEntry(path.name, item["folder"], item["new_name"], item["reason"], item["confidence"])
                entry.new_name = collision_name(messy, entry, reserved)
                existing[path.name] = entry
                plans = list(existing.values())
                plan_state.setdefault("files", {})[path.name] = path.stat().st_mtime
                stats.cleanup_suggestions += 1
            except (OSError, ButlerError) as exc:
                LOGGER.warning("Could not plan %s: %s", path.name, exc)
                stats.failed += 1
        write_json(plan_state_path, {"version": PLAN_STATE_VERSION, "files": plan_state.get("files", {})})
        save_plan(messy, plans)
        if args.auto and not stats.stopped:
            moves = auto_move(messy, plans, args.max_moves, lambda: get_idle_seconds() >= RETURNED_IDLE_SECONDS)
            organized.extend(moves)
            stats.organized = len(moves)
            moved_names = {Path(item["original"]).name for item in moves}
            plans = [item for item in plans if item.original not in moved_names]
            save_plan(messy, plans)
    old = output.read_text(encoding="utf-8") if output.exists() else ""
    save_digest(output, render_digest(results, plans, organized, args.model, trigger, stats, started, old, args.auto))
    message = f"Welcome back. {stats.summarized} summarized, {stats.cleanup_suggestions} cleanup suggestions, {stats.action_items} action items. See digest.md."
    print(message)
    notify(message)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(prog="idle-butler")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--folder")
    run.add_argument("--messy")
    run.add_argument("--folders")
    run.add_argument("--auto", action="store_true")
    run.add_argument("--max-moves", type=int, default=DEFAULT_MAX_MOVES)
    run.add_argument("--idle-minutes", type=float, default=5)
    run.add_argument("--model", default="llama3.2:3b")
    run.add_argument("--out", default="digest.md")
    run.add_argument("--now", action="store_true")
    run.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    run.add_argument("--yes", action="store_true")
    run.add_argument("--actions", default="summarize,actions,cleanup")
    run.add_argument("--verbose", action="store_true")
    apply = subparsers.add_parser("apply")
    apply.add_argument("--messy", required=True)
    undo_parser = subparsers.add_parser("undo")
    undo_parser.add_argument("--messy", required=True)
    ui = subparsers.add_parser("ui")
    ui.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse arguments and run one command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        if args.command == "run":
            if args.max_moves < 0 or args.idle_minutes < 0 or args.poll_seconds <= 0:
                parser.error("invalid numeric option")
            if args.auto and not args.messy:
                parser.error("--auto requires --messy")
            return run_command(args)
        if args.command == "ui":
            from .server import serve

            if not 1 <= args.port <= 65535:
                parser.error("--port must be between 1 and 65535")
            serve(args.port)
            return 0
        messy = Path(args.messy).expanduser().resolve()
        if not messy.is_dir():
            raise ButlerError(f"Messy folder does not exist: {messy}")
        return apply_plan(messy) if args.command == "apply" else undo(messy)
    except KeyboardInterrupt:
        print("Idle Butler stopped cleanly; progress already saved.")
        return 0
    except (ButlerError, OSError, ValueError) as exc:
        print(f"Idle Butler: {exc}", file=sys.stderr)
        return 1

