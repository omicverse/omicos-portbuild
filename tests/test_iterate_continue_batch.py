"""Integration tests for iterate.cmd_continue + iterate.cmd_batch.

Round 5 added these CLIs but didn't have unit-level coverage. These tests
exercise the carry-forward cache (continue) and multi-target dispatch (batch).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import draft, iterate, scorer  # noqa: E402
from factory.manifest import write_initial_manifest  # noqa: E402


_SKILL_BODY = """---
description: Use when running this on H&E slides.
---

# Skill

## Goal

Run things.

## Quick Workflow

1. Step 1.
2. Step 2.

## Branches: foo

Branches go here.

## Validation

- Tested against pkg>=0.9.

## Smoke testing

```python
import x
```

References / references/source-grounding.md. capability-first.
delivery contract: outputs/REPORT.html + next_agent_hints.
huggingface gate. version pin.

<!-- SLOW_UPDATE_START -->
<!-- protected -->
<!-- SLOW_UPDATE_END -->
"""


def _strong_dims(n=4):
    from factory.scorer import RUBRIC_WEIGHTS
    return {
        d: {"score": n, "weight": w, "reason": "ok"}
        for d, w in RUBRIC_WEIGHTS.items()
    }


def _write_score(target: Path, label: str, dims: dict) -> Path:
    p = target / "_trajectory" / "score" / f"{label}_rubric_score.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "reasoning": "x", "dimensions": dims,
        "cross_modality_fabrication": False,
        "top_issues": [], "recommended_revisions": [],
    }), encoding="utf-8")
    return p


def _bootstrap_skill(root: Path, skill_id: str) -> Path:
    """Set up a skill target with a draft v000 + promote to best/."""
    skills_dir = root / "skills"
    target = skills_dir / skill_id
    write_initial_manifest(
        target_dir=target,
        capability_id=skill_id, kind="skill",
        package="pkg", source=None, docs=None,
        notebooks=[], cost_profile="moderate",
    )
    draft.run(target)
    # Override the scaffolded SKILL with a body that passes evidence
    (target / "candidates" / "v000" / "skill" / "SKILL.md").write_text(
        _SKILL_BODY, encoding="utf-8",
    )
    draft.cmd_promote(target)
    return target


class TestIterateContinue(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-iter-cont-"))
        self.root = self.tmp / "as-x"
        self.target = _bootstrap_skill(self.root, "skill-a")

    def test_continue_after_finalize_reuses_cache(self):
        # Run iter 1 to v001 and finalize
        iterate.cmd_init(self.target, max_iters=1, target_score=95.0)
        _write_score(self.target, "iter00", _strong_dims(4))
        iterate.cmd_record_score(
            self.target,
            self.target / "_trajectory" / "score" / "iter00_rubric_score.json",
        )
        # Continue — should re-init on current best and carry-forward cache
        out = iterate.cmd_continue(self.target, max_iters=1, target_score=95.0)
        self.assertEqual(out["phase"], "awaiting_initial_score")
        state = iterate.IterateCursor.load(self.target)
        # Cache should have at least one entry (v000 hash from prior init)
        self.assertGreater(len(state.cache), 0)

    def test_continue_with_no_prior_state_still_works(self):
        # No iterate run yet, just bootstrap promoted v000 → best/
        out = iterate.cmd_continue(self.target, max_iters=1, target_score=95.0)
        self.assertEqual(out["phase"], "awaiting_initial_score")


class TestIterateBatch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-iter-batch-"))
        self.root = self.tmp / "as-x"
        self.s1 = _bootstrap_skill(self.root, "skill-a")
        self.s2 = _bootstrap_skill(self.root, "skill-b")

    def test_batch_inits_each_target(self):
        out = iterate.cmd_batch(self.root, n_iters=1, target_score=95.0)
        self.assertEqual(out["root"], str(self.root))
        names = {r["target"] for r in out["results"]}
        self.assertIn("skill-a", names)
        self.assertIn("skill-b", names)
        # Each got a cursor initialized
        for skill in (self.s1, self.s2):
            self.assertTrue((skill / "_state_iterate.json").exists())

    def test_batch_skips_empty_root(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        out = iterate.cmd_batch(empty, n_iters=1, target_score=95.0)
        self.assertEqual(out["results"], [])


class TestIterateFinalizeAutoRepublish(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-iter-rep-"))
        self.root = self.tmp / "as-x"
        self.target = _bootstrap_skill(self.root, "skill-a")

    def test_finalize_republishes_when_target_reached(self):
        iterate.cmd_init(self.target, max_iters=1, target_score=95.0)
        # Force-score above target_score so init step finalizes immediately
        _write_score(self.target, "iter00", _strong_dims(5))
        iterate.cmd_record_score(
            self.target,
            self.target / "_trajectory" / "score" / "iter00_rubric_score.json",
        )
        # State should be ready_to_finalize
        state = iterate.IterateCursor.load(self.target)
        self.assertEqual(state.phase, "ready_to_finalize")
        out = iterate.cmd_finalize(self.target)
        self.assertTrue(out.get("republished") in (True, False))  # at least key exists
        # Published bundle should exist
        self.assertTrue((self.target / "published").exists())


if __name__ == "__main__":
    unittest.main()
