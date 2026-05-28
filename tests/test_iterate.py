"""End-to-end test of factory.iterate (self-review loop).

We hand-craft the JSON responses the agent would normally write, drive the
full loop init → score v0 → analyst_error → merge_final → ranking → apply →
score v1 → accept → finalize, and verify the timeline + plot + state are
right.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import iterate, scorer  # noqa: E402
from factory.timeline import read_events  # noqa: E402


def _stuff(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


_SKILL_V0 = """---
name: x
description: x
---

# Skill

## Goal

Demo.

## Quick Workflow

1. Step A.
2. Step B.

## Validation

- something.

<!-- SLOW_UPDATE_START -->
<!-- protected -->
<!-- SLOW_UPDATE_END -->
"""


def _rubric_payload(*, empirical_score: int, weighted_target_hint: int = 70) -> dict:
    """Build a structured rubric payload."""
    return {
        "reasoning": "test",
        "dimensions": {
            "trigger_precision":      {"score": 4, "weight": 12, "reason": "ok"},
            "execution_clarity":      {"score": 4, "weight": 12, "reason": "ok"},
            "validation_strength":    {"score": 4, "weight": 12, "reason": "ok"},
            "empirical_executability":{"score": empirical_score, "weight": 15, "reason": "needs real data" if empirical_score < 4 else "smoke is enough"},
            "context_efficiency":     {"score": 3, "weight": 8, "reason": "ok"},
            "reusability":            {"score": 4, "weight": 8, "reason": "ok"},
            "resource_partitioning":  {"score": 3, "weight": 8, "reason": "ok"},
            "compatibility_robustness":{"score": 3, "weight": 8, "reason": "ok"},
            "maintainability":        {"score": 4, "weight": 4, "reason": "ok"},
            "omicos_routing_fitness": {"score": 4, "weight": 13, "reason": "ok"},
        },
        "cross_modality_fabrication": False,
        "top_issues": ["empirical_executability borderline"],
        "recommended_revisions": ["add a real data smoke command",
                                  "expand Validation with 2 more postconditions"],
    }


class TestIterateEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-iter-"))
        self.target = self.tmp / "as-pkg" / "cap_demo"
        # Pretend draft has run: best/ with agent.md + skill/SKILL.md
        best_skill = self.target / "best" / "skill"
        best_skill.mkdir(parents=True)
        (self.target / "best" / "agent.md").write_text("---\nid: cap_demo\n---\nagent body\n",
                                                       encoding="utf-8")
        (best_skill / "SKILL.md").write_text(_SKILL_V0, encoding="utf-8")
        (best_skill / "references").mkdir()
        (best_skill / "assets").mkdir()

    def test_init_and_initial_score_below_pass_continues(self):
        out = iterate.cmd_init(self.target, max_iters=4, Lt=4)
        self.assertEqual(out["phase"], "awaiting_initial_score")

        score_path = _stuff(
            self.target / "_trajectory" / "score" / "iter00_rubric_score.json",
            _rubric_payload(empirical_score=2),
        )
        out = iterate.cmd_record_score(self.target, score_path)
        self.assertEqual(out["phase"], "awaiting_analyst_error")
        # empirical_executability=2 trips the hard gate → verdict fail
        self.assertEqual(out["verdict"], "fail")

    def test_full_iteration_accepts_improved_candidate(self):
        # max_iters=1 so the loop finalizes after one accepted iteration
        # (pass verdict no longer early-exits; we now rely on max_iters or
        # target_score to stop).
        iterate.cmd_init(self.target, max_iters=1, Lt=4, target_score=95.0)

        # Score v0: weak on empirical executability (gate-fail).
        v0_score = _stuff(
            self.target / "_trajectory" / "score" / "iter00_rubric_score.json",
            _rubric_payload(empirical_score=2),
        )
        out = iterate.cmd_record_score(self.target, v0_score)
        self.assertEqual(out["phase"], "awaiting_analyst_error")
        v0_weighted = out["weighted_score"]

        # analyst_error: propose two edits
        ae = _stuff(
            self.target / "_trajectory" / "iter00" / "analyst_error.json",
            {
                "batch_size": 1,
                "failure_summary": [{"failure_type": "missing_real_data_smoke",
                                     "count": 1, "description": "import-only smoke"}],
                "patch": {
                    "reasoning": "raise empirical executability",
                    "edits": [
                        {"op": "append",
                         "content": "\n## Smoke\n\nProvide a real data smoke command.\n"},
                        {"op": "insert_after",
                         "target": "- something.",
                         "content": "\n- a real output postcondition."},
                    ],
                },
            },
        )
        out = iterate.cmd_record_proposal(self.target, "analyst_error", ae)
        self.assertEqual(out["phase"], "awaiting_merge_final")

        mfin = _stuff(
            self.target / "_trajectory" / "iter00" / "merge_final.json",
            {
                "reasoning": "both useful",
                "edits": [
                    {"op": "append",
                     "content": "\n## Smoke\n\nProvide a real data smoke command.\n",
                     "support_count": 1, "source_type": "failure"},
                    {"op": "insert_after",
                     "target": "- something.",
                     "content": "\n- a real output postcondition.",
                     "support_count": 1, "source_type": "failure"},
                ],
            },
        )
        out = iterate.cmd_record_proposal(self.target, "merge_final", mfin)
        self.assertEqual(out["phase"], "awaiting_ranking")

        rk = _stuff(
            self.target / "_trajectory" / "iter00" / "ranking.json",
            {"reasoning": "both", "selected_indices": [0, 1]},
        )
        out = iterate.cmd_record_proposal(self.target, "ranking", rk)
        self.assertEqual(out["phase"], "ready_to_apply")

        out = iterate.cmd_apply(self.target)
        self.assertEqual(out["phase"], "applied_awaiting_score")
        self.assertEqual(out["n_applied"], 2)

        # Score v1: empirical now 4 (gate passes), so verdict = pass + score rises
        v1_score = _stuff(
            self.target / "_trajectory" / "score" / "iter01_rubric_score.json",
            _rubric_payload(empirical_score=4),
        )
        out = iterate.cmd_record_score(self.target, v1_score)
        self.assertEqual(out["decision"], "accept_and_promote_best")
        v1_weighted = out["weighted_score"]
        self.assertGreater(v1_weighted, v0_weighted)
        # Loop ended because max_iters=1 was reached.
        self.assertEqual(out["phase"], "ready_to_finalize")

        result = iterate.cmd_finalize(self.target)
        self.assertEqual(result["phase"], "done")
        self.assertTrue((self.target / "best" / "skill" / "SKILL.md").exists())

        # Best/ now reflects the improved candidate.
        best_text = (self.target / "best" / "skill" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Smoke", best_text)
        self.assertIn("real output postcondition", best_text)

        # Timeline has the right events.
        events = read_events(self.target)
        kinds = [e.kind for e in events]
        self.assertIn("loop_initialized", kinds)
        self.assertIn("proposal_recorded", kinds)
        self.assertIn("candidate_applied", kinds)
        # candidate_evaluated appears twice (v0 initial + v1 accept).
        self.assertEqual(kinds.count("candidate_evaluated"), 2)
        self.assertIn("loop_finalized", kinds)

        # EVOLUTION_PLOT.svg renders the two iterations.
        svg = (self.target / "EVOLUTION_PLOT.svg").read_text(encoding="utf-8")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("#15803d", svg)  # accept color

        # ITERATION_LOG.md has both candidates.
        log = (self.target / "ITERATION_LOG.md").read_text(encoding="utf-8")
        self.assertIn("Iteration Log", log)

    def test_strict_gate_rejects_tie(self):
        iterate.cmd_init(self.target, max_iters=1, Lt=4, target_score=95.0)
        v0_score = _stuff(
            self.target / "_trajectory" / "score" / "iter00_rubric_score.json",
            _rubric_payload(empirical_score=2),
        )
        iterate.cmd_record_score(self.target, v0_score)

        ae = _stuff(
            self.target / "_trajectory" / "iter00" / "analyst_error.json",
            {"batch_size": 1, "failure_summary": [],
             "patch": {"reasoning": "trivial",
                       "edits": [{"op": "append", "content": "\nNoise.\n"}]}},
        )
        iterate.cmd_record_proposal(self.target, "analyst_error", ae)
        mfin = _stuff(
            self.target / "_trajectory" / "iter00" / "merge_final.json",
            {"reasoning": "x", "edits": [{"op": "append", "content": "\nNoise.\n",
                                          "support_count": 1, "source_type": "failure"}]},
        )
        iterate.cmd_record_proposal(self.target, "merge_final", mfin)
        rk = _stuff(
            self.target / "_trajectory" / "iter00" / "ranking.json",
            {"reasoning": "x", "selected_indices": [0]},
        )
        iterate.cmd_record_proposal(self.target, "ranking", rk)
        iterate.cmd_apply(self.target)

        # Score v1 with IDENTICAL dimensions → same weighted score → tie → reject.
        v1_score = _stuff(
            self.target / "_trajectory" / "score" / "iter01_rubric_score.json",
            _rubric_payload(empirical_score=2),
        )
        out = iterate.cmd_record_score(self.target, v1_score)
        self.assertEqual(out["decision"], "reject")


if __name__ == "__main__":
    unittest.main()
