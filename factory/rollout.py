"""Rollout-harness adapter — agent-driven stub.

`factory.iterate` runs the no-rollout self-review loop (RUBRIC-driven). For
capabilities where a real rollout harness exists (e.g. routing-accuracy on
a routing fixture, or skill-execution on a held-out task suite), the agent
needs a clear protocol for: which task to roll out next, where to write
the result, and how a batch of results aggregates into the JSONL that
`factory.optimizer record-rollouts` consumes.

This module is the protocol; it does NO LLM call and does NO eval-task
execution. The schema + CLI here exist so that:

  1. Real harnesses (e.g. a router fixture validator, or a skill smoke
     runner) can plug in by emitting one `RolloutResult` per task and
     appending it through `record_result`.
  2. The agent (Claude Code / Codex) using this harness has a single
     surface to interact with — `next-task` to see what to do next and
     `record-result` to log what they did.
  3. `summarize` turns a recorded batch into the JSONL shape
     `factory.optimizer record-rollouts` expects.

See ROLLOUT_HARNESS.md for the design and the wiring contract.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a hard dep of the rest of the kit
    yaml = None


# --------------------------------------------------------------------------- #
# Schemas                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class EvalTask:
    """One task entry from `eval/<split>.yaml`.

    The harness reads `task_id`, dispatches the work however it likes (LLM
    in conversation, smoke runner, fixture replay), and the result is
    judged by `scoring`.
    """

    task_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    expected_outputs: dict[str, Any] = field(default_factory=dict)
    scoring: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvalTask":
        return cls(
            task_id=str(raw["task_id"]),
            inputs=dict(raw.get("inputs") or {}),
            expected_outputs=dict(raw.get("expected_outputs") or {}),
            scoring=dict(raw.get("scoring") or {}),
            notes=str(raw.get("notes") or ""),
        )


@dataclass
class RolloutResult:
    """What the harness wrote back for one task.

    `pass_` is the harness's own judgment (True/False/None). `score` is the
    scalar the optimizer-side scorer ultimately uses; `pass_` and `score`
    are independent so the harness can record "I think this passed" while
    leaving scoring to a separate step.
    """

    task_id: str
    candidate: str
    pass_: Optional[bool] = None
    score: Optional[float] = None
    outputs: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pass"] = d.pop("pass_")
        return d


# --------------------------------------------------------------------------- #
# IO helpers                                                                  #
# --------------------------------------------------------------------------- #


def _load_split(target: Path, split: str) -> list[EvalTask]:
    yml = target / "eval" / f"{split}.yaml"
    if not yml.exists():
        raise FileNotFoundError(
            f"no {yml}; create eval/<split>.yaml (train|selection|test) first "
            "(or build it from a router fixture with "
            "`factory.rollout build-eval --from-routing-fixture`)"
        )
    if yaml is None:
        raise RuntimeError("PyYAML is required to read eval/<split>.yaml")
    raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
    tasks = raw.get("tasks") or raw.get("entries") or []
    return [EvalTask.from_dict(t) for t in tasks]


def _rollouts_dir(target: Path, epoch: int, step: int, candidate: str) -> Path:
    d = (
        target
        / "_trajectory"
        / "rollouts"
        / f"e{epoch:02d}"
        / f"s{step:02d}"
        / candidate
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# Commands                                                                    #
# --------------------------------------------------------------------------- #


def cmd_next_task(
    target: Path,
    *,
    split: str,
    epoch: int,
    step: int,
    candidate: str,
) -> dict[str, Any]:
    """Print the next un-rolled task for this (split, epoch, step, candidate).

    The harness is whatever pops out of `inputs`; this command does NOT
    decide how to run it. It only reports which task to do next.
    """
    tasks = _load_split(target, split)
    done = {p.stem for p in _rollouts_dir(target, epoch, step, candidate).glob("*.json")}
    pending = [t for t in tasks if t.task_id not in done]
    if not pending:
        return {
            "split": split,
            "candidate": candidate,
            "remaining": 0,
            "next_step": (
                f"All {len(tasks)} tasks rolled out — call "
                f"`python -m factory.rollout summarize {target} --split {split} "
                f"--epoch {epoch} --step {step} --candidate {candidate}` to "
                f"emit the JSONL for `optimizer record-rollouts`."
            ),
        }
    t = pending[0]
    return {
        "split": split,
        "candidate": candidate,
        "remaining": len(pending),
        "task": asdict(t),
        "write_to": str(
            _rollouts_dir(target, epoch, step, candidate) / f"{t.task_id}.json"
        ),
        "instructions": {
            "do": (
                f"Execute task {t.task_id!r} against the skill at "
                f"`candidates/{candidate}/skill/SKILL.md`. Record what the "
                "agent produced + your pass/fail judgment + evidence "
                "pointers in the JSON path above (schema = RolloutResult)."
            ),
            "then": (
                f"python -m factory.rollout next-task {target} "
                f"--split {split} --epoch {epoch} --step {step} "
                f"--candidate {candidate}"
            ),
        },
    }


# --------------------------------------------------------------------------- #
# Judges — convert raw harness outputs into pass/score against an EvalTask    #
# --------------------------------------------------------------------------- #


def _judge_routing_match(task: EvalTask, outputs: dict[str, Any]) -> dict[str, Any]:
    """Pass iff outputs[predicted_key] == expected_outputs[expected_key].

    `scoring` shape: {kind: "routing_match", predicted_key?: str, expected_key?: str}
    Defaults: predicted_key="predicted_skill_id", expected_key="expected_skill_id".
    """
    s = task.scoring or {}
    p_key = s.get("predicted_key", "predicted_skill_id")
    e_key = s.get("expected_key", "expected_skill_id")
    predicted = outputs.get(p_key)
    expected = task.expected_outputs.get(e_key)
    ok = predicted is not None and predicted == expected
    return {
        "pass": bool(ok),
        "score": 1.0 if ok else 0.0,
        "judge_notes": (
            f"predicted={predicted!r} expected={expected!r} "
            f"(predicted_key={p_key!r}, expected_key={e_key!r})"
        ),
    }


_JUDGES = {
    "routing_match": _judge_routing_match,
}


def _judge(task: EvalTask, outputs: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return {pass, score, judge_notes} if scoring.kind has a registered judge,
    else None (the harness owns the verdict).
    """
    kind = (task.scoring or {}).get("kind")
    fn = _JUDGES.get(kind)
    if fn is None:
        return None
    return fn(task, outputs)


