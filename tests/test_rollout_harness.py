"""Lock in the rollout-harness stub schema + protocol.

`factory/rollout.py` is intentionally a stub — it doesn't execute
rollouts. These tests pin the schema and the next-task / record-result /
summarize wiring so that when a real harness is wired in, the contract
doesn't drift.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import rollout  # noqa: E402

import yaml as _yaml  # noqa: E402


class TestRolloutSchema(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-rollout-"))
        self.target = self.tmp / "as-x" / "skills" / "skill-a"
        self.target.mkdir(parents=True)
        (self.target / "eval").mkdir()
        (self.target / "eval" / "train.yaml").write_text(
            _yaml.safe_dump({
                "tasks": [
                    {
                        "task_id": "t1",
                        "inputs": {"q": "hello"},
                        "expected_outputs": {"x": 1},
                        "scoring": {"kind": "exact_match", "key": "x"},
                    },
                    {
                        "task_id": "t2",
                        "inputs": {"q": "world"},
                        "expected_outputs": {"x": 2},
                        "scoring": {"kind": "exact_match", "key": "x"},
                    },
                ]
            }),
            encoding="utf-8",
        )

    def test_next_task_returns_first_unrolled(self):
        out = rollout.cmd_next_task(
            self.target, split="train", epoch=0, step=0, candidate="v000",
        )
        self.assertEqual(out["remaining"], 2)
        self.assertEqual(out["task"]["task_id"], "t1")
        # write_to is the path the harness should write into
        self.assertIn("t1.json", out["write_to"])

    def test_next_task_skips_already_done(self):
        d = (
            self.target / "_trajectory" / "rollouts" / "e00" / "s00" / "v000"
        )
        d.mkdir(parents=True)
        (d / "t1.json").write_text(json.dumps({
            "task_id": "t1", "candidate": "v000", "pass": True, "score": 1.0,
            "outputs": {"x": 1}, "evidence": [], "error": None,
        }), encoding="utf-8")
        out = rollout.cmd_next_task(
            self.target, split="train", epoch=0, step=0, candidate="v000",
        )
        self.assertEqual(out["task"]["task_id"], "t2")
        self.assertEqual(out["remaining"], 1)

    def test_next_task_returns_done_when_complete(self):
        d = (
            self.target / "_trajectory" / "rollouts" / "e00" / "s00" / "v000"
        )
        d.mkdir(parents=True)
        for tid in ("t1", "t2"):
            (d / f"{tid}.json").write_text(json.dumps({
                "task_id": tid, "candidate": "v000", "pass": True,
            }), encoding="utf-8")
        out = rollout.cmd_next_task(
            self.target, split="train", epoch=0, step=0, candidate="v000",
        )
        self.assertEqual(out["remaining"], 0)
        self.assertIn("All 2 tasks", out["next_step"])

    def test_record_result_validates_required_keys(self):
        p = self.tmp / "bad.json"
        p.write_text(json.dumps({"pass": True}), encoding="utf-8")
        with self.assertRaises(ValueError):
            rollout.cmd_record_result(self.target, p)

    def test_record_result_normalizes_pass_alias(self):
        p = self.tmp / "ok.json"
        p.write_text(json.dumps({
            "task_id": "t1", "candidate": "v000", "pass": True, "score": 1.0,
        }), encoding="utf-8")
        out = rollout.cmd_record_result(self.target, p)
        self.assertEqual(out["result"]["pass"], True)
        self.assertEqual(out["result"]["task_id"], "t1")

    def test_summarize_writes_optimizer_shaped_jsonl(self):
        d = (
            self.target / "_trajectory" / "rollouts" / "e00" / "s00" / "v000"
        )
        d.mkdir(parents=True)
        for tid, ok, score in (("t1", True, 1.0), ("t2", False, 0.0)):
            (d / f"{tid}.json").write_text(json.dumps({
                "task_id": tid, "candidate": "v000",
                "pass": ok, "score": score,
                "outputs": {}, "evidence": [], "error": None,
            }), encoding="utf-8")
        out = rollout.cmd_summarize(
            self.target, split="train", epoch=0, step=0, candidate="v000",
        )
        self.assertEqual(out["n_tasks"], 2)
        self.assertEqual(out["n_pass"], 1)
        self.assertAlmostEqual(out["mean_score"], 0.5)
        jsonl = Path(out["jsonl"])
        self.assertTrue(jsonl.exists())
        rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
        self.assertEqual({r["task_id"] for r in rows}, {"t1", "t2"})
        # The jsonl is the shape optimizer.record-rollouts expects: one row per
        # task with task_id + candidate + pass + score keys at top level.
        for r in rows:
            for k in ("task_id", "candidate", "pass", "score"):
                self.assertIn(k, r)


class TestRolloutMissingSplit(unittest.TestCase):
    def test_missing_eval_yaml_raises(self):
        tmp = Path(tempfile.mkdtemp(prefix="osg-rollout-nosplit-"))
        target = tmp / "as-x" / "skills" / "skill-a"
        target.mkdir(parents=True)
        with self.assertRaises(FileNotFoundError):
            rollout.cmd_next_task(target, split="train", epoch=0, step=0,
                                  candidate="v000")


if __name__ == "__main__":
    unittest.main()
