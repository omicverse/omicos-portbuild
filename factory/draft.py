"""Phase 3 — co-draft an OmicOS agent + its skill from manifest + grounding.

OmicOS-targeted module. The factory's deliverable is the PAIR (agent, skill).
This module:

  1. Pre-fills the parts that are derivable without an LLM (capability_id,
     toolsets, category_order default, etc.).
  2. Marks every field that needs LLM input with `<FILL: explain>` so the
     downstream agent (Claude Code / Codex) knows exactly what to write.
  3. Embeds the `<!-- SLOW_UPDATE_START -->` / `<!-- SLOW_UPDATE_END -->`
     block in v000 so the SkillOpt loop has somewhere to write epoch-boundary
     guidance.

Publisher refuses to ship any artifact still containing `<FILL:` markers.
Run `python -m factory.draft check targets/<id>` to see what's still
unresolved.

Drafting flow (rebuildr-style, agent-driven):

  $ omicos-portbuild build ...               # writes scaffolded v000
  ... factory prints: "draft these fields in conversation" with file paths ...
  ... Claude Code reads SOURCE_GROUNDING.md + PARTITION.md, fills <FILL:>s ...
  $ python -m factory.draft check targets/<id>   # confirms no unresolved markers
  $ python -m factory.optimizer init targets/<id>  # start the SkillOpt loop
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

from factory.manifest import read_manifest
from factory.patcher import ensure_slow_update_block


FILL_MARKER_RE = re.compile(r"<FILL:\s*([^>]+?)\s*>")

_TOOLSETS_DEFAULT = [
    "file_manager",
    "python_interpreter",
    "shell",
    "plan",
    "think",
    "task",
    "skill",
    "memory",
]


# --------------------------------------------------------------------------- #
# Templates                                                                   #
# --------------------------------------------------------------------------- #


_AGENT_TEMPLATE_MULTISKILL = """---
icon: 🧪
id: {capability_id}
name: {human_name}
title: <FILL: 5-12 word title naming the agent's domain or the package, NOT one sub-task>
description: <FILL: full agent description: who they are, what bundle of capabilities they own, when to delegate to siblings>
category: <FILL: one of the admin categories (e.g. spatial_analysis, single_cell, bulk_*, multiomics, ...)>
category_order: 100
tier: pro
summary: <FILL: one-line summary of what this agent covers across its owned skills>
use_when: |
  <FILL: 3-6 lines covering each major intent this agent dispatches between.
        For an "omni" agent on a third-party package, list each skill's
        trigger condition so the runtime layer can pick this agent broadly
        and the agent itself picks the right skill internally.>
example_prompts:
  - <FILL: a realistic user request matching one of the owned skills>
  - <FILL: a different intent matching another owned skill>
  - <FILL: a third intent — try to span the skill set>
example_inputs:
  - input: <FILL: "<concrete input artifact for one of the owned skills>">
    output: <FILL: "<corresponding output (REPORT.html / summary.json / handoff)>">
toolsets:
{toolsets_block}
skills:
{skills_block}
{domain_line}---

You are **{human_name}**.

<FILL: 1-2 paragraphs — what this agent owns ACROSS its skill bundle, what
it explicitly does NOT own, and which sibling agents it delegates to. The
agent is the FRONT DOOR for the package; internally it picks the right
skill based on the user's request.>

## Skill dispatch table

For each owned skill, name the intent that should route to it. The runtime
layer matches the user's request against `use_when` to pick THIS agent;
inside this agent body, you pick which OWNED skill to call.

<FILL: a 2-column table mapping intents to owned-skill ids, e.g.
| User intent | Skill |
|---|---|
| "single-slide morphology + report" | pathology-foo-morphology |
| "cohort + labels → classifier" | pathology-foo-classification |
...
>

## Clarification questions (ask up-front, do not assume)

<FILL: 4-7 numbered clarification questions covering the SHARED concerns
across owned skills (modality, organ, HF gate, GPU, output layer, ...)
plus the ONE question that disambiguates which owned skill to run. Skip a
question if the user already answered it.>

## Delivery contract

The agent always produces:

- `outputs/REPORT.html` — human-readable report
- `outputs/summary.json` — machine-readable summary
- `outputs/handoff_manifest.json` — what's available for the next agent

<FILL: capability-specific additions to this contract across all owned
skills, or "none">
"""


_AGENT_TEMPLATE = """---
icon: 🧪
id: {capability_id}
name: {human_name}
title: <FILL: 5-12 word title naming the capability, NOT the package or sample dataset>
description: <FILL: full agent description: who they are, what task they own, when to delegate>
category: <FILL: one of the admin categories (e.g. spatial_analysis, single_cell, bulk_*, multiomics, ...)>
category_order: 100
tier: pro
summary: <FILL: one-line capability summary, mixed-language if it helps (zh + en is fine)>
use_when: |
  <FILL: 2-5 lines describing user intents that should route here. Cover:
        - what kind of input the user has
        - what kind of output they want
        - the disambiguation question if the request is ambiguous>
example_prompts:
  - <FILL: a realistic user request that should trigger this agent>
  - <FILL: another phrasing of the same intent>
  - <FILL: an adjacent intent that should ALSO trigger this agent>
example_inputs:
  - input: <FILL: "<describe a concrete input artifact (file paths abstracted)>">
    output: <FILL: "<describe the corresponding output (REPORT.html / summary.json / handoff)>">
toolsets:
{toolsets_block}
skills:
  - {skill_id}
  - <FILL: any additional reusable skills this agent should be allowed to call,
         or remove this line if the one above is enough>
{domain_line}---

You are **{human_name}**.

<FILL: 1-2 paragraph persona — what this agent owns, what it explicitly does
NOT own, and which sibling agents it delegates to (e.g. spatial_omics_orchestrator
for downstream spatial analysis, scientific_writer for manuscripts, etc.).
Use concrete language. Do not mention the example dataset by name unless it
is execution-critical.>

## Clarification questions (ask up-front, do not assume)

<FILL: 3-8 numbered clarification questions the agent asks before producing
output. Examples of good questions:
  1. Does the user have paired Visium / RNA / proteomics data?
  2. What species and organ/tissue?
  3. Is the goal a report, a figure, or a downstream handoff?
Do NOT assume the answer; if the user already covered it in their prompt,
skip that question rather than re-asking.>

## Delivery contract

The agent always produces:

- `outputs/REPORT.html` — human-readable report
- `outputs/summary.json` — machine-readable summary
- `outputs/handoff_manifest.json` — what's available for the next agent

<FILL: any capability-specific additions to this contract, or "none">
"""


_SKILL_TEMPLATE = """---
id: {skill_id}
name: {skill_id}
title: <FILL: skill title, capability-first (not dataset-first)>
description: <FILL: 1-2 sentences. Include "Use when ..." trigger language so
            another agent can pick this skill from intent alone.>
category: <FILL: admin skill category — usually matches the agent's category>
category_order: 100
tier: pro
summary: <FILL: one-line summary>
use_when: <FILL: short, single-line "Use when ..." trigger>
---

# <FILL: skill title>

## Goal

<FILL: one short paragraph. State the stable job, not the source notebook.>

## Quick Workflow

<FILL: 4-8 ordered steps. Each step is one imperative sentence. Reference
the live API functions inspected in references/source-grounding.md.>

## Resource Map

- Read `references/source-grounding.md` when documenting concrete signatures.
- <FILL: any additional reference files this skill needs, or remove this line>

## Constraints

- <FILL: capability-specific constraint, e.g. requires GPU, gated HF weights, ...>
- <FILL: another constraint, or remove>

## Validation

- <FILL: a concrete output-existence check, e.g. `adata.obsm["X_pca"]` exists>
- <FILL: a value-range or schema check, e.g. tile count > 0>

<!-- SLOW_UPDATE_START -->
<!-- Epoch-boundary longitudinal guidance is written here by the slow_update
     prompt. Step-level edits never touch this region. -->