def _find_task_by_id(target: Path, split: str, task_id: str) -> Optional[EvalTask]:
    for t in _load_split(target, split):
        if t.task_id == task_id:
            return t
    return None


# --------------------------------------------------------------------------- #
# record-result + summarize                                                   #
# --------------------------------------------------------------------------- #


def cmd_record_result(
    target: Path,
    json_path: Path,
    *,
    split: Optional[str] = None,
) -> dict[str, Any]:
    """Validate one rollout result. If the harness left pass/score unset and
    the scoring kind has a registered judge, auto-fill them.

    `split` is needed for the auto-judge to look up the EvalTask. If omitted,
    auto-judge is skipped (the harness is responsible for pass/score).
    """
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    for k in ("task_id", "candidate"):
        if k not in raw:
            raise ValueError(f"{json_path}: missing required key {k!r}")
    if "pass" in raw and "pass_" not in raw:
        raw["pass_"] = raw.pop("pass")
    result = RolloutResult(
        task_id=str(raw["task_id"]),
        candidate=str(raw["candidate"]),
        pass_=raw.get("pass_"),
        score=raw.get("score"),
        outputs=dict(raw.get("outputs") or {}),
        evidence=list(raw.get("evidence") or []),
        error=raw.get("error"),
    )

    auto = None
    if split and (result.pass_ is None or result.score is None):
        task = _find_task_by_id(target, split, result.task_id)
        if task is not None:
            judged = _judge(task, result.outputs)
            if judged is not None:
                if result.pass_ is None:
                    result.pass_ = judged["pass"]
                if result.score is None:
                    result.score = judged["score"]
                auto = judged

    # Persist the (possibly auto-judged) result back to disk so summarize sees it.
    json_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    out: dict[str, Any] = {"recorded": str(json_path), "result": result.to_dict()}
    if auto is not None:
        out["auto_judged"] = auto
    return out


