"""Drive a synthetic SkillOpt trajectory through the optimizer cursor and
verify that timeline.jsonl + ITERATION_LOG.md + EVOLUTION_PLOT.svg are all
populated correctly. No LLM involved — we hand-craft the JSON responses
the agent would normally write.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import draft, optimizer, plot_evolution  # noqa: E402
from factory.manifest import write_initial_manifest  # noqa: E402
from factory.timeline import read_events  # noqa: E402


def _stuff(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TestTimelineAndPlot(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-evo-"))
        self.target = self.tmp / "targets" / "evo_capability"

    def test_one_full_step_then_finalize(self):
        # Bootstrap: manifest + draft v000.
        write_initial_manifest(
            target_dir=self.target,
            capability_id="evo_capability",
            kind="capability",
            package="fake",
            source=None,
            docs=None,
            notebooks=[],
            cost_profile="cheap",
        )
        draft.run(self.target)

        # `optimizer.run` initializes the cursor.
        out = optimizer.run(self.target)
        self.assertEqual(out["phase"], "awaiting_initial_eval")

        events_after_init = read_events(self.target)
        self.assertTrue(any(e.kind == "loop_initialized" for e in events_after_init))

        # Initial eval: pretend v000 scored 0.50.
        out = optimizer.cmd_record_eval(self.target, 0.50)
        self.assertEqual(out["phase"], "awaiting_rollouts")

        # Fake rollouts JSONL (3 failures, 2 successes).
        rollouts_path = self.target / "_trajectory" / "e01" / "s00" / "rollouts.jsonl"
        rollouts_path.parent.mkdir(parents=True, exist_ok=True)
        with rollouts_path.open("w", encoding="utf-8") as f:
            for i, ok in enumerate([False, False, False, True, True]):
                f.write(json.dumps({
                    "task_id": f"t{i}",
                    "score": 1.0 if ok else 0.0,
                    "success": ok,
                    "trace": "fake-trace",
                }) + "\n")
        out = optimizer.cmd_record_rollouts(self.target, rollouts_path)
        self.assertEqual(out["phase"], "awaiting_analyst_error")

        # analyst_error proposes 2 edits, analyst_success proposes 1.
        ae = _stuff(
            self.target / "_trajectory" / "e01" / "s00" / "analyst_error.json",
            {
                "batch_size": 3,
                "failure_summary": [
                    {"failure_type": "missing_branch_coverage", "count": 2,
                     "description": "method= branch missing"},
                ],
                "patch": {
                    "reasoning": "common failure: method= branch missing",
                    "edits": [
                        {"op": "append",
                         "content": "Document the `method='advanced'` branch explicitly."},
                        {"op": "insert_after",
                         "target": "## Quick Workflow",
                         "content": "\nBefore picking a method, inspect lazyslide.tl.method_zoo()."},
                    ],
                },
            },
        )
        out = optimizer.cmd_record_proposal(self.target, "analyst_error", ae)
        self.assertEqual(out["phase"], "awaiting_analyst_success")

        asj = _stuff(
            self.target / "_trajectory" / "e01" / "s00" / "analyst_success.json",
            {
                "batch_size": 2,
                "success_patterns": ["pre-tile QC always pays off"],
                "patch": {
                    "reasoning": "encode pre-tile QC",
                    "edits": [
                        {"op": "append",
                         "content": "Always confirm tile shape (n, 3, 224, 224) before embedding."},
                    ],
                },
            },
        )
        out = optimizer.cmd_record_proposal(self.target, "analyst_success", asj)
        self.assertEqual(out["phase"], "awaiting_merge_failure")

        mf = _stuff(
            self.target / "_trajectory" / "e01" / "s00" / "merge_failure.json",
            {
                "reasoning": "all merged",
                "edits": [
                    {"op": "append",
                     "content": "Document the `method='advanced'` branch explicitly.",
                     "support_count": 2,
                     "source_type": "failure"},
                ],
            },
        )
        out = optimizer.cmd_record_proposal(self.target, "merge_failure", mf)
        self.assertEqual(out["phase"], "awaiting_merge_success")

        ms = _stuff(
            self.target / "_trajectory" / "e01" / "s00" / "merge_success.json",
            {
                "reasoning": "one success edit",
                "edits": [
                    {"op": "append",
                     "content": "Always confirm tile shape (n, 3, 224, 224) before embedding.",
                     "support_count": 1,
                     "source_type": "success"},
                ],
            },
        )
        out = optimizer.cmd_record_proposal(self.target, "merge_success", ms)
        self.assertEqual(out["phase"], "awaiting_merge_final")

        mfin = _stuff(
            self.target / "_trajectory" / "e01" / "s00" / "merge_final.json",
            {
                "reasoning": "failure first, then success",
                "edits": [
                    {"op": "append",
                     "content": "Document the `method='advanced'` branch explicitly.",
                     "support_count": 2,
                     "source_type": "failure"},
                    {"op": "append",
                     "content": "Always confirm tile shape (n, 3, 224, 224) before embedding.",
                     "support_count": 1,
                     "source_type": "success"},
                ],
            },
        )
        out = optimizer.cmd_record_proposal(self.target, "merge_final", mfin)
        self.assertEqual(out["phase"], "awaiting_ranking")

        # Select both edits (budget L_t=4 by default).
        rk = _stuff(
            self.target / "_trajectory" / "e01" / "s00" / "ranking.json",
            {"reasoning": "both helpful", "selected_indices": [0, 1]},
        )
        out = optimizer.cmd_record_proposal(self.target, "ranking", rk)
        self.assertEqual(out["phase"], "ready_to_apply")

        # Materialize the new candidate.
        out = optimizer.cmd_apply(self.target)
        self.assertEqual(out["phase"], "applied_awaiting_eval")
        self.assertEqual(out["n_applied"], 2)
        self.assertEqual(out["n_skipped"], 0)
        self.assertTrue(out["candidate_dir"].startswith("candidates/v"))

        # Pretend the new candidate scored 0.62 — strictly > 0.50 so it's accepted.
        out = optimizer.cmd_record_eval(self.target, 0.62)
        # With 1 step per epoch, this triggers slow_update path.
        self.assertEqual(out["phase"], "awaiting_slow_update")

        # Provide slow_update content. Re-evaluation will gate it again; we
        # short-circuit by jumping straight to finalize after rejecting it.
        su = _stuff(
            self.target / "_trajectory" / "e01" / "slow_update.json",
            {"reasoning": "stable", "slow_update_content": "When picking a backbone, prefer UNI2 if HF gate passes."},
        )
        out = optimizer.cmd_record_proposal(self.target, "slow_update", su)
        self.assertEqual(out["phase"], "applied_awaiting_eval")
        # Slow update candidate scores worse — rejected.
        out = optimizer.cmd_record_eval(self.target, 0.58)
        # Cursor moves on. Either next epoch or finalize.

        # Force-finalize for the test.
        # (In a real run the agent walks the loop to completion.)
        # Adjust state to finalize-ready.
        from factory.optimizer import CursorState
        st = CursorState.load(self.target)
        st.phase = "ready_to_finalize"
        st.save(self.target)
        result = optimizer.cmd_finalize(self.target)
        self.assertEqual(result["phase"], "done")
        self.assertTrue((self.target / "best" / "skill" / "SKILL.md").exists())

        # ---- The actual evolution-recording assertions ---- #
        events = read_events(self.target)
        kinds = {e.kind for e in events}
        for required in (
            "loop_initialized",
            "rollouts_recorded",
            "proposal_recorded",
            "candidate_applied",
            "candidate_evaluated",
            "slow_update_applied",
            "loop_finalized",
        ):
            self.assertIn(required, kinds, f"missing event kind {required}")

        # ITERATION_LOG.md is rendered after every event (including finalize).
        log = (self.target / "ITERATION_LOG.md").read_text(encoding="utf-8")
        self.assertIn("Iteration Log", log)
        self.assertIn("evo_capability", log)
        self.assertIn("Epoch 1", log)
        self.assertIn("Rejected attempts", log)  # the 0.58 slow_update rejection
        self.assertIn("Loop finalized at", log)

        # EVOLUTION_PLOT.svg was generated at finalize.
        svg_path = self.target / "EVOLUTION_PLOT.svg"
        self.assertTrue(svg_path.exists())
        svg = svg_path.read_text(encoding="utf-8")
        self.assertTrue(svg.startswith("<svg"))
        # At least one accepted-circle (green) and at least one rejected-square (red).
        self.assertIn("#15803d", svg)  # accept color
        self.assertIn("#b91c1c", svg)  # reject color

        # Verify the standalone plot_evolution CLI also works.
        out_path = plot_evolution.write_svg(self.target)
        self.assertEqual(out_path, svg_path)
        self.assertTrue(out_path.read_text(encoding="utf-8").startswith("<svg"))


if __name__ == "__main__":
    unittest.main()
