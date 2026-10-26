"""Workflow configuration tests."""

import unittest

from idle_butler.cli import normalize_actions
from idle_butler.state import ButlerError


class WorkflowTests(unittest.TestCase):
    """Verify workflow steps remain within the safe built-in action set."""

    def test_duplicate_steps_are_removed_in_order(self) -> None:
        self.assertEqual(normalize_actions(["cleanup", "actions", "cleanup"]), ("cleanup", "actions"))

    def test_unknown_steps_are_rejected(self) -> None:
        with self.assertRaises(ButlerError):
            normalize_actions(["run_shell_command"])

    def test_comma_separated_steps_are_supported(self) -> None:
        self.assertEqual(normalize_actions("summarize, actions"), ("summarize", "actions"))