def cmd_summarize(
    target: Path,
    *,
    split: str,
    epoch: int,
    step: int,
    candidate: str,
) -> dict[str, Any]:
    """Aggregate per-task JSONs into the rollouts.jsonl shape optimizer expects.

    For any per-task JSON missing pass/score where the scoring kind has a
    registered judge (e.g. routing_match), the judge runs at summarize
    time so the aggregate isn't skewed by un-judged rows.
    """
    d = _rollouts_dir(target, epoch, step, candidate)
    # Index task_id → EvalTask once so summarize can auto-judge cheaply.
    try:
        tasks_by_id = {t.task_id: t for t in _load_split(target, split)}
    except FileNotFoundError:
        tasks_by_id = {}
    rows: list[dict[str, Any]] = []
    pass_count = 0
    score_sum = 0.0
    n_scored = 0
    n_auto_judged = 0
    for p in sorted(d.glob("*.json")):
        raw = json.loads(p.read_text(encoding="utf-8"))
        if "pass" in raw and "pass_" not in raw:
            raw["pass_"] = raw.pop("pass")
        # Auto-judge if pass/score missing and we have a judge for this scoring kind
        if raw.get("pass_") is None or raw.get("score") is None:
            task = tasks_by_id.get(raw.get("task_id"))
            if task is not None:
                judged = _judge(task, dict(raw.get("outputs") or {}))
                if judged is not None:
                    if raw.get("pass_") is None:
                        raw["pass_"] = judged["pass"]
                    if raw.get("score") is None:
                        raw["score"] = judged["score"]
                    n_auto_judged += 1
        rows.append({
            "task_id": raw["task_id"],
            "candidate": raw["candidate"],
            "pass": raw.get("pass_"),
            "score": raw.get("score"),
            "outputs": raw.get("outputs") or {},
            "evidence": raw.get("evidence") or [],
            "error": raw.get("error"),
        })
        if raw.get("pass_") is True:
            pass_count += 1
        if isinstance(raw.get("score"), (int, float)):
            score_sum += float(raw["score"])
            n_scored += 1
    jsonl = d.parent / f"{candidate}.rollouts.jsonl"
    with jsonl.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {
        "split": split,
        "candidate": candidate,
        "n_tasks": len(rows),
        "n_pass": pass_count,
        "n_auto_judged": n_auto_judged,
        "mean_score": (score_sum / n_scored) if n_scored else None,
        "jsonl": str(jsonl),
        "next_step": (
            f"python -m factory.optimizer record-rollouts {target} {jsonl}"
        ),
    }


# --------------------------------------------------------------------------- #
# build-eval — first real harness: router-fixture replay                      #
# --------------------------------------------------------------------------- #


def _routing_fixture_path(target: Path) -> Path:
    """Locate `assets/routing_fixture.jsonl` for an agent target.

    Mirrors `publisher._check_routing_fixture_if_promised`: the fixture
    lives at `<target>/best/assets/routing_fixture.jsonl`.
    """
    return target / "best" / "assets" / "routing_fixture.jsonl"


