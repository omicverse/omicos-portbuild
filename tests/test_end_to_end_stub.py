"""End-to-end stub smoke test (agent-driven model).

Confirms the deterministic factory pieces wire together without any LLM call:
  1. Write an initial manifest.yaml
  2. draft.run() emits candidates/v000/ with SLOW_UPDATE block
  3. optimizer.run() = optimizer.cmd_init(): records the OptimizerState
     cursor in _state.json and surfaces the first agent-facing instruction
     ("evaluate v000 on the selection split, then call record-eval")
  4. validate.validate_target() catches that best/ doesn't exist yet (loop
     hasn't been driven by an agent) AND catches TODO markers in v000
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import draft, optimizer, validate  # noqa: E402
from factory.manifest import read_manifest, write_initial_manifest  # noqa: E402
from factory.patcher import has_slow_update_block  # noqa: E402


class TestStubEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-test-"))
        self.target = self.tmp / "targets" / "test_capability"

    def test_full_pipeline_short_circuits_cleanly(self):
        write_initial_manifest(
            target_dir=self.target,
            capability_id="test_capability",
            kind="capability",
            package="fakepkg",
            source=None,
            docs=None,
            notebooks=[],
            cost_profile="cheap",
        )
        manifest = read_manifest(self.target)
        self.assertEqual(manifest.capability_id, "test_capability")
        self.assertEqual(manifest.kind, "capability")

        # Phase 3: draft v000 (TODO-laden scaffold)
        draft.run(self.target)
        v000 = self.target / "candidates" / "v000"
        self.assertTrue((v000 / "agent.md").exists())
        skill_md = v000 / "skill" / "SKILL.md"
        self.assertTrue(skill_md.exists())
        self.assertTrue(has_slow_update_block(skill_md.read_text(encoding="utf-8")))
        self.assertTrue((v000 / "skill" / "assets" / "acceptance.json").exists())

        # Phase 5: optimizer.run() now initializes the cursor and instructs
        # the agent. It does NOT call any LLM and does NOT advance the loop.
        result = optimizer.run(self.target)
        self.assertEqual(result["phase"], "awaiting_initial_eval")
        self.assertIn("instructions", result)
        instr = result["instructions"]
        self.assertIn("eval/selection.yaml", instr["read"])
        self.assertEqual(instr["then"], "python -m factory.optimizer record-eval <score>")

        # _state.json must exist for the agent to drive subsequent steps.
        state_path = self.target / "_state.json"
        self.assertTrue(state_path.exists())
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state_data["capability_id"], "test_capability")
        self.assertEqual(state_data["epoch"], 1)
        self.assertEqual(state_data["step"], 0)

        # next-step is idempotent: calling again returns the same phase.
        result2 = optimizer.run(self.target)
        self.assertEqual(result2["phase"], "awaiting_initial_eval")

        # Validator should flag missing best/ + TODO markers in v000.
        issues = validate.validate_target(self.target)
        joined = " | ".join(issues)
        self.assertIn("best/ not produced yet", joined)


if __name__ == "__main__":
    unittest.main()
