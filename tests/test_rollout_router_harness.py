"""First real rollout harness: router-fixture replay.

This goes end-to-end on an agent target that ships an `assets/
routing_fixture.jsonl`:

  1. `factory.rollout build-eval --from-routing-fixture` → eval/train.yaml
  2. `factory.rollout next-task` → which task to roll out
  3. agent writes a result JSON with `outputs.predicted_skill_id`
  4. `factory.rollout record-result --split train` → auto-judges via
     routing_match (pass = predicted == expected)
  5. `factory.rollout summarize` → rollouts.jsonl with judged rows
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


def _make_agent_target_with_fixture(tmp: Path) -> Path:
    """Lay out a minimal agent target with best/assets/routing_fixture.jsonl."""
    target = tmp / "as-x" / "agents" / "pkg_router"
    (target / "best" / "assets").mkdir(parents=True)
    rows = [
        {"user_query": "run Moran's I on visium", "expected_skill_id": "spatial_autocorr"},
        {"q": "neighborhood enrichment z-score", "expected_skill_id": "nhood_enrich"},
        {"user_query": "do something exotic", "expected_skill_id": "fallback"},
    ]
    (target / "best" / "assets" / "routing_fixture.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8",
    )
    return target


class TestRouterHarnessBuildEval(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-rharn-"))
        self.target = _make_agent_target_with_fixture(self.tmp)

    def test_build_eval_emits_one_task_per_row(self):
        out = rollout.cmd_build_eval_from_routing_fixture(self.target, split="train")
        self.assertEqual(out["n_tasks"], 3)
        self.assertEqual(out["n_skipped"], 0)
        # YAML round-trip
        eval_yml = self.target / "eval" / "train.yaml"
        self.assertTrue(eval_yml.exists())
        data = _yaml.safe_load(eval_yml.read_text(encoding="utf-8"))
        ids = [t["task_id"] for t in data["tasks"]]
        self.assertEqual(ids, ["route_0001", "route_0002", "route_0003"])
        # routing_match scoring kind on every task
        kinds = {t["scoring"]["kind"] for t in data["tasks"]}
        self.assertEqual(kinds, {"routing_match"})
        # `q` alias also picked up
        self.assertEqual(
            data["tasks"][1]["inputs"]["user_query"],
            "neighborhood enrichment z-score",
        )

    def test_build_eval_refuses_overwrite_by_default(self):
        rollout.cmd_build_eval_from_routing_fixture(self.target, split="train")
        with self.assertRaises(FileExistsError):
            rollout.cmd_build_eval_from_routing_fixture(self.target, split="train")
        # overwrite=True replaces it
        out = rollout.cmd_build_eval_from_routing_fixture(
            self.target, split="train", overwrite=True,
        )
        self.assertEqual(out["n_tasks"], 3)

    def test_build_eval_skips_malformed_rows(self):
        # append a bad line
        f = self.target / "best" / "assets" / "routing_fixture.jsonl"
        f.write_text(
            f.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8",
        )
        out = rollout.cmd_build_eval_from_routing_fixture(self.target, split="train")
        self.assertEqual(out["n_tasks"], 3)
        self.assertEqual(out["n_skipped"], 1)

    def test_build_eval_missing_fixture_raises(self):
        target2 = self.tmp / "as-x" / "agents" / "no_fixture"
        target2.mkdir(parents=True)
        with self.assertRaises(FileNotFoundError):
            rollout.cmd_build_eval_from_routing_fixture(target2, split="train")


class TestRouterHarnessAutoJudge(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-rharn-judge-"))
        self.target = _make_agent_target_with_fixture(self.tmp)
        rollout.cmd_build_eval_from_routing_fixture(self.target, split="train")

    def _write_result(self, task_id: str, predicted: str | None) -> Path:
        d = (
            self.target / "_trajectory" / "rollouts" / "e00" / "s00" / "v000"
        )
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{task_id}.json"
        payload = {
            "task_id": task_id,
            "candidate": "v000",
            "outputs": {"predicted_skill_id": predicted} if predicted else {},
        }
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_record_result_auto_judges_correct_routing(self):
        # The fixture for route_0001 expects spatial_autocorr
        p = self._write_result("route_0001", "spatial_autocorr")
        out = rollout.cmd_record_result(self.target, p, split="train")
        self.assertEqual(out["result"]["pass"], True)
        self.assertEqual(out["result"]["score"], 1.0)
        self.assertIn("auto_judged", out)
        # Result on disk is now the judged one
        on_disk = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["pass"], True)

    def test_record_result_auto_judges_wrong_routing(self):
        p = self._write_result("route_0001", "totally_wrong")
        out = rollout.cmd_record_result(self.target, p, split="train")
        self.assertEqual(out["result"]["pass"], False)
        self.assertEqual(out["result"]["score"], 0.0)

    def test_record_result_no_split_skips_auto_judge(self):
        p = self._write_result("route_0001", "spatial_autocorr")
        out = rollout.cmd_record_result(self.target, p)
        # No split → pass/score stay None
        self.assertIsNone(out["result"]["pass"])
        self.assertIsNone(out["result"]["score"])
        self.assertNotIn("auto_judged", out)

    def test_record_result_respects_pre_judged_results(self):
        d = (
            self.target / "_trajectory" / "rollouts" / "e00" / "s00" / "v000"
        )
        d.mkdir(parents=True, exist_ok=True)
        p = d / "route_0001.json"
        # Harness already filled pass=False even though prediction is "correct"
        p.write_text(json.dumps({
            "task_id": "route_0001", "candidate": "v000",
            "pass": False, "score": 0.0,
            "outputs": {"predicted_skill_id": "spatial_autocorr"},
        }), encoding="utf-8")
        out = rollout.cmd_record_result(self.target, p, split="train")
        # Harness verdict wins
        self.assertEqual(out["result"]["pass"], False)
        self.assertNotIn("auto_judged", out)

    def test_summarize_auto_judges_unjudged_rows(self):
        # Write three results, all unjudged
        self._write_result("route_0001", "spatial_autocorr")  # ✓
        self._write_result("route_0002", "nhood_enrich")      # ✓
        self._write_result("route_0003", "wrong")             # ✗

        out = rollout.cmd_summarize(
            self.target, split="train", epoch=0, step=0, candidate="v000",
        )
        self.assertEqual(out["n_tasks"], 3)
        self.assertEqual(out["n_pass"], 2)
        self.assertEqual(out["n_auto_judged"], 3)
        self.assertAlmostEqual(out["mean_score"], 2 / 3)
        # JSONL has judged rows
        rows = [json.loads(line)
                for line in Path(out["jsonl"]).read_text(encoding="utf-8").splitlines()]
        passes = {r["task_id"]: r["pass"] for r in rows}
        self.assertEqual(passes, {
            "route_0001": True, "route_0002": True, "route_0003": False,
        })


if __name__ == "__main__":
    unittest.main()
