"""Pure job prompts and file-job orchestration."""

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set

from .llm import OllamaClient
from .state import ButlerError, FileResult, SessionStats

MAX_SUMMARY_CHARS = 6000
MAX_CLEANUP_CHARS = 300
MAX_REASON_WORDS = 10
TEXT_EXTENSIONS = (".txt", ".md")
PARTIAL_EXTENSIONS = (".crdownload", ".part", ".tmp", ".download", ".partial")
DEFAULT_FOLDERS = ("Documents", "Finance", "Study", "Work", "Images", "Installers", "Misc")


def text_files(folder: Path) -> List[Path]:
    """List eligible text files non-recursively."""
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in TEXT_EXTENSIONS), key=lambda p: p.name.lower())


def prompt_summary(text: str) -> str:
    """Build the summary prompt."""
    return ("Summarize the text in exactly 3 short bullet points. Use only information explicitly in the text. "
            "Do not guess or add outside facts. If the text is too short or unclear, reply exactly: INSUFFICIENT TEXT.\n\n" + text)


def prompt_actions(text: str) -> str:
    """Build the action-item prompt."""
    return ("List any tasks or deadlines explicitly written in the text, one per line starting with '- [ ]'. "
            "Use only information in the text. If there are none, reply exactly: NONE.\n\n" + text)


def summarize_file(
    path: Path,
    client: OllamaClient,
    model: str,
    stats: SessionStats,
    include_summary: bool = True,
    include_actions: bool = True,
) -> FileResult:
    """Run the selected text-file workflow actions for one file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise ButlerError("empty file")
    truncated = len(text) > MAX_SUMMARY_CHARS
    sample = text[:MAX_SUMMARY_CHARS]
    answer = client.generate(model, prompt_summary(sample)) if include_summary else "Summary not requested."
    if answer.rstrip(" .") == "INSUFFICIENT TEXT":
        answer = "INSUFFICIENT TEXT"
    actions = client.generate(model, prompt_actions(sample)) if include_actions else "NONE"
    if actions.strip().rstrip(".") == "NONE":
        actions = "NONE"
    count = sum(1 for line in actions.splitlines() if line.startswith("- [ ]"))
    stats.action_items += count
    modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return FileResult(path.name, answer, modified, truncated, actions)


def cleanup_prompt(path: Path, folders: Sequence[str]) -> str:
    """Build a cleanup prompt with only permitted file information."""
    preview = path.read_text(encoding="utf-8", errors="replace")[:MAX_CLEANUP_CHARS] if path.suffix.lower() in TEXT_EXTENSIONS else ""
    choices = ", ".join(folders)
    return (f"Return JSON only with folder, new_name, reason, confidence. folder must be one of {choices}. "
            "confidence must be high, medium, or low; reason must be at most 10 words.\n"
            f"file name: {path.name}\nextension: {path.suffix}\npreview: {preview}")


def parse_cleanup(raw: str, original: str, folders: Sequence[str]) -> Dict[str, str]:
    """Parse and sanitize an untrusted cleanup response."""
    import json

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = {}
    if not isinstance(value, dict) or not value.get("folder") or not value.get("new_name") or not value.get("reason"):
        return {"folder": "_review", "new_name": original, "reason": "Review manually", "confidence": "low"}
    folder = str(value["folder"]) if str(value["folder"]) in folders else "_review"
    raw_name = str(value["new_name"]).replace("\\", "/")
    suffix = Path(original).suffix.lower()
    if "/" in raw_name or ".." in raw_name:
        name = Path(original).stem.lower()
    else:
        name = re.sub(r"[^a-z0-9._-]+", "-", Path(raw_name).name.lower().replace(" ", "-")).strip(".-") or Path(original).stem.lower()
    name = f"{Path(name).stem}{suffix}"
    confidence = str(value.get("confidence", "low")).lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    reason = " ".join(str(value["reason"]).split()[:MAX_REASON_WORDS])
    return {"folder": folder, "new_name": name, "reason": reason, "confidence": confidence}


def cleanup_candidates(folder: Path, internal: Iterable[str]) -> List[Path]:
    """List visible non-recursive cleanup candidates."""
    excluded: Set[str] = set(internal)
    return sorted((p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".") and p.name not in excluded), key=lambda p: p.name.lower())

