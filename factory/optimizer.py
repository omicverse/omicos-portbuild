"""Phase 5 — SkillOpt loop, driven by an agent in conversation (not by an SDK).

This module is intentionally LLM-free. The pattern mirrors omicverse-rebuildr's
`engine/loop.py`: the optimizer here is a state-machine cursor + a step-by-step
CLI that an interactive agent (Claude Code, Codex, etc.) drives by:

  1. Running `python -m factory.optimizer next-step targets/<id>` to ask what
     to do next.
  2. Doing that work (read trajectories, apply a prompt_contract, write the
     resulting JSON to the path the cursor named).
  3. Running the corresponding `record-*` subcommand to advance the state
     machine.

The state lives in `targets/<id>/_state.json`. It is the on-disk projection
of `factory.algorithm1.OptimizerState`. No model call ever leaves this
process; the agent at the keyboard / in the conversation is the optimizer.

Subcommands:

  init           Initialize state from candidates/v000/.
  next-step      Print the next action the agent should perform.
  record-rollouts <split> <jsonl>
                 Record that the agent has just produced rollout traces.
  record-proposal <prompt> <json>
                 Record an optimizer-prompt JSON response (analyst_* / merge_* / ranking).
  apply          Read the latest ranking JSON, run patcher, write a new candidate.
  record-eval <score>
                 Record the candidate's selection-split score; gate accept/reject.
  advance-epoch  End-of-epoch slow_update + meta_skill bookkeeping.
  finalize       After all epochs: copy the best candidate to best/, run scorer.

This file also still exposes `run()` for the cli.py orchestrator, but `run()`
now just calls `init` and prints the agent-facing instructions; it does not
silently drive the loop.
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

from factory.algorithm1 import OptimizerConfig, OptimizerState, skill_hash
from factory.manifest import read_manifest
from factory.patcher import (
    apply_patch,
    edits_from_optimizer_json,
)
from factory.timeline import append_event

STATE_FILENAME = "_state.json"


# --------------------------------------------------------------------------- #
# Disk-projected state                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class CursorState:
    """On-disk projection of OptimizerState + phase cursor.

    `phase` walks through the SkillOpt step skeleton:

      idle → awaiting_rollouts → awaiting_analyst_error
           → awaiting_analyst_success → awaiting_merge_failure
           → awaiting_merge_success → awaiting_merge_final
           → awaiting_ranking → ready_to_apply
           → applied_awaiting_eval → step_done
           → (next step or end_of_epoch → awaiting_slow_update → ...)
    """

    capability_id: str
    epoch: int
    step: int
    total_epochs: int
    Lt_eff: int

    s_cur_path: str         # relative to target_dir
    s_best_path: str
    score_cur: float
    score_best: float

    cache: dict[str, float] = field(default_factory=dict)
    rejected_buffer: list[dict[str, Any]] = field(default_factory=list)
    failure_pattern_buffer: list[dict[str, Any]] = field(default_factory=list)
    m_meta_path: Optional[str] = None  # optimizer-side; never copied to best/

    phase: str = "idle"
    pending: dict[str, Any] = field(default_factory=dict)

    # ---- io ---------------------------------------------------------- #

    @classmethod
    def load(cls, target_dir: Path) -> "CursorState":
        p = target_dir / STATE_FILENAME
        if not p.exists():
            raise FileNotFoundError(f"no optimizer state at {p}; run `init` first")
        return cls(**json.loads(p.read_text(encoding="utf-8")))

    def save(self, target_dir: Path) -> None:
        p = target_dir / STATE_FILENAME
        p.write_text(
            json.dumps(dataclasses.asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# --------------------------------------------------------------------------- #
# CLI-facing operations                                                       #
# --------------------------------------------------------------------------- #


def cmd_init(target_dir: Path) -> dict[str, Any]:
    manifest = read_manifest(target_dir)
    config = OptimizerConfig(
        epochs=manifest.optimizer.epochs,
        rollout_batch_size_B=manifest.optimizer.rollout_batch_size_B,
        reflection_minibatch_size_Bm=manifest.optimizer.minibatch_size_Bm,
        edit_budget_Lt=manifest.optimizer.edit_budget_Lt,
        lr_schedule=manifest.optimizer.lr_schedule,  # type: ignore[arg-type]
        lr_floor=manifest.optimizer.lr_floor,
        slow_update_samples=manifest.optimizer.slow_update_samples,
        meta_skill_enabled=manifest.optimizer.meta_skill_enabled,
    )
    v000 = target_dir / "candidates" / "v000"
    if not (v000 / "skill" / "SKILL.md").exists():
        raise FileNotFoundError(f"missing {v000 / 'skill' / 'SKILL.md'}; run draft first")

    skill_text = (v000 / "skill" / "SKILL.md").read_text(encoding="utf-8")
    h = skill_hash(skill_text)

    state = CursorState(
        capability_id=manifest.capability_id,
        epoch=1,
        step=0,
        total_epochs=config.epochs,
        Lt_eff=config.lr_for_epoch(1),
        s_cur_path=str((v000 / "skill" / "SKILL.md").relative_to(target_dir)),
        s_best_path=str((v000 / "skill" / "SKILL.md").relative_to(target_dir)),
        # We do NOT initialize score because the agent must evaluate v000 on
        # the selection split first. The first call to next-step will instruct it.
        score_cur=float("nan"),
        score_best=float("nan"),
        cache={h: float("nan")},
        phase="awaiting_initial_eval",
        pending={"target_skill_path": state_s_path(v000, "skill/SKILL.md")},
    )
    state.save(target_dir)
    append_event(target_dir, "loop_initialized", epoch=1, step=0, payload={
        "capability_id": manifest.capability_id,
        "total_epochs": config.epochs,
        "edit_budget_Lt": config.edit_budget_Lt,
        "lr_schedule": config.lr_schedule,
        "v000_hash": h,
    })
    return {"phase": state.phase, "instructions": _instructions(state, target_dir)}


def cmd_next_step(target_dir: Path) -> dict[str, Any]:
    state = CursorState.load(target_dir)
    return {"phase": state.phase, "instructions": _instructions(state, target_dir)}


def cmd_record_eval(target_dir: Path, score: float) -> dict[str, Any]:
    state = CursorState.load(target_dir)

    if state.phase == "awaiting_initial_eval":
        # First evaluation of v000.
        state.score_cur = score
        state.score_best = score
        state.cache[skill_hash(Path(target_dir / state.s_cur_path).read_text(encoding="utf-8"))] = score
        state.phase = "awaiting_rollouts"
        state.pending = {"split": "train", "skill_path": state.s_cur_path,
                         "out_jsonl": _step_artifact_path(state, "rollouts.jsonl")}
        state.save(target_dir)
        append_event(target_dir, "candidate_evaluated",
                     epoch=state.epoch, step=state.step,
                     payload={"candidate": "v000", "score_cand": score,
                              "score_cur_at_decision": float("nan"),
                              "decision": "initial"})
        return {"phase": state.phase, "instructions": _instructions(state, target_dir)}

    if state.phase == "applied_awaiting_eval":
        cand_skill_path = Path(target_dir / state.pending["candidate_skill_path"])
        cand_text = cand_skill_path.read_text(encoding="utf-8")
        cand_hash = skill_hash(cand_text)
        state.cache[cand_hash] = score
        score_cur_before = state.score_cur
        decision = _gate(state, score)
        state.pending["decision"] = decision
        state.pending["score_cand"] = score
        # Record the decision event BEFORE we mutate score_cur/score_best,
        # so the timeline preserves what was compared.
        append_event(target_dir, "candidate_evaluated",
                     epoch=state.epoch, step=state.step,
                     payload={
                         "candidate": Path(state.pending["candidate_skill_path"]).parent.parent.name,
                         "score_cand": score,
                         "score_cur_at_decision": score_cur_before,
                         "score_best_at_decision": state.score_best,
                         "decision": decision,
                     })
        if decision in ("accept_only", "accept_and_promote_best"):
            state.s_cur_path = state.pending["candidate_skill_path"]
            state.score_cur = score
            if decision == "accept_and_promote_best":
                state.s_best_path = state.pending["candidate_skill_path"]
                state.score_best = score
        else:
            # Algorithm 1 line 26: add rejected edits + failure patterns to B.
            state.rejected_buffer.append({
                "epoch": state.epoch,
                "step": state.step,
                "edits_json_path": state.pending.get("merged_edits_path"),
                "score_drop": float(state.score_cur - score),
            })
        # advance step
        state.step += 1
        max_steps_per_epoch = _steps_per_epoch(target_dir)
        if state.step >= max_steps_per_epoch:
            # End of epoch
            if state.epoch >= state.total_epochs:
                state.phase = "ready_to_finalize"
                state.pending = {}
            elif state.epoch >= 1:
                state.phase = "awaiting_slow_update"
                state.pending = {
                    "previous_epoch_skill": state.s_cur_path,
                    "current_epoch_skill": state.s_cur_path,
                    "out_json": _epoch_artifact_path(state, "slow_update.json"),
                }
            else:
                state.epoch += 1
                state.step = 0
                state.Lt_eff = OptimizerConfig(
                    epochs=state.total_epochs,
                    edit_budget_Lt=4,
                ).lr_for_epoch(state.epoch)
                state.rejected_buffer.clear()
                state.failure_pattern_buffer.clear()
                state.phase = "awaiting_rollouts"
                state.pending = {"split": "train",
                                 "skill_path": state.s_cur_path,
                                 "out_jsonl": _step_artifact_path(state, "rollouts.jsonl")}
        else:
            state.phase = "awaiting_rollouts"
            state.pending = {"split": "train",
                             "skill_path": state.s_cur_path,
                             "out_jsonl": _step_artifact_path(state, "rollouts.jsonl")}
        state.save(target_dir)
        return {
            "phase": state.phase,
            "decision": decision,
            "instructions": _instructions(state, target_dir),
        }

    raise RuntimeError(f"record-eval called in unexpected phase {state.phase!r}")


def cmd_record_rollouts(target_dir: Path, jsonl_path: Path) -> dict[str, Any]:
    state = CursorState.load(target_dir)
    if state.phase != "awaiting_rollouts":
        raise RuntimeError(f"record-rollouts called in phase {state.phase!r}")
    if not jsonl_path.exists():
        raise FileNotFoundError(jsonl_path)
    # Count failures vs successes (best effort — JSONL "score" or "success" field).
    n_failures = n_successes = 0
    try:
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            ok = d.get("success")
            if ok is None:
                sc = d.get("score", 0.0)
                ok = bool(sc > 0.5)
            if ok:
                n_successes += 1
            else:
                n_failures += 1
    except Exception:
        pass
    state.pending = {
        "rollouts_jsonl": str(jsonl_path.relative_to(target_dir)),
        "skill_at_rollout": state.s_cur_path,
        "n_failures": n_failures,
        "n_successes": n_successes,
    }
    state.phase = "awaiting_analyst_error"
    state.save(target_dir)
    append_event(target_dir, "rollouts_recorded",
                 epoch=state.epoch, step=state.step,
                 payload={
                     "jsonl_path": str(jsonl_path.relative_to(target_dir)),
                     "n_failures": n_failures,
                     "n_successes": n_successes,
                     "skill_at_rollout": state.s_cur_path,
                 })
    return {"phase": state.phase, "instructions": _instructions(state, target_dir)}


def cmd_record_proposal(
    target_dir: Path,
    prompt: str,
    json_path: Path,
) -> dict[str, Any]:
    """Record one optimizer-prompt response and advance the phase."""
    state = CursorState.load(target_dir)
    if not json_path.exists():
        raise FileNotFoundError(json_path)

    expected_prompt = {
        "awaiting_analyst_error": "analyst_error",
        "awaiting_analyst_success": "analyst_success",
        "awaiting_merge_failure": "merge_failure",
        "awaiting_merge_success": "merge_success",
        "awaiting_merge_final": "merge_final",
        "awaiting_ranking": "ranking",
        "awaiting_slow_update": "slow_update",
        "awaiting_meta_skill": "meta_skill",
    }.get(state.phase)
    if expected_prompt is None:
        raise RuntimeError(f"record-proposal called in phase {state.phase!r}")
    if prompt != expected_prompt:
        raise ValueError(
            f"expected prompt {expected_prompt!r} in phase {state.phase!r}, "
            f"got {prompt!r}"
        )

    rel = str(json_path.relative_to(target_dir))
    # Record the proposal as a timeline event (carries enough info for ITERATION_LOG).
    proposal_summary: dict[str, Any] = {"prompt": prompt, "json_path": rel}
    try:
        proposal_data = json.loads(json_path.read_text(encoding="utf-8"))
        if prompt in ("analyst_error", "analyst_success"):
            edits = proposal_data.get("patch", {}).get("edits", [])
            proposal_summary["n_edits_proposed"] = len(edits)
            if prompt == "analyst_error":
                proposal_summary["failure_types"] = [
                    fs.get("failure_type") for fs in proposal_data.get("failure_summary", [])
                ]
        elif prompt in ("merge_failure", "merge_success", "merge_final"):
            proposal_summary["n_edits_merged"] = len(proposal_data.get("edits", []))
        elif prompt == "ranking":
            proposal_summary["n_selected"] = len(proposal_data.get("selected_indices", []))
    except Exception:
        pass
    append_event(target_dir, "proposal_recorded",
                 epoch=state.epoch, step=state.step,
                 payload=proposal_summary)

    if prompt == "analyst_error":
        state.pending["failure_proposal_json"] = rel
        # Also capture observed failure patterns into the epoch buffer.
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            for fp in data.get("failure_summary", []) or []:
                state.failure_pattern_buffer.append(fp)
        except Exception:
            pass
        state.phase = "awaiting_analyst_success"
    elif prompt == "analyst_success":
        state.pending["success_proposal_json"] = rel
        state.phase = "awaiting_merge_failure"
    elif prompt == "merge_failure":
        state.pending["merged_failure_json"] = rel
        state.phase = "awaiting_merge_success"
    elif prompt == "merge_success":
        state.pending["merged_success_json"] = rel
        state.phase = "awaiting_merge_final"
    elif prompt == "merge_final":
        state.pending["merged_final_json"] = rel
        state.phase = "awaiting_ranking"
    elif prompt == "ranking":
        state.pending["ranking_json"] = rel
        state.phase = "ready_to_apply"
    elif prompt == "slow_update":
        # Apply the slow_update content into the SLOW_UPDATE region, then
        # gate the resulting skill through the selection split.
        out = _apply_slow_update(target_dir, state, json_path)
        state.pending["candidate_skill_path"] = out["candidate_skill_path"]
        state.phase = "applied_awaiting_eval"
        append_event(target_dir, "slow_update_applied",
                     epoch=state.epoch, step=state.step,
                     payload={"candidate": Path(out["candidate_skill_path"]).parent.parent.name})
    elif prompt == "meta_skill":
        # Optimizer-side only. Save and advance to next epoch (or finalize).
        meta_text = json.loads(json_path.read_text(encoding="utf-8")).get(
            "meta_skill_content", ""
        )
        meta_dst = target_dir / "_meta_skill" / f"epoch_{state.epoch:02d}.md"
        meta_dst.parent.mkdir(parents=True, exist_ok=True)
        meta_dst.write_text(meta_text, encoding="utf-8")
        state.m_meta_path = str(meta_dst.relative_to(target_dir))
        append_event(target_dir, "meta_skill_recorded",
                     epoch=state.epoch, step=state.step,
                     payload={"path": state.m_meta_path})
        if state.epoch >= state.total_epochs:
            state.phase = "ready_to_finalize"
            state.pending = {}
        else:
            append_event(target_dir, "epoch_boundary",
                         epoch=state.epoch, step=state.step,
                         payload={"score_best_so_far": state.score_best})
            state.epoch += 1
            state.step = 0
            state.Lt_eff = OptimizerConfig(
                epochs=state.total_epochs, edit_budget_Lt=4,
            ).lr_for_epoch(state.epoch)
            state.rejected_buffer.clear()
            state.failure_pattern_buffer.clear()
            state.phase = "awaiting_rollouts"
            state.pending = {"split": "train",
                             "skill_path": state.s_cur_path,
                             "out_jsonl": _step_artifact_path(state, "rollouts.jsonl")}
    state.save(target_dir)
    return {"phase": state.phase, "instructions": _instructions(state, target_dir)}


def cmd_apply(target_dir: Path) -> dict[str, Any]:
    """Use ranking output + final-merge edits to produce a new candidate.

    The agent has already written ranking.json (selected_indices) and the
    final merged edits live in pending.merged_final_json. We pick the top-N
    edits per the ranking, apply them via patcher to s_cur, and write a new
    candidates/v00n/ folder.
    """
    state = CursorState.load(target_dir)
    if state.phase != "ready_to_apply":
        raise RuntimeError(f"apply called in phase {state.phase!r}")

    final_payload = json.loads(
        (target_dir / state.pending["merged_final_json"]).read_text(encoding="utf-8")
    )
    ranking_payload = json.loads(
        (target_dir / state.pending["ranking_json"]).read_text(encoding="utf-8")
    )
    edits_pool = edits_from_optimizer_json(final_payload)
    selected = [
        edits_pool[i] for i in ranking_payload.get("selected_indices", [])
        if 0 <= i < len(edits_pool)
    ]
    if not selected:
        # Nothing selected — phantom step, gate will reject the unchanged skill.
        selected = []

    s_cur_text = (target_dir / state.s_cur_path).read_text(encoding="utf-8")
    new_text, report = apply_patch(s_cur_text, selected)

    new_v = _next_candidate_idx(target_dir)
    new_dir = target_dir / "candidates" / f"v{new_v:03d}"
    (new_dir / "skill" / "references").mkdir(parents=True, exist_ok=True)
    (new_dir / "skill" / "assets").mkdir(exist_ok=True)
    # Copy supporting files (references, assets) from s_cur's skill dir.
    src_skill_dir = (target_dir / state.s_cur_path).parent
    for sub in ("references", "assets", "scripts"):
        src = src_skill_dir / sub
        if src.is_dir():
            shutil.copytree(src, new_dir / "skill" / sub, dirs_exist_ok=True)
    # Carry agent.md and routing_metadata.yaml across (rare to change at this stage).
    src_agent = src_skill_dir.parent / "agent.md"
    if src_agent.exists():
        shutil.copy2(src_agent, new_dir / "agent.md")
    src_rm = src_skill_dir.parent / "routing_metadata.yaml"
    if src_rm.exists():
        shutil.copy2(src_rm, new_dir / "routing_metadata.yaml")
    skill_md_dst = new_dir / "skill" / "SKILL.md"
    skill_md_dst.write_text(new_text, encoding="utf-8")
    report.write(new_dir / "edit_apply_report.json")

    state.pending["candidate_skill_path"] = str(skill_md_dst.relative_to(target_dir))
    state.pending["merged_edits_path"] = state.pending.get("merged_final_json")

    h = skill_hash(new_text)
    if h in state.cache:
        # Cache hit: surface to the agent that no re-eval is needed.
        state.pending["cache_hit"] = True
        state.pending["cached_score"] = state.cache[h]
        state.phase = "applied_awaiting_eval"
    else:
        state.pending["cache_hit"] = False
        state.phase = "applied_awaiting_eval"
    state.save(target_dir)
    append_event(target_dir, "candidate_applied",
                 epoch=state.epoch, step=state.step,
                 payload={
                     "candidate": new_dir.name,
                     "parent": Path(state.s_cur_path).parent.parent.name,
                     "n_applied": report.n_applied,
                     "n_skipped": report.n_skipped,
                     "Lt_eff": state.Lt_eff,
                     "skip_reasons": [
                         {"index": r.index, "reason": r.reason}
                         for r in report.results if r.status == "skipped"
                     ],
                 })
    return {
        "phase": state.phase,
        "n_applied": report.n_applied,
        "n_skipped": report.n_skipped,
        "candidate_dir": str(new_dir.relative_to(target_dir)),
        "instructions": _instructions(state, target_dir),
    }


def cmd_finalize(target_dir: Path) -> dict[str, Any]:
    """Copy the best candidate to best/ and emit ITERATION_LOG.md."""
    state = CursorState.load(target_dir)
    if state.phase != "ready_to_finalize":
        # Allow finalize-from-anywhere as long as we have a defensible best.
        if state.s_best_path is None:
            raise RuntimeError(f"cannot finalize in phase {state.phase!r}")

    best = target_dir / "best"
    if best.exists():
        shutil.rmtree(best)
    best.mkdir()
    best_skill_md = target_dir / state.s_best_path
    best_skill_dir = best_skill_md.parent       # candidates/v00n/skill/
    cand_dir = best_skill_dir.parent            # candidates/v00n/

    shutil.copytree(best_skill_dir, best / "skill")
    if (cand_dir / "agent.md").exists():
        shutil.copy2(cand_dir / "agent.md", best / "agent.md")
    if (cand_dir / "routing_metadata.yaml").exists():
        shutil.copy2(cand_dir / "routing_metadata.yaml", best / "routing_metadata.yaml")

    # Append the finalization event (ITERATION_LOG.md re-renders inside append_event).
    append_event(target_dir, "loop_finalized",
                 epoch=state.epoch, step=state.step,
                 payload={"best": Path(state.s_best_path).parent.parent.name,
                          "score_best": state.score_best})
    # Also generate the evolution SVG plot.
    from factory.plot_evolution import write_svg
    plot_path = write_svg(target_dir)
    state.phase = "done"
    state.save(target_dir)
    return {"phase": state.phase, "best_dir": str(best),
            "iteration_log": str(target_dir / "ITERATION_LOG.md"),
            "evolution_plot": str(plot_path)}


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _gate(state: CursorState, score: float) -> Literal[
    "accept_and_promote_best", "accept_only", "reject"
]:
    if not (score > state.score_cur):  # strict >, ties rejected
        return "reject"
    if score > state.score_best:
        return "accept_and_promote_best"
    return "accept_only"


def _steps_per_epoch(target_dir: Path) -> int:
    """How many SkillOpt steps per epoch. Driven by rollout batch + train size.

    For the agent-driven flow we don't have a fixed schedule; we let the agent
    decide when an epoch is done by calling `advance-epoch`. So we treat this
    as 1 step per epoch by default — the agent does multiple steps explicitly
    if they want denser optimization.
    """
    return 1


def _step_artifact_path(state: CursorState, name: str) -> str:
    return f"_trajectory/e{state.epoch:02d}/s{state.step:02d}/{name}"


def _epoch_artifact_path(state: CursorState, name: str) -> str:
    return f"_trajectory/e{state.epoch:02d}/{name}"


def _next_candidate_idx(target_dir: Path) -> int:
    cands = sorted((target_dir / "candidates").glob("v*"))
    if not cands:
        return 0
    return max(int(d.name.lstrip("v")) for d in cands if d.name[1:].isdigit()) + 1


def _apply_slow_update(target_dir: Path, state: CursorState, json_path: Path) -> dict[str, Any]:
    """Splice slow_update_content into the SLOW_UPDATE region and write a new candidate."""
    from factory.patcher import SLOW_UPDATE_END, SLOW_UPDATE_START

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    new_content = payload.get("slow_update_content", "").strip()

    s_text = (target_dir / state.s_cur_path).read_text(encoding="utf-8")
    s_start = s_text.find(SLOW_UPDATE_START)
    s_end = s_text.find(SLOW_UPDATE_END)
    if s_start == -1 or s_end == -1 or s_end < s_start:
        raise RuntimeError("current skill missing well-formed SLOW_UPDATE block")
    before = s_text[: s_start + len(SLOW_UPDATE_START)]
    after = s_text[s_end:]
    new_skill = before + "\n" + new_content + "\n" + after

    new_v = _next_candidate_idx(target_dir)
    new_dir = target_dir / "candidates" / f"v{new_v:03d}"
    (new_dir / "skill" / "references").mkdir(parents=True, exist_ok=True)
    src_skill_dir = (target_dir / state.s_cur_path).parent
    for sub in ("references", "assets", "scripts"):
        src = src_skill_dir / sub
        if src.is_dir():
            shutil.copytree(src, new_dir / "skill" / sub, dirs_exist_ok=True)
    src_agent = src_skill_dir.parent / "agent.md"
    if src_agent.exists():
        shutil.copy2(src_agent, new_dir / "agent.md")
    cand_skill_md = new_dir / "skill" / "SKILL.md"
    cand_skill_md.write_text(new_skill, encoding="utf-8")

    return {"candidate_skill_path": str(cand_skill_md.relative_to(target_dir))}


def _render_iteration_log(state: CursorState, target_dir: Path) -> str:
    lines = ["# Iteration Log", "",
             f"- Capability: `{state.capability_id}`",
             f"- Epochs completed: {state.epoch}",
             f"- Final score_cur: {state.score_cur}",
             f"- Final score_best: {state.score_best}",
             f"- Best candidate: `{state.s_best_path}`",
             "",
             "## Rejected edits across the run",
             ""]
    for r in state.rejected_buffer:
        lines.append(f"- epoch {r['epoch']} step {r['step']}: score drop {r['score_drop']:.4f}")
    return "\n".join(lines) + "\n"


def state_s_path(v000: Path, sub: str) -> str:
    """Return the path inside the target dir for a v000-relative subpath."""
    return str((v000 / sub).relative_to(v000.parent.parent))


# --------------------------------------------------------------------------- #
# Agent-facing instructions (what to do next)                                 #
# --------------------------------------------------------------------------- #


def _instructions(state: CursorState, target_dir: Path) -> dict[str, Any]:
    """Return a structured description of the next action the agent should take.

    Every action has the same shape:

      {
        "do":      one-line imperative
        "read":    list of files to read
        "prompt":  optional path to prompt_contracts/<name>.md
        "write":   where to write the agent's JSON / output
        "then":    command to invoke afterwards
      }
    """
    pc = "factory/prompt_contracts"
    if state.phase == "awaiting_initial_eval":
        return {
            "do": "Run the target agent on eval/selection.yaml with the v000 skill, "
                  "compute the mean benchmark score, then record it.",
            "read": [state.s_cur_path, "eval/selection.yaml"],
            "prompt": None,
            "write": None,
            "then": "python -m factory.optimizer record-eval <score>",
        }
    if state.phase == "awaiting_rollouts":
        return {
            "do": f"Run the target agent on a rollout batch from eval/{state.pending.get('split')}.yaml "
                  f"with the current skill. Write trajectories as JSONL "
                  f"(one per task, fields: task_id, score, success, trace).",
            "read": [state.pending.get("skill_path"), "eval/train.yaml"],
            "prompt": None,
            "write": state.pending.get("out_jsonl"),
            "then": "python -m factory.optimizer record-rollouts <jsonl_path>",
        }
    if state.phase == "awaiting_analyst_error":
        return {
            "do": "Filter the recorded trajectories down to failures. Group into minibatch(es) "
                  f"of size {OptimizerConfig().reflection_minibatch_size_Bm}. For each minibatch, "
                  "apply the analyst_error prompt and write the JSON. For now, treat the "
                  "first minibatch's response as the proposal.",
            "read": [state.pending.get("rollouts_jsonl"), f"{pc}/analyst_error.md", state.s_cur_path],
            "prompt": f"{pc}/analyst_error.md",
            "write": _step_artifact_path(state, "analyst_error.json"),
            "then": "python -m factory.optimizer record-proposal analyst_error <json_path>",
            "context_hints": state.rejected_buffer[-3:],  # most recent rejections
        }
    if state.phase == "awaiting_analyst_success":
        return {
            "do": "Apply the analyst_success prompt to the SUCCESS minibatch.",
            "read": [state.pending.get("rollouts_jsonl"), f"{pc}/analyst_success.md", state.s_cur_path],
            "prompt": f"{pc}/analyst_success.md",
            "write": _step_artifact_path(state, "analyst_success.json"),
            "then": "python -m factory.optimizer record-proposal analyst_success <json_path>",
        }
    if state.phase == "awaiting_merge_failure":
        return {
            "do": "Apply merge_failure to consolidate failure proposals.",
            "read": [state.pending.get("failure_proposal_json"), f"{pc}/merge_failure.md"],
            "prompt": f"{pc}/merge_failure.md",
            "write": _step_artifact_path(state, "merge_failure.json"),
            "then": "python -m factory.optimizer record-proposal merge_failure <json_path>",
        }
    if state.phase == "awaiting_merge_success":
        return {
            "do": "Apply merge_success to consolidate success proposals.",
            "read": [state.pending.get("success_proposal_json"), f"{pc}/merge_success.md"],
            "prompt": f"{pc}/merge_success.md",
            "write": _step_artifact_path(state, "merge_success.json"),
            "then": "python -m factory.optimizer record-proposal merge_success <json_path>",
        }
    if state.phase == "awaiting_merge_final":
        return {
            "do": "Apply merge_final (failure-prioritized).",
            "read": [
                state.pending.get("merged_failure_json"),
                state.pending.get("merged_success_json"),
                f"{pc}/merge_final.md",
            ],
            "prompt": f"{pc}/merge_final.md",
            "write": _step_artifact_path(state, "merge_final.json"),
            "then": "python -m factory.optimizer record-proposal merge_final <json_path>",
        }
    if state.phase == "awaiting_ranking":
        return {
            "do": f"Apply ranking to the merged edits with budget L_t={state.Lt_eff}.",
            "read": [state.pending.get("merged_final_json"), f"{pc}/ranking.md", state.s_cur_path],
            "prompt": f"{pc}/ranking.md",
            "write": _step_artifact_path(state, "ranking.json"),
            "then": "python -m factory.optimizer record-proposal ranking <json_path>",
        }
    if state.phase == "ready_to_apply":
        return {
            "do": "Run `python -m factory.optimizer apply` to materialize the new candidate.",
            "read": [],
            "prompt": None,
            "write": None,
            "then": "python -m factory.optimizer apply <target_dir>",
        }
    if state.phase == "applied_awaiting_eval":
        hint = ""
        if state.pending.get("cache_hit"):
            hint = (f"  (cache hit: candidate was already scored "
                    f"at {state.pending.get('cached_score')}; you may use that directly)")
        return {
            "do": "Run the target agent on eval/selection.yaml with the NEW candidate skill, "
                  "compute the mean score, and record it. The gate is strict `>` against "
                  f"score_cur={state.score_cur}." + hint,
            "read": [state.pending.get("candidate_skill_path"), "eval/selection.yaml"],
            "prompt": None,
            "write": None,
            "then": "python -m factory.optimizer record-eval <score>",
        }
    if state.phase == "awaiting_slow_update":
        return {
            "do": "Apply slow_update with the longitudinal comparison of train tasks under "
                  "the previous and current epoch-end skills.",
            "read": [
                state.pending.get("previous_epoch_skill"),
                state.pending.get("current_epoch_skill"),
                f"{pc}/slow_update.md",
            ],
            "prompt": f"{pc}/slow_update.md",
            "write": _epoch_artifact_path(state, "slow_update.json"),
            "then": "python -m factory.optimizer record-proposal slow_update <json_path>",
        }
    if state.phase == "awaiting_meta_skill":
        return {
            "do": "Apply meta_skill (optimizer-side memory; NOT shipped with best_skill.md).",
            "read": [f"{pc}/meta_skill.md"],
            "prompt": f"{pc}/meta_skill.md",
            "write": _epoch_artifact_path(state, "meta_skill.json"),
            "then": "python -m factory.optimizer record-proposal meta_skill <json_path>",
        }
    if state.phase == "ready_to_finalize":
        return {
            "do": "All epochs done. Run finalize to copy the best candidate to best/.",
            "read": [],
            "prompt": None,
            "write": None,
            "then": "python -m factory.optimizer finalize <target_dir>",
        }
    if state.phase == "done":
        return {"do": "Optimization complete.", "read": [], "prompt": None,
                "write": None, "then": "python -m factory.cli publish <target_dir> --admin-root <admin>"}
    return {"do": f"unknown phase {state.phase!r}", "read": [], "prompt": None,
            "write": None, "then": None}


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory.optimizer",
                                description="SkillOpt loop driver — agent-driven, no SDK.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="Initialize state from candidates/v000/")
    pi.add_argument("target_dir")

    pn = sub.add_parser("next-step", help="Print the next action the agent should perform")
    pn.add_argument("target_dir")

    pre = sub.add_parser("record-rollouts",
                         help="Record that rollouts JSONL has been produced")
    pre.add_argument("target_dir")
    pre.add_argument("jsonl_path")

    prp = sub.add_parser("record-proposal", help="Record an optimizer-prompt response")
    prp.add_argument("target_dir")
    prp.add_argument("prompt", choices=(
        "analyst_error", "analyst_success",
        "merge_failure", "merge_success", "merge_final",
        "ranking", "slow_update", "meta_skill",
    ))
    prp.add_argument("json_path")

    pa = sub.add_parser("apply", help="Materialize the new candidate from the latest ranking")
    pa.add_argument("target_dir")

    pe = sub.add_parser("record-eval", help="Record a candidate's selection-split score")
    pe.add_argument("target_dir")
    pe.add_argument("score", type=float)

    pf = sub.add_parser("finalize", help="Copy the best candidate to best/")
    pf.add_argument("target_dir")

    return p


def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    target = Path(args.target_dir).resolve()
    try:
        if args.cmd == "init":
            out = cmd_init(target)
        elif args.cmd == "next-step":
            out = cmd_next_step(target)
        elif args.cmd == "record-rollouts":
            out = cmd_record_rollouts(target, Path(args.jsonl_path).resolve())
        elif args.cmd == "record-proposal":
            out = cmd_record_proposal(target, args.prompt, Path(args.json_path).resolve())
        elif args.cmd == "apply":
            out = cmd_apply(target)
        elif args.cmd == "record-eval":
            out = cmd_record_eval(target, args.score)
        elif args.cmd == "finalize":
            out = cmd_finalize(target)
        else:
            print(f"unknown subcommand {args.cmd!r}", file=sys.stderr)
            return 2
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[optimizer] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def run(target_dir: Path) -> dict[str, Any]:
    """Compatibility shim for cli.py build flow.

    Does NOT advance the loop. Initializes state (if not already) and prints
    the first agent-facing instruction set. The agent then drives the loop
    interactively via `factory.optimizer` subcommands.
    """
    state_path = target_dir / STATE_FILENAME
    if not state_path.exists():
        return cmd_init(target_dir)
    return cmd_next_step(target_dir)


if __name__ == "__main__":
    raise SystemExit(_main())
