"""Scorer — applies RUBRIC.md (10 dimensions) to a candidate skill folder.

OmicOS-targeted module. Two layers:

  1. Static lints (no LLM): SLOW_UPDATE block, no machine-specific paths,
     no cross-modality fabrication keywords.
  2. RUBRIC self-score (LLM in conversation via `prompt_contracts/rubric_score.md`):
     the agent reads the skill + the rubric and writes a per-dimension JSON.

Subcommands:

  lint <target>        run static lints, write LINT_REPORT.md
  score-request <target> [--candidate v00n]
                       write factory.scorer instructions for the agent;
                       agent writes _trajectory/eNN/sNN/rubric_score.json
  record-score <target> <json>
                       compute weighted score, write SCORE_REPORT_<cand>.md,
                       and return {score: float, verdict, gate_failed_dims: [...]}

This is also what the optimizer's `self-review` mode uses for the gate score
when no rollout harness exists.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from factory.patcher import has_slow_update_block


MACHINE_PATH_PATTERNS = [
    re.compile(r"/Users/[A-Za-z0-9_.-]+"),
    re.compile(r"/home/[A-Za-z0-9_.-]+"),
    re.compile(r"/scratch/[A-Za-z0-9_.-]+"),
    re.compile(r"C:\\Users\\"),
]

FABRICATION_PHRASES = [
    re.compile(r"\bpredict\s+(gene|transcript|pathway)\b", re.I),
    re.compile(r"\binfer\s+(expression|deconvolution)\b", re.I),
]

# Rubric weights and hard-gate dimensions are pre-registered (see RUBRIC.md).
RUBRIC_WEIGHTS: dict[str, int] = {
    "trigger_precision": 12,
    "execution_clarity": 12,
    "validation_strength": 12,
    "empirical_executability": 15,
    "context_efficiency": 8,
    "reusability": 8,
    "resource_partitioning": 8,
    "compatibility_robustness": 8,
    "maintainability": 4,
    "omicos_routing_fitness": 13,
}
HARD_GATE_DIMS: tuple[str, ...] = (
    "trigger_precision",
    "execution_clarity",
    "validation_strength",
    "empirical_executability",
    "omicos_routing_fitness",
)
PASS_THRESHOLD = 75
HARD_GATE_MIN = 3


# --------------------------------------------------------------------------- #
# Evidence-keyword constraint per dimension                                   #
# --------------------------------------------------------------------------- #
#
# Round 4 bug: an LLM (or batch helper) could write
# `empirical_executability=5` without ever adding a smoke section to
# SKILL.md, and the scorer would happily accept it. To block that, every
# dim score >= EVIDENCE_REQUIRED_AT_OR_ABOVE must be backed by at least
# one matching keyword in the artifact body (SKILL.md, plus agent.md for
# agent kind, plus acceptance.json for skill kind).
#
# This is a NECESSARY (not sufficient) check — it confirms a claim of
# strength has a textual footprint. A clever LLM can still fake-paste a
# trivial section, but the body of work to do so is at least visible.

EVIDENCE_REQUIRED_AT_OR_ABOVE = 4

# For each dimension, list keyword sets where at least one keyword must
# appear in the artifact body. Lists are OR'd within (any one match);
# the dim passes if at least one set's keyword matches.
RUBRIC_EVIDENCE_KEYWORDS: dict[str, list[list[str]]] = {
    "trigger_precision": [
        # use_when / example_prompts live in agent.md frontmatter; SKILL.md
        # has "Use when ..." trigger language in its description. Either works.
        ["use_when", "example_prompts", "Use when", "use when"],
    ],
    "execution_clarity": [
        ["## Quick Workflow", "## Quick workflow"],
        ["## Branches", "## Branch"],
    ],
    "validation_strength": [
        ["## Validation", "validation_budget", "smoke_commands"],
    ],
    "empirical_executability": [
        ["## Smoke testing", "## Smoke", "## Worked example",
         "## Worked invocation", "smoke_commands", "synthetic-fixture"],
    ],
    "context_efficiency": [
        # No simple keyword check — length-based check applied below.
    ],
    "reusability": [
        ["capability-first", "stable job", "reusable"],
    ],
    "resource_partitioning": [
        ["references/", "scripts/", "assets/"],
    ],
    "compatibility_robustness": [
        ["version", ">=0.", ">= 0.", "huggingface", "HF gate", "tested against"],
    ],
    "maintainability": [
        ["source-grounding", "Source Grounding"],
    ],
    "omicos_routing_fitness": [
        ["handoff", "Delivery contract", "delivery contract", "next_agent_hints"],
    ],
}


def check_evidence(dims: dict[str, dict[str, Any]],
                   artifact_body: str) -> list[str]:
    """For every dim with score >= EVIDENCE_REQUIRED_AT_OR_ABOVE, confirm
    at least one of its evidence keyword sets matches the artifact body.

    Returns a list of dim names whose score is not backed by evidence.
    """
    failed: list[str] = []
    body_lower = artifact_body.lower()
    for dim_name, d in (dims or {}).items():
        try:
            score = float((d or {}).get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        if score < EVIDENCE_REQUIRED_AT_OR_ABOVE:
            continue
        keyword_sets = RUBRIC_EVIDENCE_KEYWORDS.get(dim_name, [])
        if not keyword_sets:
            continue
        # Pass if any set has at least one keyword in body.
        ok = False
        for kset in keyword_sets:
            if any(kw.lower() in body_lower for kw in kset):
                ok = True
                break
        if not ok:
            failed.append(dim_name)
    return failed


class EvidenceCheckFailed(ValueError):
    """Raised by cmd_record_score when high-score dims lack evidence."""


@dataclass
class StaticLintResult:
    missing_slow_update_block: bool
    machine_specific_paths: list[str] = field(default_factory=list)
    cross_modality_fabrication: list[str] = field(default_factory=list)
    todo_or_fill_present: bool = False

    def is_clean(self) -> bool:
        return not (
            self.missing_slow_update_block
            or self.machine_specific_paths
            or self.cross_modality_fabrication
            or self.todo_or_fill_present
        )


# --------------------------------------------------------------------------- #
# Static lints                                                                #
# --------------------------------------------------------------------------- #


def static_lints(skill_text: str) -> StaticLintResult:
    machine = []
    for pat in MACHINE_PATH_PATTERNS:
        machine.extend(pat.findall(skill_text))
    fab = []
    for pat in FABRICATION_PHRASES:
        for m in pat.findall(skill_text):
            fab.append(m if isinstance(m, str) else " ".join(m))
    return StaticLintResult(
        missing_slow_update_block=not has_slow_update_block(skill_text),
        machine_specific_paths=machine,
        cross_modality_fabrication=fab,
        todo_or_fill_present=("TODO" in skill_text or "<FILL:" in skill_text),
    )


def cmd_lint(target_dir: Path, *, candidate: Optional[str] = None) -> dict[str, Any]:
    skill_md = _resolve_skill_md(target_dir, candidate)
    text = skill_md.read_text(encoding="utf-8")
    lint = static_lints(text)
    report = {
        "skill_path": str(skill_md.relative_to(target_dir)),
        "missing_slow_update_block": lint.missing_slow_update_block,
        "machine_specific_paths": lint.machine_specific_paths,
        "cross_modality_fabrication": lint.cross_modality_fabrication,
        "todo_or_fill_present": lint.todo_or_fill_present,
        "is_clean": lint.is_clean(),
    }
    (target_dir / "LINT_REPORT.md").write_text(
        _render_lint_md(report), encoding="utf-8"
    )
    return report


# --------------------------------------------------------------------------- #
# Rubric scoring                                                              #
# --------------------------------------------------------------------------- #


def cmd_score_request(target_dir: Path, candidate: Optional[str] = None) -> dict[str, Any]:
    """Emit instructions the agent follows to produce a rubric_score.json."""
    skill_md = _resolve_skill_md(target_dir, candidate)
    agent_md = _resolve_agent_md(target_dir, candidate)
    rubric_path = Path(__file__).resolve().parents[1] / "RUBRIC.md"
    prompt_path = Path(__file__).resolve().parent / "prompt_contracts" / "rubric_score.md"

    cand_label = candidate or skill_md.parent.parent.name
    out_path = target_dir / "_trajectory" / "score" / f"{cand_label}_rubric_score.json"
    instructions = {
        "do": (
            f"Score {skill_md.name} on the 10 OmicOS rubric dimensions per "
            f"`prompt_contracts/rubric_score.md`. Output ONLY a JSON object with "
            f"per-dimension {{score, weight, reason}} plus top_issues and "
            f"recommended_revisions."
        ),
        "read": [
            str(skill_md.relative_to(target_dir)),
            str(agent_md.relative_to(target_dir)) if agent_md else None,
            str(rubric_path),
            str(prompt_path),
        ],
        "prompt": str(prompt_path.relative_to(prompt_path.parents[2])),
        "write": str(out_path.relative_to(target_dir)),
        "then": f"python -m factory.scorer record-score {target_dir.name} {out_path.relative_to(target_dir)}",
    }
    return {"phase": "awaiting_rubric_score", "instructions": instructions}


def compute_weighted_score(dimensions: dict[str, dict[str, Any]]) -> float:
    """Compute the rubric weighted score on the 0–100 scale."""
    total = 0.0
    for dim, w in RUBRIC_WEIGHTS.items():
        d = dimensions.get(dim)
        if not d:
            continue
        score = float(d.get("score", 0))
        total += (score / 5.0) * w
    return round(total, 2)


def gate_failures(dimensions: dict[str, dict[str, Any]]) -> list[str]:
    out = []
    for dim in HARD_GATE_DIMS:
        d = dimensions.get(dim) or {}
        if float(d.get("score", 0)) < HARD_GATE_MIN:
            out.append(dim)
    return out


def verdict_for(weighted_score: float, gate_failed: list[str],
                cross_modality_fabrication: bool) -> str:
    if cross_modality_fabrication:
        return "fail"
    if gate_failed:
        return "fail"
    if weighted_score >= PASS_THRESHOLD:
        return "pass"
    return "revise"


def cmd_record_score(target_dir: Path, json_path: Path,
                     candidate: Optional[str] = None,
                     *, enforce_evidence: bool = True) -> dict[str, Any]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    dims = payload.get("dimensions") or {}
    weighted = compute_weighted_score(dims)
    gates = gate_failures(dims)
    cmf = bool(payload.get("cross_modality_fabrication", False))
    verdict = verdict_for(weighted, gates, cmf)

    # Round 4 bug fix: confirm high-score dims are backed by evidence in
    # the artifact body. Pulls SKILL.md (skill kind) and agent.md (agent
    # kind) and acceptance.json so evidence can live in any of them.
    evidence_unsupported: list[str] = []
    if enforce_evidence:
        body = _gather_artifact_body(target_dir, candidate)
        evidence_unsupported = check_evidence(dims, body)
        if evidence_unsupported:
            raise EvidenceCheckFailed(
                f"score for dimension(s) {evidence_unsupported} is "
                f">= {EVIDENCE_REQUIRED_AT_OR_ABOVE} but the artifact body "
                f"has no matching evidence keywords. Either lower the score "
                f"or add the textual evidence (e.g. for "
                f"empirical_executability: add a '## Smoke testing' or "
                f"'## Worked example' section). To bypass this gate "
                f"temporarily, pass enforce_evidence=False — but that "
                f"reintroduces the Round 4 bug class."
            )

    cand_label = candidate or json_path.stem.split("_rubric_score")[0]
    out_md = target_dir / f"SCORE_REPORT_{cand_label}.md"
    out_md.write_text(
        _render_score_md(cand_label=cand_label,
                         payload=payload,
                         weighted=weighted,
                         gates=gates,
                         verdict=verdict),
        encoding="utf-8",
    )
    return {
        "candidate": cand_label,
        "weighted_score": weighted,
        "gate_failed_dims": gates,
        "cross_modality_fabrication": cmf,
        "verdict": verdict,
        "top_issues": payload.get("top_issues") or [],
        "recommended_revisions": payload.get("recommended_revisions") or [],
        "report_path": str(out_md.relative_to(target_dir)),
    }


# --------------------------------------------------------------------------- #
# Compatibility shim for legacy cli.cmd_build                                 #
# --------------------------------------------------------------------------- #


def run(target_dir: Path) -> dict[str, Any]:
    """Backwards-compat entry called by cli.cmd_build (build pipeline).

    The new interface is `lint` / `score-request` / `record-score`. This shim
    runs lint only — full rubric scoring needs an LLM and is therefore
    triggered explicitly after Phase 3 fills are complete.
    """
    skill_md_candidates = sorted((target_dir / "best" / "skill").glob("SKILL.md"))
    if not skill_md_candidates:
        return {"verdict": "no_best_yet",
                "note": "no best/skill/SKILL.md to score; run the loop first"}
    return cmd_lint(target_dir)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _gather_artifact_body(target_dir: Path,
                          candidate: Optional[str]) -> str:
    """Concatenate SKILL.md + agent.md + acceptance.json bodies so the
    evidence check can match keywords across the whole artifact bundle."""
    parts: list[str] = []
    # SKILL body (skill or capability kind)
    try:
        skill_md = _resolve_skill_md(target_dir, candidate)
        parts.append(skill_md.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    # acceptance.json (lives alongside SKILL.md)
    try:
        skill_md = _resolve_skill_md(target_dir, candidate)
        accept = skill_md.parent / "assets" / "acceptance.json"
        if accept.exists():
            parts.append(accept.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    # agent.md (agent or capability kind)
    agent_md = _resolve_agent_md(target_dir, candidate)
    if agent_md is not None:
        parts.append(agent_md.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def _resolve_skill_md(target_dir: Path, candidate: Optional[str]) -> Path:
    # 'best' candidate label resolves to best/ subdirectory.
    if candidate == "best":
        p = target_dir / "best" / "skill" / "SKILL.md"
        if p.exists():
            return p
        raise FileNotFoundError(p)
    if candidate:
        p = target_dir / "candidates" / candidate / "skill" / "SKILL.md"
        if p.exists():
            return p
        # Some iterate labels (iter00 / iter01) aren't actual candidate dir
        # names. Fall back to best/ to score it.
        p2 = target_dir / "best" / "skill" / "SKILL.md"
        if p2.exists():
            return p2
        raise FileNotFoundError(p)
    p = target_dir / "best" / "skill" / "SKILL.md"
    if p.exists():
        return p
    # Last candidate fallback.
    cands = sorted((target_dir / "candidates").glob("v*"))
    if cands:
        return cands[-1] / "skill" / "SKILL.md"
    raise FileNotFoundError(f"no skill/SKILL.md found under {target_dir}")


def _resolve_agent_md(target_dir: Path, candidate: Optional[str]) -> Optional[Path]:
    if candidate == "best":
        p = target_dir / "best" / "agent.md"
        return p if p.exists() else None
    if candidate:
        p = target_dir / "candidates" / candidate / "agent.md"
        if p.exists():
            return p
    p = target_dir / "best" / "agent.md"
    if p.exists():
        return p
    cands = sorted((target_dir / "candidates").glob("v*"))
    if cands and (cands[-1] / "agent.md").exists():
        return cands[-1] / "agent.md"
    return None


def _render_lint_md(report: dict[str, Any]) -> str:
    lines = ["# Static Lints", "",
             f"- Skill: `{report['skill_path']}`",
             f"- Clean: **{report['is_clean']}**",
             ""]
    if report["missing_slow_update_block"]:
        lines.append("- ❌ Missing `<!-- SLOW_UPDATE_START -->` block")
    if report["machine_specific_paths"]:
        lines.append("- ❌ Machine-specific paths:")
        for p in report["machine_specific_paths"]:
            lines.append(f"    - `{p}`")
    if report["cross_modality_fabrication"]:
        lines.append("- ❌ Cross-modality fabrication keywords:")
        for p in report["cross_modality_fabrication"]:
            lines.append(f"    - `{p}`")
    if report["todo_or_fill_present"]:
        lines.append("- ❌ Unresolved `TODO` or `<FILL:>` markers remain")
    if report["is_clean"]:
        lines.append("- ✅ No static-lint issues.")
    lines.append("")
    return "\n".join(lines)


def _render_score_md(*, cand_label: str, payload: dict[str, Any],
                     weighted: float, gates: list[str], verdict: str) -> str:
    lines = [f"# Score Report — `{cand_label}`", "",
             f"- Verdict: **`{verdict}`**",
             f"- Weighted score: **{weighted:.1f} / 100** "
             f"(threshold {PASS_THRESHOLD} to pass)",
             ""]
    if gates:
        lines.append(f"- ⚠️  Hard-gate failures (score < {HARD_GATE_MIN}): "
                     f"{', '.join(gates)}")
    if payload.get("cross_modality_fabrication"):
        lines.append("- ❌ Cross-modality fabrication flagged → automatic fail.")
    lines.append("")
    lines.append("## Dimension scores")
    lines.append("")
    lines.append("| Dim | Score | Weight | Reason |")
    lines.append("|---|---:|---:|:---|")
    for dim, w in RUBRIC_WEIGHTS.items():
        d = (payload.get("dimensions") or {}).get(dim) or {}
        score = d.get("score", "–")
        reason = (d.get("reason") or "").replace("|", "/")
        gate = " 🚪" if dim in HARD_GATE_DIMS else ""
        lines.append(f"| {dim}{gate} | {score} | {w} | {reason} |")
    lines.append("")
    issues = payload.get("top_issues") or []
    if issues:
        lines.append("## Top issues")
        lines.append("")
        for i in issues:
            lines.append(f"- {i}")
        lines.append("")
    revs = payload.get("recommended_revisions") or []
    if revs:
        lines.append("## Recommended revisions")
        lines.append("")
        for r in revs:
            lines.append(f"- {r}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory.scorer",
                                description="Static lints + RUBRIC.md scoring.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("lint", help="Run static lints (no LLM).")
    pl.add_argument("target_dir")
    pl.add_argument("--candidate", default=None,
                    help="vNNN under candidates/; defaults to best/.")

    psr = sub.add_parser("score-request",
                         help="Emit instructions for an agent rubric scoring run.")
    psr.add_argument("target_dir")
    psr.add_argument("--candidate", default=None)

    prc = sub.add_parser("record-score",
                         help="Record an agent's rubric_score.json and emit SCORE_REPORT.")
    prc.add_argument("target_dir")
    prc.add_argument("json_path")
    prc.add_argument("--candidate", default=None)

    return p


def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    target = Path(args.target_dir).resolve()
    try:
        if args.cmd == "lint":
            out = cmd_lint(target, candidate=args.candidate)
        elif args.cmd == "score-request":
            out = cmd_score_request(target, args.candidate)
        elif args.cmd == "record-score":
            out = cmd_record_score(target, Path(args.json_path).resolve(),
                                   candidate=args.candidate)
        else:
            return 2
    except (FileNotFoundError, ValueError) as e:
        print(f"[scorer] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
