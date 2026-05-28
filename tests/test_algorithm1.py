"""Tests for the Algorithm 1 state machine (no LLM calls)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory.algorithm1 import (  # noqa: E402
    OptimizerConfig,
    OptimizerState,
    RejectedEdit,
    SelectionCache,
    StepBuffer,
    skill_hash,
)


class TestSkillHash(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(skill_hash("hello"), skill_hash("hello"))

    def test_differs_on_whitespace(self):
        self.assertNotEqual(skill_hash("hello\n"), skill_hash("hello"))


class TestSelectionCache(unittest.TestCase):
    def test_put_get(self):
        c = SelectionCache()
        c.put("h1", 0.85)
        self.assertEqual(c.get("h1"), 0.85)
        self.assertIsNone(c.get("h2"))
        self.assertIn("h1", c)
        self.assertNotIn("h2", c)


class TestStepBuffer(unittest.TestCase):
    def test_reset_clears(self):
        b = StepBuffer()
        b.add_rejected(RejectedEdit(epoch=1, step=2, edits_json="[]", score_drop=0.1))
        b.add_failure_patterns([{"type": "X", "count": 4}])
        self.assertEqual(len(b.rejected), 1)
        self.assertEqual(len(b.failure_patterns), 1)
        b.reset()
        self.assertEqual(len(b.rejected), 0)
        self.assertEqual(len(b.failure_patterns), 0)

    def test_optimizer_context_shape(self):
        b = StepBuffer()
        b.add_rejected(RejectedEdit(epoch=1, step=3, edits_json='{"x":1}', score_drop=0.05))
        b.add_failure_patterns([{"failure_type": "wrong_branch", "count": 5}])
        ctx = b.as_optimizer_context()
        self.assertEqual(ctx["rejected_edits"][0]["epoch"], 1)
        self.assertEqual(ctx["observed_failure_patterns"][0]["failure_type"], "wrong_branch")


class TestOptimizerStateGate(unittest.TestCase):
    def test_initial_caches_score(self):
        st = OptimizerState.initial(s0="skill v0", score_s0_on_sel=0.5)
        self.assertEqual(st.score_cur, 0.5)
        self.assertEqual(st.score_best, 0.5)
        self.assertEqual(len(st.C), 1)
        self.assertIn(skill_hash("skill v0"), st.C)

    def test_strict_gate_rejects_tie(self):
        st = OptimizerState.initial("v0", 0.5)
        decision = st.consider_candidate("v1", 0.5, gate_strict=True)
        self.assertEqual(decision, "reject")
        self.assertEqual(st.s_cur, "v0")

    def test_strict_gate_accepts_strictly_greater(self):
        st = OptimizerState.initial("v0", 0.5)
        decision = st.consider_candidate("v1", 0.6, gate_strict=True)
        self.assertEqual(decision, "accept_and_promote_best")
        self.assertEqual(st.s_cur, "v1")
        self.assertEqual(st.s_best, "v1")
        self.assertEqual(st.score_best, 0.6)

    def test_accept_only_when_score_between_cur_and_best(self):
        # Construct a state where score_best > score_cur (possible after a
        # regression on score_cur that wasn't supposed to happen by gate
        # rules, but the dataclass should still handle it cleanly).
        st = OptimizerState(
            s_cur="vC",
            s_best="vB",
            score_cur=0.5,
            score_best=0.7,
        )
        # New candidate beats cur but not best.
        decision = st.consider_candidate("v_new", 0.6, gate_strict=True)
        self.assertEqual(decision, "accept_only")
        self.assertEqual(st.s_cur, "v_new")
        self.assertEqual(st.s_best, "vB")


class TestCachedOrEval(unittest.TestCase):
    def test_cache_hit_skips_eval(self):
        st = OptimizerState.initial("v0", 0.5)
        # Pre-cache v1
        st.C.put(skill_hash("v1"), 0.9)

        calls = []
        def eval_fn(s: str) -> float:
            calls.append(s)
            return 0.0

        score, hit = st.cached_or_eval("v1", eval_fn)
        self.assertTrue(hit)
        self.assertEqual(score, 0.9)
        self.assertEqual(calls, [])

    def test_cache_miss_calls_eval_and_stores(self):
        st = OptimizerState.initial("v0", 0.5)
        score, hit = st.cached_or_eval("v_new", lambda s: 0.71)
        self.assertFalse(hit)
        self.assertEqual(score, 0.71)
        self.assertEqual(st.C.get(skill_hash("v_new")), 0.71)


class TestLearningRateSchedule(unittest.TestCase):
    def test_constant_returns_base_lt(self):
        cfg = OptimizerConfig(epochs=4, edit_budget_Lt=4, lr_floor=2, lr_schedule="constant")
        for e in range(1, 5):
            self.assertEqual(cfg.lr_for_epoch(e), 4)

    def test_linear_decays_to_floor(self):
        cfg = OptimizerConfig(epochs=4, edit_budget_Lt=4, lr_floor=2, lr_schedule="linear")
        self.assertEqual(cfg.lr_for_epoch(1), 4)
        self.assertEqual(cfg.lr_for_epoch(4), 2)

    def test_cosine_decays_to_floor(self):
        cfg = OptimizerConfig(epochs=4, edit_budget_Lt=8, lr_floor=2, lr_schedule="cosine")
        self.assertEqual(cfg.lr_for_epoch(1), 8)
        self.assertEqual(cfg.lr_for_epoch(4), 2)
        mid = cfg.lr_for_epoch(2)
        self.assertGreater(mid, 2)
        self.assertLess(mid, 8)


if __name__ == "__main__":
    unittest.main()
