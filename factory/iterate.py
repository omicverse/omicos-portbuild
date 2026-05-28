"""Self-review iteration — RUBRIC-driven, no-rollout.

When you don't have a rollout harness for a capability (no real H&E slides,
no benchmark evaluator), you can still iterate on the skill text by:

  1. Scoring v_cur via RUBRIC.md (factory.scorer)
  2. Driving analyst_error / merge / ranking with the rubric's
     `recommended_revisions` as the failure signal (instead of failed trajectories)
  3. Patching → re-scoring v_cand → strict-`>` gate accept/reject

Same patcher, same hash cache, same SLOW_UPDATE protection, same timeline +
EVOLUTION_PLOT. The only difference from `factory.optimizer` is the SCORE
function (rubric weighted score, not benchmark accuracy) and that there are
no rollout JSONLs to record.

This is not SkillOpt-per-paper (no scored rollouts), but it shares all the
algorithmic discipline: bounded edits, validation gate, audit trail, rejected
attempts kept.

Subcommands (parallel to factory.optimizer):

  init <target>                      Initialize state from best/ (or
                                     candidates/v_latest/) as v_cur.
  next-step <target>                 Print next agent-facing instruction.
  record-score <target> <json>       Record the rubric scoring JSON the agent
                                     just produced. Computes weighted_score
                                     and runs the strict-`>` gate.
  record-proposal <target> <prompt> <json>
                                     Same as optimizer's: analyst_error /
                                     merge_failure / merge_final / ranking.
  apply <target>                     Materialize a new candidate via patcher.
  finalize <target>                  Copy the best candidate into best/, emit
                                     ITERATION_LOG.md + EVOLUTION_PLOT.svg.

State lives in `<target>/_state_iterate.json` so it doesn't collide with the
rollout-mode `_state.json`.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from factory.algorithm1 import skill_hash
from factory import scorer
from factory.patcher import apply_patch, edits_from_optimizer_json
from factory.timeline import append_event

STATE_FILENAME = "_state_iterate.json"


# --------------------------------------------------------------------------- #
# Cursor state                                                                #
# --------------------------------------------------------------------------- #


@dataclass
class IterateCursor:
    capability_id: str
    s_cur_path: str           # relative to target_dir
    s_best_path: str
    score_cur: float
    score_best: float
    iter_n: int = 0
    max_iters: int = 4
    Lt: int = 4
    pass_threshold: int = scorer.PASS_THRESHOLD
    target_score: float = 95.0
    factory_version: str = ""
    cache: dict[str, float] = field(default_factory=dict)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    phase: str = "idle"
    pending: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, target: Path) -> "IterateCursor":
        p = target / STATE_FILENAME
        if not p.exists():
            raise FileNotFoundError(f"no iterate state at {p}; run `init` first")
        data = json.loads(p.read_text(encoding="utf-8"))
        from factory import __version__ as _fv
        stamped = data.get("factory_version") or ""
        if stamped and stamped != _fv:
            import warnings as _w
            _w.warn(
                f"iterate state at {p} was written by factory {stamped!r}; "
                f"current factory is {_fv!r}. Prompt contracts and rubric "
                f"may have drifted; verify before continuing.",
                RuntimeWarning,
            )
        return cls(**data)

    def save(self, target: Path) -> None:
        (target / STATE_FILENAME).write_text(
            json.dumps(dataclasses.asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #


def cmd_init(target: Path, *, max_iters: int = 4, Lt: int = 4,
             target_score: float = 95.0) -> dict[str, Any]:
    # Pick the trainable text for this target's kind.
    #   kind=skill / capability → best/skill/SKILL.md
    #   kind=agent              → best/agent.md
    try:
        from factory.manifest import read_manifest as _rm
        kind = (_rm(target).kind or "capability")
    except Exception:
        kind = "capability"

    if kind == "agent":
        rel_to_best = Path("agent.md")
        rel_to_candidate = Path("agent.md")
    else:
        rel_to_best = Path("skill") / "SKILL.md"
        rel_to_candidate = Path("skill") / "SKILL.md"

    best_path = target / "best" / rel_to_best
    if best_path.exists():
        s0_path = best_path
    else:
        cands = sorted((target / "candidates").glob("v*"))
        if not cands:
            raise FileNotFoundError(
                f"no best/{rel_to_best} or candidates/v*/{rel_to_candidate} "
                f"under {target}; draft first"
            )
        s0_path = cands[-1] / rel_to_candidate

    text = s0_path.read_text(encoding="utf-8")
    h = skill_hash(text)
    from factory import __version__ as _fv
    state = IterateCursor(
        capability_id=target.name,
        s_cur_path=str(s0_path.relative_to(target)),
        s_best_path=str(s0_path.relative_to(target)),
        score_cur=float("nan"),
        score_best=float("nan"),
        iter_n=0,
        max_iters=max_iters,
        Lt=Lt,
        target_score=target_score,
        factory_version=_fv,
        cache={h: float("nan")},
        phase="awaiting_initial_score",
    )
    state.save(target)
    append_event(target, "loop_initialized", epoch=1, step=0, payload={
        "capability_id": state.capability_id,
        "mode": "self_review",
        "total_epochs": max_iters,
        "edit_budget_Lt": Lt,
        "v_init_hash": h,
        "v_init_path": state.s_cur_path,
    })
    return {"phase": state.phase, "instructions": _instructions(state, target)}


# --------------------------------------------------------------------------- #
# next-step                                                                   #
# --------------------------------------------------------------------------- #


def cmd_next_step(target: Path) -> dict[str, Any]:
    state = IterateCursor.load(target)
    return {"phase": state.phase, "instructions": _instructions(state, target)}


# --------------------------------------------------------------------------- #
# record-score                                                                #
# --------------------------------------------------------------------------- #


def cmd_record_score(target: Path, json_path: Path) -> dict[str, Any]:
    state = IterateCursor.load(target)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    dims = payload.get("dimensions") or {}
    weighted = scorer.compute_weighted_score(dims)
    gates = scorer.gate_failures(dims)
    cmf = bool(payload.get("cross_modality_fabrication", False))
    verdict = scorer.verdict_for(weighted, gates, cmf)

    score_cur_before = state.score_cur

    if state.phase == "awaiting_initial_score":
        # First-ever score: just record, do NOT gate.
        state.score_cur = weighted
        state.score_best = weighted
        state.cache[skill_hash((target / state.s_cur_path).read_text(encoding="utf-8"))] = weighted
        state.pending = {
            "score_json_path": str(json_path.relative_to(target)),
            "recommended_revisions": payload.get("recommended_revisions") or [],
            "top_issues": payload.get("top_issues") or [],
            "verdict": verdict,
            "gate_failed_dims": gates,
            "weakest_hard_gate_dim": _weakest_dim(dims, scorer.HARD_GATE_DIMS),
        }
        # Stop only if already at the target score; otherwise iterate.
        # (Matches SkillOpt: pass verdict isn't a reason to skip remaining
        # epochs — gains can still come from edits to non-gate dimensions.)
        if weighted >= state.target_score:
            state.phase = "ready_to_finalize"
        else:
            state.phase = "awaiting_analyst_error"
        state.save(target)
        append_event(target, "candidate_evaluated",
                     epoch=1, step=0,
                     payload={"candidate": _cand_label(state.s_cur_path),
                              "score_cand": weighted,
                              "verdict_self_review": verdict,
                              "gate_failed_dims": gates,
                              "decision": "initial"})
        return {"phase": state.phase, "weighted_score": weighted, "verdict": verdict,
                "instructions": _instructions(state, target)}

    if state.phase == "applied_awaiting_score":
        cand_path = Path(target / state.pending["candidate_skill_path"])
        h = skill_hash(cand_path.read_text(encoding="utf-8"))
        state.cache[h] = weighted
        # Strict-> gate
        decision: Literal["accept_and_promote_best", "accept_only", "reject"]
        if weighted > state.score_cur:
            decision = ("accept_and_promote_best"
                        if weighted > state.score_best else "accept_only")
        else:
            decision = "reject"
        state.pending["score_cand"] = weighted
        state.pending["decision"] = decision
        state.pending["recommended_revisions"] = payload.get("recommended_revisions") or []
        state.pending["top_issues"] = payload.get("top_issues") or []
        state.pending["weakest_hard_gate_dim"] = _weakest_dim(dims, scorer.HARD_GATE_DIMS)

        append_event(target, "candidate_evaluated",
                     epoch=1, step=state.iter_n,
                     payload={
                         "candidate": _cand_label(state.pending["candidate_skill_path"]),
                         "score_cand": weighted,
                         "score_cur_at_decision": score_cur_before,
                         "score_best_at_decision": state.score_best,
                         "decision": decision,
                         "verdict_self_review": verdict,
                     })

        if decision in ("accept_only", "accept_and_promote_best"):
            state.s_cur_path = state.pending["candidate_skill_path"]
            state.score_cur = weighted
            if decision == "accept_and_promote_best":
                state.s_best_path = state.pending["candidate_skill_path"]
                state.score_best = weighted
        else:
            state.rejected.append({
                "iter": state.iter_n,
                "candidate": state.pending["candidate_skill_path"],
                "score_drop": float(score_cur_before - weighted),
            })

        state.iter_n += 1
        # End-of-loop decisions: stop on (max_iters reached) OR (we hit
        # target_score with an accepted candidate). Pass verdict is no
        # longer a stop condition — we keep iterating until target_score
        # to squeeze gains from non-hard-gate dimensions.
        hit_target = (decision != "reject" and weighted >= state.target_score)
        if state.iter_n >= state.max_iters or hit_target:
            state.phase = "ready_to_finalize"
            state.pending = {}
        else:
            state.phase = "awaiting_analyst_error"
        state.save(target)
        return {"phase": state.phase, "decision": decision,
                "weighted_score": weighted, "verdict": verdict,
                "instructions": _instructions(state, target)}

    raise RuntimeError(f"record-score called in unexpected phase {state.phase!r}")


# --------------------------------------------------------------------------- #
# record-proposal                                                             #
# --------------------------------------------------------------------------- #


_ALLOWED_PROPOSAL: dict[str, str] = {
    "awaiting_analyst_error": "analyst_error",
    "awaiting_merge_final":   "merge_final",
    "awaiting_ranking":       "ranking",
}

_NEXT_PHASE: dict[str, str] = {
    "analyst_error": "awaiting_merge_final",
    "merge_final":   "awaiting_ranking",
    "ranking":       "ready_to_apply",
}


def cmd_record_proposal(target: Path, prompt: str, json_path: Path) -> dict[str, Any]:
    state = IterateCursor.load(target)
    expected = _ALLOWED_PROPOSAL.get(state.phase)
    if expected is None:
        raise RuntimeError(f"record-proposal called in phase {state.phase!r}")
    if prompt != expected:
        raise ValueError(
            f"expected prompt {expected!r} in phase {state.phase!r}, got {prompt!r}"
        )
    if not json_path.exists():
        raise FileNotFoundError(json_path)
    rel = str(json_path.relative_to(target))

    summary: dict[str, Any] = {"prompt": prompt, "json_path": rel}
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if prompt == "analyst_error":
            edits = data.get("patch", {}).get("edits", []) or []
            summary["n_edits_proposed"] = len(edits)
            summary["failure_types"] = [
                fs.get("failure_type") for fs in (data.get("failure_summary") or [])
            ]
        elif prompt == "merge_final":
            summary["n_edits_merged"] = len(data.get("edits") or [])
        elif prompt == "ranking":
            summary["n_selected"] = len(data.get("selected_indices") or [])
    except Exception:
        pass
    append_event(target, "proposal_recorded", epoch=1, step=state.iter_n,
                 payload=summary)

    if prompt == "analyst_error":
        state.pending["analyst_error_json"] = rel
    elif prompt == "merge_final":
        state.pending["merge_final_json"] = rel
    elif prompt == "ranking":
        state.pending["ranking_json"] = rel
    state.phase = _NEXT_PHASE[prompt]
    state.save(target)
    return {"phase": state.phase, "instructions": _instructions(state, target)}


# --------------------------------------------------------------------------- #
# apply                                                                       #
# --------------------------------------------------------------------------- #


def cmd_apply(target: Path) -> dict[str, Any]:
    state = IterateCursor.load(target)
    if state.phase != "ready_to_apply":
        raise RuntimeError(f"apply called in phase {state.phase!r}")

    final_path = target / state.pending["merge_final_json"]
    ranking_path = target / state.pending["ranking_json"]
    final = json.loads(final_path.read_text(encoding="utf-8"))
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))

    edits_pool = edits_from_optimizer_json(final)
    selected = [edits_pool[i]
                for i in (ranking.get("selected_indices") or [])
                if 0 <= i < len(edits_pool)]

    s_cur_text = (target / state.s_cur_path).read_text(encoding="utf-8")
    new_text, report = apply_patch(s_cur_text, selected)

    new_v = _next_candidate_idx(target)
    new_dir = target / "candidates" / f"v{new_v:03d}"

    # Detect target shape from s_cur_path. If it ends with agent.md,
    # we're iterating an agent-only target.
    is_agent_iter = Path(state.s_cur_path).name == "agent.md"

    if is_agent_iter:
        new_dir.mkdir(parents=True, exist_ok=True)
        src_cand_dir = (target / state.s_cur_path).parent
        src_routing = src_cand_dir / "routing_metadata.yaml"
        if src_routing.exists():
            shutil.copy2(src_routing, new_dir / "routing_metadata.yaml")
        cand_path = new_dir / "agent.md"
        cand_path.write_text(new_text, encoding="utf-8")
    else:
        (new_dir / "skill" / "references").mkdir(parents=True, exist_ok=True)
        src_skill_dir = (target / state.s_cur_path).parent
        for sub in ("references", "assets", "scripts"):
            src = src_skill_dir / sub
            if src.is_dir():
                shutil.copytree(src, new_dir / "skill" / sub, dirs_exist_ok=True)
        src_agent = src_skill_dir.parent / "agent.md"
        if src_agent.exists():
            shutil.copy2(src_agent, new_dir / "agent.md")
        cand_path = new_dir / "skill" / "SKILL.md"
        cand_path.write_text(new_text, encoding="utf-8")
    report.write(new_dir / "edit_apply_report.json")

    state.pending["candidate_skill_path"] = str(cand_path.relative_to(target))

    cache_hit = skill_hash(new_text) in state.cache
    state.pending["cache_hit"] = cache_hit
    if cache_hit:
        state.pending["cached_score"] = state.cache[skill_hash(new_text)]
    state.phase = "applied_awaiting_score"
    state.save(target)

    append_event(target, "candidate_applied", epoch=1, step=state.iter_n,
                 payload={
                     "candidate": new_dir.name,
                     "parent": Path(state.s_cur_path).parent.parent.name,
                     "n_applied": report.n_applied,
                     "n_skipped": report.n_skipped,
                     "Lt_eff": state.Lt,
                 })
    return {"phase": state.phase, "candidate_dir": str(new_dir.relative_to(target)),
            "n_applied": report.n_applied, "n_skipped": report.n_skipped,
            "instructions": _instructions(state, target)}


# --------------------------------------------------------------------------- #
# finalize                                                                    #
# --------------------------------------------------------------------------- #


def cmd_finalize(target: Path, *, republish: bool = True) -> dict[str, Any]:
    state = IterateCursor.load(target)
    best = target / "best"
    if best.exists():
        shutil.rmtree(best)
    best.mkdir()
    is_agent_iter = Path(state.s_best_path).name == "agent.md"
    if is_agent_iter:
        # Agent-only target: best/ has agent.md (+ routing) only.
        cand_dir = (target / state.s_best_path).parent
        shutil.copy2(target / state.s_best_path, best / "agent.md")
        if (cand_dir / "routing_metadata.yaml").exists():
            shutil.copy2(cand_dir / "routing_metadata.yaml",
                         best / "routing_metadata.yaml")
    else:
        best_skill_md = target / state.s_best_path
        best_skill_dir = best_skill_md.parent       # candidates/v00n/skill/
        cand_dir = best_skill_dir.parent            # candidates/v00n/

        shutil.copytree(best_skill_dir, best / "skill")
        if (cand_dir / "agent.md").exists():
            shutil.copy2(cand_dir / "agent.md", best / "agent.md")
        if (cand_dir / "routing_metadata.yaml").exists():
            shutil.copy2(cand_dir / "routing_metadata.yaml",
                         best / "routing_metadata.yaml")

    append_event(target, "loop_finalized", epoch=1, step=state.iter_n,
                 payload={"best": Path(state.s_best_path).parent.parent.name,
                          "score_best": state.score_best,
                          "n_iters": state.iter_n})

    from factory.plot_evolution import write_svg
    plot_path = write_svg(target)
    state.phase = "done"
    state.save(target)

    # Auto-republish so published/ reflects the new best.
    pub_info = None
    if republish:
        try:
            from factory import publisher
            pub_info = publisher.run(target_dir=target)
        except Exception as e:
            pub_info = {"published": False, "error": f"{type(e).__name__}: {e}"}

    return {"phase": state.phase, "best_dir": str(best),
            "evolution_plot": str(plot_path),
            "iteration_log": str(target / "ITERATION_LOG.md"),
            "republished": bool(pub_info and pub_info.get("published")),
            "publish_info": pub_info}


def cmd_continue(target: Path, *, max_iters: int = 4, Lt: int = 4,
                 target_score: float = 95.0) -> dict[str, Any]:
    """Re-anchor on current best/ and start a fresh iterate cycle.

    Use when v_n finalized but score is below target_score and you want
    another iter cycle without manually deleting `_state_iterate.json`.
    Carries forward the cache so previously-scored versions stay cached.
    """
    state_path = target / STATE_FILENAME
    prev_cache: dict[str, float] = {}
    if state_path.exists():
        try:
            prev = json.loads(state_path.read_text(encoding="utf-8"))
            prev_cache = prev.get("cache", {}) or {}
        except Exception:
            pass
        state_path.unlink()
    out = cmd_init(target, max_iters=max_iters, Lt=Lt, target_score=target_score)
    # Restore the score cache so duplicate scoring of unchanged best is free.
    state = IterateCursor.load(target)
    for k, v in prev_cache.items():
        state.cache.setdefault(k, v)
    state.save(target)
    return out


def cmd_batch(root: Path, *, n_iters: int = 1, target_score: float = 95.0,
              Lt: int = 4) -> dict[str, Any]:
    """Run iterate.continue across every skill + agent target in a package root.

    Each target advances by n_iters (or until target_score). This is
    convenience scaffolding — the agent still drives the score/proposal
    cycle per iter (via record-score / record-proposal); batch_cmd just
    initializes each target's state in the right place.
    """
    results: list[dict[str, Any]] = []
    targets = []
    if (root / "skills").is_dir():
        targets.extend(sorted((root / "skills").iterdir()))
    if (root / "agents").is_dir():
        targets.extend(sorted((root / "agents").iterdir()))
    for t in targets:
        try:
            info = cmd_continue(t, max_iters=n_iters, Lt=Lt,
                                target_score=target_score)
            results.append({"target": t.name, "phase": info["phase"]})
        except Exception as e:
            results.append({"target": t.name, "error": f"{type(e).__name__}: {e}"})
    return {"root": str(root), "n_iters": n_iters,
            "target_score": target_score, "results": results}


# --------------------------------------------------------------------------- #
# Instructions                                                                #
# --------------------------------------------------------------------------- #


def _instructions(state: IterateCursor, target: Path) -> dict[str, Any]:
    pc = "factory/prompt_contracts"
    if state.phase == "awaiting_initial_score":
        return {
            "do": "Score the current skill against the 10-dim RUBRIC. Read SKILL.md + agent.md + RUBRIC.md and write a JSON per prompt_contracts/rubric_score.md.",
            "read": [state.s_cur_path, _agent_for(state.s_cur_path), "RUBRIC.md",
                     f"{pc}/rubric_score.md"],
            "prompt": f"{pc}/rubric_score.md",
            "write": _score_path(state, target),
            "then": f"python -m factory.iterate record-score {target.name} <json>",
        }
    if state.phase == "awaiting_analyst_error":
        return {
            "do": ("Apply analyst_error using the latest rubric score's "
                   "`recommended_revisions` and `top_issues` as the failure signal. "
                   f"Edit budget L_t={state.Lt}. The weakest hard gate is "
                   f"{state.pending.get('weakest_hard_gate_dim', '<unknown>')}; "
                   "aim edits at that dimension first."),
            "read": [state.pending.get("score_json_path") or _score_path(state, target),
                     state.s_cur_path, f"{pc}/analyst_error.md"],
            "prompt": f"{pc}/analyst_error.md",
            "write": _analyst_path(state, target),
            "then": f"python -m factory.iterate record-proposal {target.name} analyst_error <json>",
        }
    if state.phase == "awaiting_merge_final":
        return {
            "do": "Apply merge_final to consolidate the analyst_error proposals (we skip analyst_success/merge_success in self-review mode).",
            "read": [state.pending.get("analyst_error_json"), f"{pc}/merge_final.md"],
            "prompt": f"{pc}/merge_final.md",
            "write": _step_artifact(state, "merge_final.json"),
            "then": f"python -m factory.iterate record-proposal {target.name} merge_final <json>",
        }
    if state.phase == "awaiting_ranking":
        return {
            "do": f"Apply ranking with budget L_t={state.Lt}.",
            "read": [state.pending.get("merge_final_json"), f"{pc}/ranking.md", state.s_cur_path],
            "prompt": f"{pc}/ranking.md",
            "write": _step_artifact(state, "ranking.json"),
            "then": f"python -m factory.iterate record-proposal {target.name} ranking <json>",
        }
    if state.phase == "ready_to_apply":
        return {
            "do": "Materialize the candidate.",
            "read": [], "prompt": None, "write": None,
            "then": f"python -m factory.iterate apply {target.name}",
        }
    if state.phase == "applied_awaiting_score":
        hint = ""
        if state.pending.get("cache_hit"):
            hint = (f"  (cache hit: candidate already scored at "
                    f"{state.pending.get('cached_score')}; you may reuse that directly)")
        return {
            "do": f"Score the NEW candidate skill against the rubric. Strict-`>` gate against score_cur={state.score_cur:.2f}." + hint,
            "read": [state.pending.get("candidate_skill_path"), "RUBRIC.md", f"{pc}/rubric_score.md"],
            "prompt": f"{pc}/rubric_score.md",
            "write": _score_path(state, target),
            "then": f"python -m factory.iterate record-score {target.name} <json>",
        }
    if state.phase == "ready_to_finalize":
        return {
            "do": "Self-review loop done. Copy best/.",
            "read": [], "prompt": None, "write": None,
            "then": f"python -m factory.iterate finalize {target.name}",
        }
    if state.phase == "done":
        return {"do": "Iteration complete.", "read": [], "prompt": None, "write": None,
                "then": f"omicos-portbuild publish {target}"}
    return {"do": f"unknown phase {state.phase!r}", "read": [], "prompt": None,
            "write": None, "then": None}


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _next_candidate_idx(target: Path) -> int:
    cands = sorted((target / "candidates").glob("v*"))
    if not cands:
        return 0
    return max(int(d.name.lstrip("v")) for d in cands if d.name[1:].isdigit()) + 1


def _agent_for(skill_path_rel: str) -> str:
    return str(Path(skill_path_rel).parent.parent / "agent.md")


def _cand_label(skill_path_rel: str) -> str:
    return Path(skill_path_rel).parent.parent.name


def _step_artifact(state: IterateCursor, name: str) -> str:
    return f"_trajectory/iter{state.iter_n:02d}/{name}"


def _score_path(state: IterateCursor, target: Path) -> str:
    return f"_trajectory/score/iter{state.iter_n:02d}_rubric_score.json"


def _analyst_path(state: IterateCursor, target: Path) -> str:
    return f"_trajectory/iter{state.iter_n:02d}/analyst_error.json"


def _weakest_dim(dims: dict[str, dict], pool: tuple[str, ...]) -> Optional[str]:
    weakest = None
    weakest_score = 5.1
    for dim in pool:
        d = dims.get(dim) or {}
        try:
            s = float(d.get("score", 5))
        except (TypeError, ValueError):
            s = 5.0
        if s < weakest_score:
            weakest = dim
            weakest_score = s
    return weakest


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory.iterate",
                                description="Self-review iteration loop (RUBRIC-driven, no rollouts).")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init")
    pi.add_argument("target_dir")
    pi.add_argument("--max-iters", type=int, default=4)
    pi.add_argument("--lt", type=int, default=4, help="textual learning rate (edits per iter)")
    pi.add_argument("--target-score", type=float, default=95.0,
                    help="stop iterating once weighted rubric score >= this value (default 95)")

    pn = sub.add_parser("next-step")
    pn.add_argument("target_dir")

    prs = sub.add_parser("record-score")
    prs.add_argument("target_dir")
    prs.add_argument("json_path")

    prp = sub.add_parser("record-proposal")
    prp.add_argument("target_dir")
    prp.add_argument("prompt", choices=("analyst_error", "merge_final", "ranking"))
    prp.add_argument("json_path")

    pa = sub.add_parser("apply")
    pa.add_argument("target_dir")

    pf = sub.add_parser("finalize")
    pf.add_argument("target_dir")
    pf.add_argument("--no-republish", action="store_true",
                    help="skip auto-republish after finalize")

    pc = sub.add_parser("continue",
                        help="re-anchor on best/ and start another iter cycle")
    pc.add_argument("target_dir")
    pc.add_argument("--max-iters", type=int, default=4)
    pc.add_argument("--lt", type=int, default=4)
    pc.add_argument("--target-score", type=float, default=95.0)

    pb = sub.add_parser("batch", help="run iterate.continue across all targets in a root")
    pb.add_argument("root")
    pb.add_argument("--n-iters", type=int, default=1)
    pb.add_argument("--target-score", type=float, default=95.0)
    pb.add_argument("--lt", type=int, default=4)

    return p


def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    target = Path(args.target_dir).resolve()
    try:
        if args.cmd == "init":
            out = cmd_init(target, max_iters=args.max_iters, Lt=args.lt,
                           target_score=args.target_score)
        elif args.cmd == "next-step":
            out = cmd_next_step(target)
        elif args.cmd == "record-score":
            out = cmd_record_score(target, Path(args.json_path).resolve())
        elif args.cmd == "record-proposal":
            out = cmd_record_proposal(target, args.prompt, Path(args.json_path).resolve())
        elif args.cmd == "apply":
            out = cmd_apply(target)
        elif args.cmd == "finalize":
            out = cmd_finalize(target, republish=not args.no_republish)
        elif args.cmd == "continue":
            out = cmd_continue(target, max_iters=args.max_iters, Lt=args.lt,
                               target_score=args.target_score)
        elif args.cmd == "batch":
            out = cmd_batch(target, n_iters=args.n_iters,
                            target_score=args.target_score, Lt=args.lt)
        else:
            return 2
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"[iterate] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