<!-- SLOW_UPDATE_END -->
"""


_ACCEPTANCE_TEMPLATE = """{
  "version": 1,
  "skill": "{skill_id}",
  "sample_requests": ["<FILL: at least one realistic user request>"],
  "required_description_terms": [],
  "required_sections": ["## Goal", "## Quick Workflow", "## Validation"],
  "required_body_terms": [],
  "required_paths": [],
  "smoke_commands": [],
  "validation_budget": {
    "smoke_mode": "import_only",
    "max_wall_seconds": 120,
    "skip_reason": ""
  }
}
"""


_DRAFT_INSTRUCTIONS_TEMPLATE = """# Phase 3 — Draft the agent + skill PAIR

The factory has scaffolded `candidates/v000/`. Two files need to be filled in
before the SkillOpt loop starts. Both contain `<FILL: …>` markers; replace
each marker with the indicated content. The publisher refuses to ship any
artifact that still contains a marker.

## Files to fill in

1. `candidates/v000/agent.md` — the OmicOS runtime persona
2. `candidates/v000/skill/SKILL.md` — the execution policy

## How to fill in

Read these inputs in order:

- `manifest.yaml` — capability_id, package, cost_profile
- `PARTITION.md` — which capability this is (vs. the package's other capabilities)
- `SOURCE_GROUNDING.md` — live API signatures, branch parameters, model registry

Then rewrite each `<FILL: …>` marker into final content. The marker's text
explains what to write (e.g. `<FILL: 5-12 word title naming the capability>`).
Do NOT leave any marker behind.

## How to check

```bash
python -m factory.draft check targets/{capability_id}
```

That command lists every unresolved `<FILL:>` per file. Once it returns OK,
proceed to:

```bash
python -m factory.optimizer init targets/{capability_id}
```

## What you're producing

A *pair* of OmicOS artifacts:

- The **agent** owns routing and persona — what the user asks, when to ask
  clarification, when to delegate to siblings, what report contract is
  guaranteed.
- The **skill** owns execution — the live API calls, the validation
  postconditions, the SLOW_UPDATE region the optimizer will train against.

Both ship together, always. The `published/` bundle puts the agent in
`agents/<id>.md` and the skill in `skills/<skill_id>/`, matching the
omicos-admin two-tier layout.
"""


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #


def run(target_dir: Path) -> None:
    """Scaffold candidates/v000/ — shape depends on manifest.kind.

    kind=skill       → only skill/ folder (no agent.md).
    kind=agent       → only agent.md (no skill/ folder); pre-fills owns_skills.
    kind=capability  → legacy: both agent.md and skill/ folder.
    """
    manifest = read_manifest(target_dir)
    kind = manifest.kind or "capability"
    cand_dir = target_dir / "candidates" / "v000"
    cand_dir.mkdir(parents=True, exist_ok=True)

    capability_id = manifest.capability_id
    domain = manifest.extra.get("domain") if isinstance(manifest.extra, dict) else None

    if kind == "skill":
        _scaffold_skill(cand_dir, capability_id, domain)
    elif kind == "agent":
        owns = (manifest.extra or {}).get("owns_skills") or []
        _scaffold_agent(cand_dir, capability_id, domain, owns)
    elif kind == "router-synthesis":
        # Routers go through factory.route, not factory.draft.
        pass
    else:
        # Legacy capability: both halves.
        _scaffold_skill(cand_dir, capability_id, domain)
        _scaffold_agent(cand_dir, capability_id, domain,
                        [capability_id.replace("_", "-")])

    (target_dir / "DRAFT_INSTRUCTIONS.md").write_text(
        _DRAFT_INSTRUCTIONS_TEMPLATE.format(capability_id=capability_id),
        encoding="utf-8",
    )


def _scaffold_skill(cand_dir: Path, capability_id: str,
                    domain: Optional[str]) -> None:
    skill_dir = cand_dir / "skill"
    refs_dir = skill_dir / "references"
    assets_dir = skill_dir / "assets"
    for d in (skill_dir, refs_dir, assets_dir):
        d.mkdir(parents=True, exist_ok=True)

    skill_id = capability_id.replace("_", "-") if "_" in capability_id else capability_id

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        text = _SKILL_TEMPLATE.format(skill_id=skill_id)
        # Gap #5: if SOURCE_GROUNDING.md is already present at the capability
        # root (from `factory.source_grounding inspect`), pre-fill the
        # Quick Workflow section with an API skeleton so the agent doesn't
        # have to re-type the names from the source-grounding table.
        target_root = cand_dir.parent.parent  # candidates/v000/ → target/
        skeleton = _quick_workflow_skeleton_from_grounding(target_root)
        if skeleton:
            text = text.replace(
                "<FILL: 4-8 ordered steps. Each step is one imperative sentence. Reference\nthe live API functions inspected in references/source-grounding.md.>",
                skeleton,
            )
        text = ensure_slow_update_block(text)
        skill_md.write_text(text, encoding="utf-8")

    grounding = refs_dir / "source-grounding.md"
    if not grounding.exists():
        grounding.write_text(
            "# Source Grounding\n\nTODO — populated by "
            "`factory.source_grounding inspect`.\n",
            encoding="utf-8",
        )

    acceptance = assets_dir / "acceptance.json"
    if not acceptance.exists():
        acceptance.write_text(
            _ACCEPTANCE_TEMPLATE.replace("{skill_id}", skill_id),
            encoding="utf-8",
        )

    routing_meta = cand_dir / "routing_metadata.yaml"
    if not routing_meta.exists():
        routing_meta.write_text(
            f"capability_id: {capability_id}\n"
            f"kind: skill\n"
            f"domain: {domain or '<FILL: e.g. spatial-he, single-cell, bulk-rna>'}\n"
            f"role: terminal\n",
            encoding="utf-8",
        )


_SOURCE_GROUNDING_API_LINE = re.compile(
    r"^\|\s*`([A-Za-z_][A-Za-z0-9_]*)`\s*\|\s*(function|class)\s*\|", re.MULTILINE
)


def _quick_workflow_skeleton_from_grounding(target_root: Path) -> str:
    """Read SOURCE_GROUNDING.md if present and emit a numbered Quick Workflow
    skeleton listing the detected APIs as `<FILL: describe step using <name>(...)>`
    placeholders. Agent fills in real text per step.

    Returns empty string if SOURCE_GROUNDING.md is missing or has no APIs.
    """
    grounding_md = target_root / "SOURCE_GROUNDING.md"
    if not grounding_md.exists():
        return ""
    try:
        text = grounding_md.read_text(encoding="utf-8")
    except Exception:
        return ""
    names = []
    for m in _SOURCE_GROUNDING_API_LINE.finditer(text):
        name = m.group(1)
        if name in names:
            continue
        # Skip docstring helpers and noise
        if name.startswith("_") or name in {"Name", "Kind"}:
            continue
        names.append(name)
        if len(names) >= 8:
            break
    if not names:
        return ""
    lines = [f"{i+1}. <FILL: imperative step using `{n}`(...) — what it does, what it writes to the WSI container>"
             for i, n in enumerate(names)]
    return "\n".join(lines)


def _scaffold_agent(cand_dir: Path, capability_id: str,
                    domain: Optional[str], owns_skills: list[str]) -> None:
    human_name = " ".join(w.capitalize() for w in capability_id.split("_"))
    toolsets_block = "\n".join(f"  - {t}" for t in _TOOLSETS_DEFAULT)
    skills_block = "\n".join(f"  - {s}" for s in owns_skills) if owns_skills \
                    else "  - <FILL: at least one kebab-case skill id>"
    domain_line = f"domain: {domain}\n" if domain else ""

    agent_path = cand_dir / "agent.md"
    if not agent_path.exists():
        agent_path.write_text(
            _AGENT_TEMPLATE_MULTISKILL.format(
                capability_id=capability_id,
                human_name=human_name,
                skills_block=skills_block,
                toolsets_block=toolsets_block,
                domain_line=domain_line,
            ),
            encoding="utf-8",
        )

    routing_meta = cand_dir / "routing_metadata.yaml"
    if not routing_meta.exists():
        skills_yaml = "\n".join(f"  - {s}" for s in owns_skills) or "  []"
        routing_meta.write_text(
            f"capability_id: {capability_id}\n"
            f"kind: agent\n"
            f"domain: {domain or '<FILL: e.g. spatial-he, single-cell, bulk-rna>'}\n"
            f"role: terminal\n"
            f"owns_skills:\n{skills_yaml}\n",
            encoding="utf-8",
        )


# --------------------------------------------------------------------------- #
# check                                                                       #
# --------------------------------------------------------------------------- #


def find_fill_markers(target_dir: Path) -> dict[str, list[str]]:
    """Return {relative_path: [marker_explain, ...]} for every unresolved marker."""
    out: dict[str, list[str]] = {}
    for cand in sorted((target_dir / "candidates").glob("v*")):
        for sub in ("agent.md", "skill/SKILL.md", "skill/assets/acceptance.json",
                    "routing_metadata.yaml"):
            f = cand / sub
            if not f.exists():
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
            hits = FILL_MARKER_RE.findall(text)
            if hits:
                out[str(f.relative_to(target_dir))] = hits
    return out


def cmd_check(target_dir: Path) -> int:
    unresolved = find_fill_markers(target_dir)
    if not unresolved:
        print(f"[draft] OK — no <FILL:> markers remain in {target_dir.name}")
        return 0
    print(f"[draft] {sum(len(v) for v in unresolved.values())} unresolved "
          f"<FILL:> markers across {len(unresolved)} file(s):")
    for path, hits in unresolved.items():
        print(f"  {path}")
        for h in hits:
            print(f"    - <FILL: {h}>")
    return 1


def cmd_promote(target_dir: Path) -> int:
    """Promote the latest candidate to best/ without running the SkillOpt loop.

    Used when the operator has drafted a single v0NN they consider ready to
    publish, but doesn't want / need the iterate or optimizer loop yet.
    Refuses to promote if any <FILL:> markers remain.
    """
    unresolved = find_fill_markers(target_dir)
    if unresolved:
        print(f"[draft] refusing to promote — {sum(len(v) for v in unresolved.values())} "
              f"<FILL:> markers remain. Run `factory.draft check` to see them.",
              file=sys.stderr)
        return 1
    cands = sorted((target_dir / "candidates").glob("v*"))
    if not cands:
        print(f"[draft] no candidates/v* under {target_dir}", file=sys.stderr)
        return 1
    src = cands[-1]
    import shutil
    best = target_dir / "best"
    if best.exists():
        shutil.rmtree(best)
    best.mkdir()
    if (src / "agent.md").exists():
        shutil.copy2(src / "agent.md", best / "agent.md")
    if (src / "skill").is_dir():
        shutil.copytree(src / "skill", best / "skill")
    if (src / "routing_metadata.yaml").exists():
        shutil.copy2(src / "routing_metadata.yaml", best / "routing_metadata.yaml")
    print(f"[draft] promoted {src.name} → best/")
    return 0


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="factory.draft",
                                description="Co-draft an OmicOS agent + skill.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="Scaffold candidates/v000/ with FILL markers.")
    pi.add_argument("target_dir")

    pc = sub.add_parser("check", help="List unresolved <FILL:> markers.")
    pc.add_argument("target_dir")

    pp = sub.add_parser("promote", help="Promote the latest candidate to best/ (no loop).")
    pp.add_argument("target_dir")

    args = p.parse_args(argv)
    target = Path(args.target_dir).resolve()
    if args.cmd == "init":
        run(target)
        try:
            manifest = read_manifest(target)
            kind = manifest.kind or "capability"
        except Exception:
            kind = "capability"
        print(f"[draft] scaffolded {target.name}/candidates/v000/ (kind={kind})")
        if kind == "skill":
            print(f"[draft] next: open candidates/v000/skill/SKILL.md, fill in "
                  f"every <FILL:> marker, then `python -m factory.draft check "
                  f"{target.name}`")
        elif kind == "agent":
            print(f"[draft] next: open candidates/v000/agent.md, fill in every "
                  f"<FILL:> marker (skill dispatch table + clarification + "
                  f"delivery contract), then `python -m factory.draft check "
                  f"{target.name}`")
        else:
            print(f"[draft] next: open candidates/v000/agent.md and "
                  f"skill/SKILL.md, fill in every <FILL:> marker, then "
                  f"`python -m factory.draft check {target.name}`")
        return 0
    if args.cmd == "check":
        return cmd_check(target)
    if args.cmd == "promote":
        return cmd_promote(target)
    return 2


if __name__ == "__main__":
    sys.exit(_main())
