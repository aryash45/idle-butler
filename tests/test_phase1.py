"""Phase 1 unit tests."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from idle_butler.cleanup import PlanEntry, apply_plan, collision_name, load_plan, save_plan, undo
from idle_butler.digest import render_digest
from idle_butler.idle import parse_linux_idle_seconds, parse_macos_idle_seconds
from idle_butler.jobs import parse_cleanup
from idle_butler.cli import normalize_actions
from idle_butler.llm import OllamaClient
from idle_butler.state import FileResult, SessionStats, ButlerError, read_json, write_json


class PhaseOneTests(unittest.TestCase):
    """Test pure logic and filesystem safety without a running model."""

    def test_filename_fallback_and_collision(self) -> None:
        entry = parse_cleanup("not json", "notes.txt", ("Study",))
        self.assertEqual(entry["folder"], "_review")
        self.assertEqual(entry["new_name"], "notes.txt")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "Finance").mkdir()
            (root / "Finance" / "bill.pdf").write_text("x", encoding="utf-8")
            self.assertEqual(collision_name(root, PlanEntry("new.pdf", "Finance", "bill.pdf", "review", "high"), set()), "bill-2.pdf")

    def test_idle_parsers_and_containment(self) -> None:
        self.assertEqual(parse_linux_idle_seconds("2500\n"), 2.5)
        self.assertEqual(parse_macos_idle_seconds('"HIDIdleTime" = 3000000000'), 3.0)

    def test_plan_log_round_trip_and_apply_undo(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "bill.txt"
            source.write_text("invoice", encoding="utf-8")
            entry = PlanEntry("bill.txt", "Finance", "bill.txt", "invoice", "high")
            save_plan(root, [entry])
            self.assertEqual(load_plan(root), [entry])
            with patch("builtins.input", return_value="y"):
                self.assertEqual(apply_plan(root), 0)
            self.assertFalse(source.exists())
            self.assertTrue((root / "Finance" / "bill.txt").exists())
            self.assertEqual(undo(root), 0)
            self.assertTrue(source.exists())

    def test_digest_rendering(self) -> None:
        result = FileResult("notes.txt", "- one", "today", action_items="- [ ] Call Sam")
        digest = render_digest([result], [], [], "model", "manual --now", SessionStats(summarized=1, action_items=1), 0, "")
        self.assertIn("## Start here", digest)
        self.assertIn("Call Sam", digest)

    def test_mocked_ollama_json_generation(self) -> None:
        client = OllamaClient()
        payload = json.dumps({"response": "ok"}).encode("utf-8")
        with patch.object(client, "_request", return_value=payload) as request:
            self.assertEqual(client.generate("model", "prompt", json_mode=True), "ok")
        self.assertEqual(request.call_count, 1)

    def test_corrupt_state_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "state.json"
            path.write_text("{", encoding="utf-8")
            self.assertEqual(read_json(path, {"version": 1}), {"version": 1})

    def test_workflow_actions_are_constrained(self) -> None:
        self.assertEqual(normalize_actions(["summarize", "cleanup", "summarize"]), ("summarize", "cleanup"))
        with self.assertRaises(ButlerError):
            normalize_actions(["shell"])


if __name__ == "__main__":
    unittest.main()

