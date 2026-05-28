"""Tests for the rubric scorer: weighted-score computation, gate logic,
verdict selection, and the score-request → record-score flow.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import scorer  # noqa: E402


def _strong_dims(empirical: int = 4) -> dict[str, dict]:
    """Build a baseline strong dimension set; override `empirical` to test the gate."""
    return {
        "trigger_precision":      {"score": 4, "weight": 12, "reason": "x"},
        "execution_clarity":      {"score": 4, "weight": 12, "reason": "x"},
        "validation_strength":    {"score": 4, "weight": 12, "reason": "x"},
        "empirical_executability":{"score": empirical, "weight": 15, "reason": "x"},
        "context_efficiency":     {"score": 4, "weight": 8,  "reason": "x"},
        "reusability":            {"score": 5, "weight": 8,  "reason": "x"},
        "resource_partitioning":  {"score": 4, "weight": 8,  "reason": "x"},
        "compatibility_robustness":{"score": 4, "weight": 8, "reason": "x"},
        "maintainability":        {"score": 5, "weight": 4,  "reason": "x"},
        "omicos_routing_fitness": {"score": 5, "weight": 13, "reason": "x"},
    }


class TestWeightedScoreAndGates(unittest.TestCase):
    def test_perfect_score(self):
        perfect = {dim: {"score": 5, "weight": w, "reason": "x"}
                   for dim, w in scorer.RUBRIC_WEIGHTS.items()}
        ws = scorer.compute_weighted_score(perfect)
        self.assertAlmostEqual(ws, 100.0)
        self.assertEqual(scorer.gate_failures(perfect), [])
        self.assertEqual(
            scorer.verdict_for(ws, [], cross_modality_fabrication=False),
            "pass",
        )

    def test_strong_dims_pass(self):
        dims = _strong_dims(empirical=4)
        ws = scorer.compute_weighted_score(dims)
        self.assertGreater(ws, 75)
        self.assertEqual(scorer.gate_failures(dims), [])
        self.assertEqual(scorer.verdict_for(ws, [], False), "pass")

    def test_below_threshold_revise(self):
        dims = _strong_dims(empirical=4)
        # Knock down a non-gate dim
        dims["context_efficiency"]["score"] = 1
        dims["reusability"]["score"] = 1
        dims["resource_partitioning"]["score"] = 1
        dims["compatibility_robustness"]["score"] = 1
        dims["maintainability"]["score"] = 1
        ws = scorer.compute_weighted_score(dims)
        # All hard gates still >= 3, but weighted < 75 → revise
        self.assertLess(ws, 75)
        self.assertEqual(scorer.gate_failures(dims), [])
        self.assertEqual(scorer.verdict_for(ws, [], False), "revise")

    def test_hard_gate_fail_overrides_weighted_score(self):
        # Even with everything else at 5, a hard gate at 2 forces fail.
        dims = {dim: {"score": 5, "weight": w, "reason": "x"}
                for dim, w in scorer.RUBRIC_WEIGHTS.items()}
        dims["empirical_executability"]["score"] = 2
        ws = scorer.compute_weighted_score(dims)
        gates = scorer.gate_failures(dims)
        self.assertIn("empirical_executability", gates)
        self.assertEqual(scorer.verdict_for(ws, gates, False), "fail")

    def test_cross_modality_fabrication_forces_fail(self):
        dims = _strong_dims()
        self.assertEqual(
            scorer.verdict_for(scorer.compute_weighted_score(dims), [],
                               cross_modality_fabrication=True),
            "fail",
        )


class TestStaticLints(unittest.TestCase):
    def test_clean_skill_passes(self):
        text = (
            "# Skill\n\n## Goal\n\nBody.\n\n"
            "<!-- SLOW_UPDATE_START -->\n<!-- protected -->\n<!-- SLOW_UPDATE_END -->\n"
        )
        lint = scorer.static_lints(text)
        self.assertTrue(lint.is_clean())

    def test_missing_slow_update(self):
        text = "# Skill\n\n## Goal\n\nBody.\n"
        lint = scorer.static_lints(text)
        self.assertTrue(lint.missing_slow_update_block)
        self.assertFalse(lint.is_clean())

    def test_machine_path_flagged(self):
        text = ("# Skill\n## Goal\n/Users/alice/repo/foo.py\n"
                "<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->\n")
        lint = scorer.static_lints(text)
        self.assertTrue(len(lint.machine_specific_paths) > 0)

    def test_fill_marker_flagged(self):
        text = ("# Skill\n## Goal\n<FILL: stuff>\n"
                "<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->\n")
        lint = scorer.static_lints(text)
        self.assertTrue(lint.todo_or_fill_present)


_GOOD_SKILL_BODY = """---
description: Use when running this skill on H&E slides.
---

