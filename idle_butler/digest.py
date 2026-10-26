"""Digest rendering."""

from typing import Sequence

from .cleanup import PlanEntry
from .state import FileResult, SessionStats, atomic_write
from pathlib import Path
import time
from datetime import datetime


def render_digest(results: Sequence[FileResult], plans: Sequence[PlanEntry], organized: Sequence[dict], model: str, trigger: str, stats: SessionStats, started: float, old: str, auto: bool = False) -> str:
    """Render one newest-first Markdown session."""
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    first = results[0].name if results else "Nothing urgent"
    actions = [f"- [ ] {result.name}: {line[6:]}" for result in results for line in result.action_items.splitlines() if line.startswith("- [ ]")]
    text = (f"# While you were away\nGenerated: {generated} | Model: {model} | Trigger: {trigger}\n"
            f"Stats: {stats.summarized} summarized, {stats.cleanup_suggestions} cleanup suggestions, {stats.action_items} action items, {stats.organized} organized, stopped early: {'yes' if stats.stopped else 'no'}, time: {int(time.monotonic() - started)} seconds\n\n"
            f"## Start here\n{first}\n\n## Action items\n{chr(10).join(actions) if actions else 'No action items found.'}\n\n")
    if organized:
        text += "## Organized while you were away\n" + "\n".join(f"- `{item['original']}` -> `{item['new']}`" for item in organized) + "\n\n"
    if plans:
        title = "Needs your eye" if auto else "Proposed cleanup"
        text += f"## {title}\n\n| Original | Destination | Confidence | Reason |\n|---|---|---|---|\n"
        text += "\n".join(f"| `{p.original}` | `{p.folder}/{p.new_name}` | {p.confidence} | {p.reason} |" for p in plans) + "\n\n"
    text += "## Summaries\n\n" + "\n".join(f"### {r.name}\n{r.answer}\n\n(modified: {r.modified}; truncated: {'yes' if r.truncated else 'no'})\n" for r in results)
    return text + ("\n" + old if old else "")


def save_digest(path: Path, content: str) -> None:
    """Save digest content atomically."""
    atomic_write(path, content)

