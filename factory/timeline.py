"""Append-only event log for the SkillOpt loop.

Every state transition the agent drives writes one JSON line to
`targets/<id>/_trajectory/timeline.jsonl`. This is the source of truth for:

  - ITERATION_LOG.md  (re-rendered after every event)
  - EVOLUTION_PLOT.svg (re-generated on demand by factory.plot_evolution)
  - SCORE_REPORT.md   (final report; reads aggregates from the timeline)

Modelled after omicverse-rebuildr's iteration log: rejected attempts are
preserved as first-class entries, not silently dropped. Reading the log
end-to-end should let a reviewer see what was tried, what survived, and
why a particular `best_skill.md` ended up where it did.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

TIMELINE_FILENAME = "_trajectory/timeline.jsonl"
ITERATION_LOG_FILENAME = "ITERATION_LOG.md"

# Known event kinds (used by ITERATION_LOG renderer to pick a section).
EVENT_KINDS = (
    "loop_initialized",
    "rollouts_recorded",
    "proposal_recorded",
    "candidate_applied",
    "candidate_evaluated",
    "slow_update_applied",
    "meta_skill_recorded",
    "epoch_boundary",
    "loop_finalized",
)


@dataclass
class Event:
    ts: str
    kind: str
    epoch: int
    step: int
    payload: dict[str, Any] = field(default_factory=dict)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_event(
    target_dir: Path,
    kind: str,
    epoch: int,
    step: int,
    payload: Optional[dict[str, Any]] = None,
) -> Event:
    """Write one event to timeline.jsonl. Returns the appended Event."""
    if kind not in EVENT_KINDS:
        # Soft validation — accept unknown kinds, future-proof.
        pass
    ev = Event(ts=now_iso(), kind=kind, epoch=epoch, step=step, payload=payload or {})
    tl_path = target_dir / TIMELINE_FILENAME
    tl_path.parent.mkdir(parents=True, exist_ok=True)
    with tl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(ev), ensure_ascii=False) + "\n")
    # After every event, rewrite the human-readable log so a reviewer
    # can `cat ITERATION_LOG.md` mid-run and see what's happened so far.
    render_iteration_log(target_dir)
    return ev


def read_events(target_dir: Path) -> list[Event]:
    tl_path = target_dir / TIMELINE_FILENAME
    if not tl_path.exists():
        return []
    out: list[Event] = []
    for line in tl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            out.append(Event(
                ts=d.get("ts", ""),
                kind=d.get("kind", "unknown"),
                epoch=int(d.get("epoch", 0)),
                step=int(d.get("step", 0)),
                payload=d.get("payload", {}) or {},
            ))
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------- #
# Rendering                                                                   #
# --------------------------------------------------------------------------- #


def render_iteration_log(target_dir: Path) -> Path:
    """Render ITERATION_LOG.md from the current timeline.jsonl.

    Sections:
      - Header (capability id, total epochs, current score_best)
      - One subsection per epoch, with a per-step table:
            step | n_failures | n_successes | n_proposed | n_applied | score_cand | decision
      - A "Rejected edits" rollup at the end, mirroring rebuildr's policy of
        keeping rejected attempts visible.
    """
    events = read_events(target_dir)
    out = target_dir / ITERATION_LOG_FILENAME
    out.write_text(_render(events, target_dir), encoding="utf-8")
    return out


def _render(events: list[Event], target_dir: Path) -> str:
    # Pull capability id + totals from the loop_initialized event.
    init = next((e for e in events if e.kind == "loop_initialized"), None)
    final = next((e for e in events if e.kind == "loop_finalized"), None)
    cap_id = (init.payload.get("capability_id") if init else target_dir.name) or target_dir.name
    total_epochs = (init.payload.get("total_epochs") if init else None) or 0

    score_cur, score_best = _latest_scores(events)
    accepts = [e for e in events if e.kind == "candidate_evaluated"
               and e.payload.get("decision") != "reject"]
    rejects = [e for e in events if e.kind == "candidate_evaluated"
               and e.payload.get("decision") == "reject"]

    lines: list[str] = []
    lines.append(f"# Iteration Log — `{cap_id}`")
    lines.append("")
    lines.append(f"- Total epochs: {total_epochs}")
    lines.append(f"- Events recorded: {len(events)}")
    lines.append(f"- Latest score_cur: {_fmt_score(score_cur)}")
    lines.append(f"- Latest score_best: {_fmt_score(score_best)}")
    lines.append(f"- Accepts: {len(accepts)}     Rejects: {len(rejects)}")
    if final:
        lines.append(f"- Loop finalized at: {final.ts}")
    lines.append("")
    lines.append("> This log is rebuilt after every state transition. Rejected attempts are kept.")
    lines.append("")

    # Per-epoch tables.
    epochs = sorted({e.epoch for e in events if e.epoch >= 1})
    for ep in epochs:
        ep_events = [e for e in events if e.epoch == ep]
        lines.append(f"## Epoch {ep}")
        lines.append("")
        lines.append("| step | n_fail | n_succ | n_propose | n_apply | score_cand | decision | candidate |")
        lines.append("|---:|---:|---:|---:|---:|---:|:---|:---|")
        rollouts_by_step: dict[int, dict[str, Any]] = {}
        propose_by_step: dict[int, int] = {}
        apply_by_step: dict[int, dict[str, Any]] = {}
        eval_by_step: dict[int, dict[str, Any]] = {}
        for ev in ep_events:
            if ev.kind == "rollouts_recorded":
                rollouts_by_step[ev.step] = ev.payload
            elif ev.kind == "proposal_recorded" and ev.payload.get("prompt") == "ranking":
                # Use the ranking step's selected_indices length as the "propose" count
                propose_by_step[ev.step] = ev.payload.get("n_selected", 0)
            elif ev.kind == "candidate_applied":
                apply_by_step[ev.step] = ev.payload
            elif ev.kind == "candidate_evaluated":
                eval_by_step[ev.step] = ev.payload
        steps = sorted(set(rollouts_by_step) | set(eval_by_step) | set(apply_by_step))
        for s in steps:
            r = rollouts_by_step.get(s, {})
            a = apply_by_step.get(s, {})
            e = eval_by_step.get(s, {})
            lines.append(
                f"| {s} | "
                f"{r.get('n_failures', '–')} | "
                f"{r.get('n_successes', '–')} | "
                f"{propose_by_step.get(s, '–')} | "
                f"{a.get('n_applied', '–')} | "
                f"{_fmt_score(e.get('score_cand'))} | "
                f"{e.get('decision', '–')} | "
                f"`{a.get('candidate', '–')}` |"
            )
        # End-of-epoch artifacts.
        su = next((ev for ev in ep_events if ev.kind == "slow_update_applied"), None)
        ms = next((ev for ev in ep_events if ev.kind == "meta_skill_recorded"), None)
        if su or ms:
            lines.append("")
            if su:
                lines.append(f"- slow_update applied → candidate `{su.payload.get('candidate', '–')}` "
                             f"(decision: {su.payload.get('decision', '–')})")
            if ms:
                lines.append(f"- meta_skill recorded → `{ms.payload.get('path', '–')}` "
                             f"(optimizer-side only; NOT shipped)")
        lines.append("")

    # Rejected rollup.
    if rejects:
        lines.append("## Rejected attempts (for the record)")
        lines.append("")
        lines.append("| epoch | step | candidate | score_cand | score_cur_at_decision | score_drop |")
        lines.append("|---:|---:|:---|---:|---:|---:|")
        for ev in rejects:
            cand = ev.payload.get("candidate", "–")
            sc = ev.payload.get("score_cand")
            scur = ev.payload.get("score_cur_at_decision")
            drop = (scur - sc) if (sc is not None and scur is not None) else None
            lines.append(
                f"| {ev.epoch} | {ev.step} | `{cand}` | "
                f"{_fmt_score(sc)} | {_fmt_score(scur)} | {_fmt_score(drop)} |"
            )
        lines.append("")

    return "\n".join(lines)


def _latest_scores(events: list[Event]) -> tuple[Optional[float], Optional[float]]:
    score_cur = score_best = None
    for ev in events:
        if ev.kind == "loop_initialized":
            score_cur = ev.payload.get("score_cur")
            score_best = ev.payload.get("score_best")
        elif ev.kind == "candidate_evaluated":
            if ev.payload.get("decision") != "reject":
                cand = ev.payload.get("score_cand")
                if cand is not None:
                    score_cur = cand
                    if score_best is None or cand > score_best:
                        score_best = cand
    return score_cur, score_best


def _fmt_score(v: Any) -> str:
    if v is None:
        return "–"
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)
