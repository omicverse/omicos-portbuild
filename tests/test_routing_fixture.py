"""Verify publisher enforces routing-fixture file when agent.md promises it."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory.publisher import _check_routing_fixture_if_promised  # noqa: E402


_AGENT_WITH_PROMISE = """---
id: x
---
You ship `assets/routing_fixture.jsonl` per the routing-fixture convention.
"""

_AGENT_WITHOUT_PROMISE = """---
id: x
---
Just an agent, no fixture promised.
"""


class TestRoutingFixturePromise(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-fixture-"))
        self.target = self.tmp / "as-x" / "agents" / "ag"
        (self.target / "best" / "assets").mkdir(parents=True)

    def test_no_promise_no_check(self):
        issues = _check_routing_fixture_if_promised(self.target, _AGENT_WITHOUT_PROMISE)
        self.assertEqual(issues, [])

    def test_promise_without_file_blocks(self):
        issues = _check_routing_fixture_if_promised(self.target, _AGENT_WITH_PROMISE)
        self.assertEqual(len(issues), 1)
        self.assertIn("no file", issues[0])

    def test_promise_with_valid_file_passes(self):
        (self.target / "best" / "assets" / "routing_fixture.jsonl").write_text(
            '{"q": "hello", "expected_skill_id": "skill-a"}\n'
            '{"user_query": "hi", "expected_skill_id": "skill-b"}\n',
            encoding="utf-8",
        )
        issues = _check_routing_fixture_if_promised(self.target, _AGENT_WITH_PROMISE)
        self.assertEqual(issues, [])

    def test_promise_with_invalid_json_blocks(self):
        (self.target / "best" / "assets" / "routing_fixture.jsonl").write_text(
            'not json at all\n', encoding="utf-8",
        )
        issues = _check_routing_fixture_if_promised(self.target, _AGENT_WITH_PROMISE)
        self.assertTrue(any("invalid JSON" in i for i in issues))

    def test_promise_with_missing_keys_blocks(self):
        (self.target / "best" / "assets" / "routing_fixture.jsonl").write_text(
            '{"q": "missing-expected"}\n', encoding="utf-8",
        )
        issues = _check_routing_fixture_if_promised(self.target, _AGENT_WITH_PROMISE)
        self.assertTrue(any("missing required keys" in i for i in issues))

    def test_promise_with_empty_file_blocks(self):
        (self.target / "best" / "assets" / "routing_fixture.jsonl").write_text("", encoding="utf-8")
        issues = _check_routing_fixture_if_promised(self.target, _AGENT_WITH_PROMISE)
        self.assertTrue(any("empty" in i for i in issues))


if __name__ == "__main__":
    unittest.main()