def cmd_build_eval_from_routing_fixture(
    target: Path,
    *,
    split: str = "train",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Convert an agent target's `best/assets/routing_fixture.jsonl` into
    `eval/<split>.yaml` with one `routing_match` task per row.

    This is the first real rollout harness: the agent reads the candidate
    router's `agent.md`, picks `predicted_skill_id` for each `user_query`,
    and the auto-judge scores against `expected_skill_id`.
    """
    if yaml is None:
        raise RuntimeError("PyYAML required to write eval/<split>.yaml")
    fixture = _routing_fixture_path(target)
    if not fixture.exists():
        raise FileNotFoundError(
            f"no routing fixture at {fixture}; an agent target with a "
            "published `assets/routing_fixture.jsonl` is required"
        )
    out_yml = target / "eval" / f"{split}.yaml"
    out_yml.parent.mkdir(parents=True, exist_ok=True)
    if out_yml.exists() and not overwrite:
        raise FileExistsError(
            f"{out_yml} exists; pass overwrite=True (or --overwrite) to replace"
        )

    tasks: list[dict[str, Any]] = []
    skipped: list[str] = []
    for ln_no, line in enumerate(fixture.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            skipped.append(f"line {ln_no}: {e}")
            continue
        q = row.get("user_query") or row.get("q")
        expected = row.get("expected_skill_id")
        if not (q and expected):
            skipped.append(
                f"line {ln_no}: missing user_query/q or expected_skill_id"
            )
            continue
        tasks.append({
            "task_id": f"route_{ln_no:04d}",
            "inputs": {"user_query": q},
            "expected_outputs": {"expected_skill_id": expected},
            "scoring": {"kind": "routing_match"},
            "notes": row.get("notes", ""),
        })

    out_yml.write_text(
        yaml.safe_dump({"tasks": tasks}, sort_keys=False), encoding="utf-8"
    )
    return {
        "source": str(fixture),
        "wrote": str(out_yml),
        "n_tasks": len(tasks),
        "n_skipped": len(skipped),
        "skipped": skipped,
        "next_step": (
            f"python -m factory.rollout next-task {target} "
            f"--split {split} --candidate v000"
        ),
    }


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="factory.rollout",
        description=(
            "Agent-driven rollout-harness adapter. Schema + protocol for "
            "the next-task / record-result / summarize loop, plus the first "
            "real harness: router-fixture replay via build-eval. "
            "See ROLLOUT_HARNESS.md."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    be = sub.add_parser(
        "build-eval",
        help="Build eval/<split>.yaml from a routing fixture (first real harness).",
    )
    be.add_argument("target", type=Path)
    be.add_argument("--from-routing-fixture", action="store_true", required=True,
                    help="Source: best/assets/routing_fixture.jsonl")
    be.add_argument("--split", default="train")
    be.add_argument("--overwrite", action="store_true")

    nt = sub.add_parser("next-task", help="Print the next task to roll out.")
    nt.add_argument("target", type=Path)
    nt.add_argument("--split", default="train")
    nt.add_argument("--epoch", type=int, default=0)
    nt.add_argument("--step", type=int, default=0)
    nt.add_argument("--candidate", required=True)

    rr = sub.add_parser("record-result", help="Validate one rollout JSON.")
    rr.add_argument("target", type=Path)
    rr.add_argument("json_path", type=Path)
    rr.add_argument("--split", default=None,
                    help="If set, auto-judge unset pass/score against eval/<split>.yaml.")

    sm = sub.add_parser("summarize",
                        help="Aggregate per-task JSONs into rollouts.jsonl.")
    sm.add_argument("target", type=Path)
    sm.add_argument("--split", default="train")
    sm.add_argument("--epoch", type=int, default=0)
    sm.add_argument("--step", type=int, default=0)
    sm.add_argument("--candidate", required=True)

    return p


def _main(argv: list[str] | None = None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    if args.cmd == "build-eval":
        if not args.from_routing_fixture:  # argparse already enforces but keep explicit
            p.error("--from-routing-fixture is the only build-eval source for now")
        out = cmd_build_eval_from_routing_fixture(
            args.target, split=args.split, overwrite=args.overwrite,
        )
    elif args.cmd == "next-task":
        out = cmd_next_task(args.target, split=args.split,
                            epoch=args.epoch, step=args.step,
                            candidate=args.candidate)
    elif args.cmd == "record-result":
        out = cmd_record_result(args.target, args.json_path, split=args.split)
    elif args.cmd == "summarize":
        out = cmd_summarize(args.target, split=args.split,
                            epoch=args.epoch, step=args.step,
                            candidate=args.candidate)
    else:  # pragma: no cover
        p.error(f"unknown cmd {args.cmd!r}")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
