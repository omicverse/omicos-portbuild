"""Validate a target directory or a published admin artifact.

Our validator replaces upstream awesome-skill-generate's validate_skills.py.
The key differences:

- We understand the rebuildr-style `targets/<id>/` layout (manifest.yaml,
  candidates/, best/, eval/), not just `<skill>/{SKILL.md, references, scripts, assets}`.
- We enforce the SkillOpt invariant: SKILL.md MUST contain a well-formed
  SLOW_UPDATE block from v000 onward.
- We check admin-shape frontmatter on best/agent.md (id/name/title/etc.).
- We lint for machine-specific absolute paths.

Usage:
    python -m factory.validate target targets/pathology_lazyslide_he_analysis
    python -m factory.validate admin-skill ../omicos-admin/skills/pathology-lazyslide-he-analysis
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from factory.publisher_adapter import (
    FRONT_RE,
    REQUIRED_AGENT_FIELDS,
    MACHINE_PATH_RE,
    SLOW_UPDATE_RE,
)


def validate_target(target_dir: Path) -> list[str]:
    issues: list[str] = []
    if not (target_dir / "manifest.yaml").exists():
        issues.append(f"{target_dir.name}: missing manifest.yaml")

    candidates = sorted((target_dir / "candidates").glob("v*")) if (target_dir / "candidates").exists() else []
    if not candidates:
        issues.append(f"{target_dir.name}: no candidates/v* drafted yet")
    for cand in candidates:
        skill_md = cand / "skill" / "SKILL.md"
        if not skill_md.exists():
            issues.append(f"{cand.name}: missing skill/SKILL.md")
            continue
        text = skill_md.read_text(encoding="utf-8", errors="ignore")
        if not SLOW_UPDATE_RE.search(text):
            issues.append(f"{cand.name}: missing SLOW_UPDATE block")

    best_dir = target_dir / "best"
    if not best_dir.exists():
        issues.append(f"{target_dir.name}: best/ not produced yet — run the optimizer loop")
    else:
        issues.extend(_validate_best(best_dir))

    return issues


def _validate_best(best: Path) -> list[str]:
    issues: list[str] = []
    agent = best / "agent.md"
    skill = best / "skill" / "SKILL.md"

    if agent.exists():
        text = agent.read_text(encoding="utf-8")
        m = FRONT_RE.match(text)
        if not m:
            issues.append("best/agent.md missing frontmatter")
        else:
            front_text = m.group(1)
            for field in REQUIRED_AGENT_FIELDS:
                if f"{field}:" not in front_text:
                    issues.append(f"best/agent.md frontmatter missing field: {field}")
            if "TODO" in text:
                issues.append("best/agent.md still contains TODO")
    else:
        issues.append("best/agent.md missing")

    if skill.exists():
        text = skill.read_text(encoding="utf-8")
        if not SLOW_UPDATE_RE.search(text):
            issues.append("best/skill/SKILL.md missing SLOW_UPDATE block")
        if "TODO" in text:
            issues.append("best/skill/SKILL.md still contains TODO")
        for line in text.splitlines():
            if MACHINE_PATH_RE.search(line):
                issues.append(f"best/skill/SKILL.md contains machine-specific path: {line[:120]}")
                break
    else:
        issues.append("best/skill/SKILL.md missing")

    return issues


def validate_admin_skill(skill_dir: Path) -> list[str]:
    """Validate a single admin-side skill folder (post-publish)."""
    issues: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [f"{skill_dir.name}: missing SKILL.md"]
    text = skill_md.read_text(encoding="utf-8")
    if not SLOW_UPDATE_RE.search(text):
        issues.append(f"{skill_dir.name}: missing SLOW_UPDATE block")
    for line in text.splitlines():
        if MACHINE_PATH_RE.search(line):
            issues.append(f"{skill_dir.name}: machine-specific path: {line[:120]}")
            break
    if "TODO" in text:
        issues.append(f"{skill_dir.name}: TODO marker present")
    return issues


def _main() -> int:
    p = argparse.ArgumentParser(prog="factory.validate")
    sub = p.add_subparsers(dest="mode", required=True)

    pt = sub.add_parser("target", help="Validate a targets/<id>/ folder")
    pt.add_argument("target_dir")

    pa = sub.add_parser("admin-skill", help="Validate an admin/skills/<id>/ folder")
    pa.add_argument("skill_dir")

    args = p.parse_args()
    if args.mode == "target":
        issues = validate_target(Path(args.target_dir).resolve())
    else:
        issues = validate_admin_skill(Path(args.skill_dir).resolve())

    if issues:
        print("validation issues:")
        for i in issues:
            print(f"- {i}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
