"""Router synthesis — Phase 0–5 for a router target.

OmicOS-targeted module. Routers are generated from the admin agent registry
(read-only scan of `omicos-admin/agents/*.md`), but their output lands in
`targets/_router_<domain>/best/` and then `targets/_router_<domain>/published/`.
This module NEVER writes into `omicos-admin/`; the operator copies from
`published/` into admin when they are ready.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional


def resolve_members(
    *,
    domain: str,
    members_arg: str,
    admin_root: str,
) -> list[dict[str, Any]]:
    """Resolve --members. 'auto' = scan admin/agents/ for agents in `domain`."""
    if members_arg and members_arg != "auto":
        return [
            {"id": tok.strip()} for tok in members_arg.split(",") if tok.strip()
        ]

    out: list[dict[str, Any]] = []
    agents_dir = Path(admin_root) / "agents"
    if not agents_dir.is_dir():
        return out

    for md_path in sorted(agents_dir.glob("*.md")):
        text = md_path.read_text(encoding="utf-8", errors="ignore")
        front = _parse_frontmatter(text)
        if not front:
            continue
        agent_domain = (front.get("domain") or "").strip().lower()
        if agent_domain == domain.lower():
            out.append({
                "id": md_path.stem,
                "use_when": front.get("use_when", ""),
                "example_prompts": front.get("example_prompts", []),
                "summary": front.get("summary", ""),
            })
    return out


def draft_router(*, target_dir: Path, members: list[dict[str, Any]]) -> None:
    """Emit candidates/v000/{agent.md, skill/SKILL.md} for a router target."""
    cand_dir = target_dir / "candidates" / "v000"
    cand_dir.mkdir(parents=True, exist_ok=True)

    router_id = target_dir.name  # _router_<domain>
    dispatch_lines = ["| User intent | Delegated agent |", "|---|---|"]
    for m in members:
        use_when = (m.get("use_when") or "").strip().split("\n")[0]
        dispatch_lines.append(f"| {use_when or 'TODO'} | `{m['id']}` |")
    dispatch_table = "\n".join(dispatch_lines)

    domain = router_id.replace("_router_", "")
    router_md = f"""---
id: {router_id}
name: {router_id}
title: Router — {domain}
description: Routes user requests in the `{domain}` domain to the right downstream agent.
category: routing
category_order: 0
tier: pro
summary: Router for {domain} ({len(members)} downstream agents).
use_when: |
  Any request that broadly concerns the {domain} domain but is ambiguous
  about which specific downstream agent should run.
example_prompts: []
example_inputs: []
toolsets: [plan, think, task, skill, memory]
skills: []
domain: {domain}
role: router
---

You are the **{domain} router**. Your job is to ask the user the smallest
number of clarification questions necessary to identify which downstream
agent should run, then delegate cleanly.

## Dispatch table

{dispatch_table}

## Clarification questions

TODO — generated from the member agents' use_when fields by the SkillOpt
loop. Until then, fall back to: "What output do you need? (a) morphology
report, (b) molecular prediction, (c) both."

<!-- SLOW_UPDATE_START -->
<!-- Epoch-boundary longitudinal guidance is written here by slow_update. -->
<!-- SLOW_UPDATE_END -->
"""
    (cand_dir / "agent.md").write_text(router_md, encoding="utf-8")
    (cand_dir / "routing_metadata.yaml").write_text(
        f"capability_id: {router_id}\n"
        f"kind: router-synthesis\n"
        f"domain: {domain}\n"
        f"role: router\n"
        f"members: {[m['id'] for m in members]}\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Frontmatter parsing (tolerant — admin agents have many fields)              #
# --------------------------------------------------------------------------- #


_FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def _parse_frontmatter(text: str) -> Optional[dict[str, Any]]:
    m = _FRONT_RE.match(text)
    if not m:
        return None
    raw = m.group(1)
    try:
        import yaml
        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # Naive line-by-line fallback
    out: dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out