# Skill

## Goal

x

## Quick Workflow

1. open_wsi
2. find_tissues
3. feature_extraction

## Branches: feature_extraction(wsi, model)

Pick a backbone.

## Validation

- Confirm wsi.tables exists.
- Tested against lazyslide>=0.9.

## Smoke testing

```python
import lazyslide as zs
assert callable(zs.tl.feature_extraction)
```

Reusable stable job — capability-first naming applies.
See references/source-grounding.md for the API map.
delivery contract: outputs/REPORT.html + handoff_manifest.json + next_agent_hints.
huggingface gate verified before run.

<!-- SLOW_UPDATE_START -->
<!-- protected -->
<!-- SLOW_UPDATE_END -->
"""


class TestRecordScoreFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-scorer-"))
        self.target = self.tmp / "as-x" / "cap_y"
        (self.target / "best" / "skill").mkdir(parents=True)
        (self.target / "best" / "skill" / "SKILL.md").write_text(
            _GOOD_SKILL_BODY, encoding="utf-8"
        )
        payload = {
            "reasoning": "fine",
            "dimensions": _strong_dims(empirical=3),
            "cross_modality_fabrication": False,
            "top_issues": ["empirical_executability at the gate edge"],
            "recommended_revisions": ["add a real data smoke command"],
        }
        json_dir = self.target / "_trajectory" / "score"
        json_dir.mkdir(parents=True)
        self.json_path = json_dir / "best_rubric_score.json"
        self.json_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_record_writes_score_report_md(self):
        out = scorer.cmd_record_score(self.target, self.json_path,
                                      candidate="best")
        self.assertEqual(out["verdict"], "pass")
        report = (self.target / "SCORE_REPORT_best.md").read_text(encoding="utf-8")
        self.assertIn("Verdict", report)
        self.assertIn("empirical_executability", report)


class TestEvidenceCheck(unittest.TestCase):
    """Round 4 bug fix: high score must be backed by evidence keyword."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-evid-"))
        self.target = self.tmp / "x"
        (self.target / "best" / "skill").mkdir(parents=True)
        self.json_path = self.target / "score.json"

    def _write_skill(self, body: str):
        (self.target / "best" / "skill" / "SKILL.md").write_text(
            body, encoding="utf-8"
        )

    def _score(self, dims):
        self.json_path.write_text(json.dumps({
            "reasoning": "x", "dimensions": dims,
            "cross_modality_fabrication": False,
            "top_issues": [], "recommended_revisions": [],
        }), encoding="utf-8")

    def test_high_score_with_evidence_passes(self):
        self._write_skill(_GOOD_SKILL_BODY)
        self._score(_strong_dims(empirical=5))
        out = scorer.cmd_record_score(self.target, self.json_path, candidate="best")
        self.assertEqual(out["verdict"], "pass")

    def test_high_empirical_without_smoke_section_blocks(self):
        # Body lacks "Smoke testing" / "Worked example" — should reject score 5.
        body = _GOOD_SKILL_BODY.replace("## Smoke testing", "## Other heading")
        self._write_skill(body)
        self._score(_strong_dims(empirical=5))
        with self.assertRaises(scorer.EvidenceCheckFailed) as ctx:
            scorer.cmd_record_score(self.target, self.json_path,
                                    candidate="best")
        self.assertIn("empirical_executability", str(ctx.exception))

    def test_bypass_flag_allows_unsupported_score(self):
        body = _GOOD_SKILL_BODY.replace("## Smoke testing", "## Other heading")
        self._write_skill(body)
        self._score(_strong_dims(empirical=5))
        # enforce_evidence=False allows unsupported high scores through.
        out = scorer.cmd_record_score(self.target, self.json_path,
                                      candidate="best",
                                      enforce_evidence=False)
        self.assertEqual(out["verdict"], "pass")

    def test_low_score_does_not_need_evidence(self):
        # Score 3 < 4, evidence threshold not triggered.
        self._write_skill(_GOOD_SKILL_BODY.replace("## Smoke testing",
                                                    "## Other"))
        self._score(_strong_dims(empirical=3))
        out = scorer.cmd_record_score(self.target, self.json_path, candidate="best")
        # 3/5 on a hard gate = "fail" verdict but no evidence error.
        self.assertIn("verdict", out)


if __name__ == "__main__":
    unittest.main()
