"""Phase 0 — discovery.

OmicOS-targeted module. Read-only scan of `OMICOS_ADMIN_ROOT/agents` and
`OMICOS_ADMIN_ROOT/skills` to detect duplicate capabilities; we NEVER write
anywhere inside admin. The factory's output lives in `targets/<id>/`.

Cheap, deterministic check: does omicos-admin already serve this capability?
Does OmicVerse already cover this algorithm? If yes, STOP and route the
factory caller to update the existing artifact instead of generating a
duplicate one.

Writes targets/<id>/DISCOVERY.md.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional


def run(
    *,
    capability_id: str,
    package: str,
    source: Optional[str],
    docs: Optional[str],
    admin_root: str,
    target_dir: Path,
) -> dict[str, Any]:
    admin = Path(admin_root)
    findings = _scan_admin(admin=admin, capability_id=capability_id, package=package)
    out_md = _render(capability_id=capability_id, package=package,
                     source=source, docs=docs, findings=findings)
    (target_dir / "DISCOVERY.md").write_text(out_md, encoding="utf-8")

    if findings["existing_agents"] or findings["existing_skills"]:
        return {
            "stop": True,
            "stop_reason": (
                f"Capability appears to already exist in admin: "
                f"agents={findings['existing_agents']} "
                f"skills={findings['existing_skills']}. "
                f"Update those instead, or pass a different --capability-id."
            ),
            **findings,
        }
    return {"stop": False, **findings}


def _scan_admin(*, admin: Path, capability_id: str, package: str) -> dict[str, Any]:
    agents_dir = admin / "agents"
    skills_dir = admin / "skills"

    pkg_lower = package.lower()
    cap_lower = capability_id.lower()
    cap_tokens = [t for t in re.split(r"[_\-/]+", cap_lower) if t and t not in {"pro", "free"}]

    existing_agents: list[str] = []
    if agents_dir.is_dir():
        for p in sorted(agents_dir.glob("*.md")):
            stem = p.stem.lower()
            if stem == cap_lower:
                existing_agents.append(p.name)
                continue
            text = p.read_text(encoding="utf-8", errors="ignore").lower()
            if pkg_lower in text and any(tok in text for tok in cap_tokens):
                existing_agents.append(p.name)

    existing_skills: list[str] = []
    if skills_dir.is_dir():
        for d in sorted(skills_dir.iterdir()):
            if not d.is_dir():
                continue
            name = d.name.lower()
            if name == cap_lower.replace("_", "-"):
                existing_skills.append(d.name)
                continue
            skill_md = d / "SKILL.md"
            if skill_md.exists():
                text = skill_md.read_text(encoding="utf-8", errors="ignore").lower()
                if pkg_lower in text and any(tok in text for tok in cap_tokens):
                    existing_skills.append(d.name)

    return {
        "existing_agents": existing_agents,
        "existing_skills": existing_skills,
        "admin_root_exists": admin.is_dir(),
    }


def _render(
    *,
    capability_id: str,
    package: str,
    source: Optional[str],
    docs: Optional[str],
    findings: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append(f"# Discovery — {capability_id}")
    lines.append("")
    lines.append(f"- Package: `{package}`")
    if source:
        lines.append(f"- Source: {source}")
    if docs:
        lines.append(f"- Docs: {docs}")
    lines.append(f"- Admin root exists: {findings['admin_root_exists']}")
    lines.append("")
    lines.append("## Existing admin artifacts that may already cover this capability")
    if findings["existing_agents"]:
        lines.append("")
        lines.append("### Agents")
        for a in findings["existing_agents"]:
            lines.append(f"- `agents/{a}`")
    if findings["existing_skills"]:
        lines.append("")
        lines.append("### Skills")
        for s in findings["existing_skills"]:
            lines.append(f"- `skills/{s}/`")
    if not findings["existing_agents"] and not findings["existing_skills"]:
        lines.append("")
        lines.append("None found. Proceeding to Phase 1.")
    lines.append("")
    return "\n".join(lines)
