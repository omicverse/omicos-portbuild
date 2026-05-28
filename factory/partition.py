"""Phase 1 — capability partition.

OmicOS-targeted module. Rebuildr-style agent-driven CLI: the LLM at the
keyboard (Claude Code / Codex) is the partitioning model.

Subcommands:

  init <root>   Read the package's tutorial list (from manifest.notebooks or
                the docs URL) and write:
                  - PARTITION_INSTRUCTIONS.md (what the agent should fill)
                  - partition.json (stub with <FILL:> markers)

  emit <root>   Read filled-in partition.json and scaffold one capability
                target subdir per entry:
                  <root>/<capability_id>/manifest.yaml
                Each subdir is then ready for `factory.draft init <root>/<id>`.

  status <root> Print the partition's resolved capabilities + their target
                subdirs.

The intent is: one `partition init` + agent-fills-in + `partition emit` turns
a package's whole tutorial set into N capability scaffolds in one pass, so
the operator doesn't have to manually invent `--capability-id` for each one.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from factory.manifest import Manifest, read_manifest, write_initial_manifest

PARTITION_JSON = "partition.json"
PARTITION_INSTRUCTIONS = "PARTITION_INSTRUCTIONS.md"


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #


def cmd_init(
    root: Path,
    *,
    max_capabilities: int = 8,
    package: Optional[str] = None,
    source: Optional[str] = None,
    docs: Optional[str] = None,
    notebooks: Optional[list[str]] = None,
    cost_profile: str = "moderate",
) -> dict[str, Any]:
    """Write PARTITION_INSTRUCTIONS.md and a stub partition.json.

    `root` is the package working directory. If `root/manifest.yaml` doesn't
    exist yet, this also creates a package-shape manifest from the supplied
    args. Each emitted capability will be a subdirectory inside `root`.
    """
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "manifest.yaml").exists():
        if not package:
            raise ValueError(
                f"missing {root / 'manifest.yaml'} — pass --package, "
                f"--source, --docs, and --notebooks so partition can "
                f"bootstrap the package-level manifest"
            )
        write_initial_manifest(
            target_dir=root,
            capability_id=f"{package}_package_root",
            kind="capability",
            package=package,
            source=source,
            docs=docs,
            notebooks=notebooks or [],
            cost_profile=cost_profile,
            extra={"layout": "multi-capability"},
        )
    manifest = read_manifest(root)

    from factory import __version__ as _fv
    stub = {
        "factory_version": _fv,
        "package": manifest.package or "<FILL: package name>",
        "source": manifest.source,
        "docs": manifest.docs,
        "reasoning": "<FILL: one paragraph — how you grouped the tutorials and why>",
        "capabilities": [
            {
                "id": "<FILL: kebab-case skill id, e.g. pathology-pkg-subtask>",
                "title": "<FILL: 5-12 word capability-first title>",
                "tutorials": ["<FILL: tutorial titles or URLs>"],
                "key_modules": ["<FILL: <pkg>.<submodule>>"],
                "key_apis": [{"name": "<FILL: pkg.sub.func>", "purpose": "<FILL: one line>"}],
                "distinct_from": "<FILL: why this is separate from siblings>",
                "domain": "<FILL: domain key, e.g. spatial-he>",
                "cost_profile": "<FILL: cheap|moderate|gpu-required|infeasible-local>"
            }
        ],
        "agents": [
            {
                "id": "<FILL: snake_case agent id, e.g. pathology_pkg>",
                "title": "<FILL: agent title — name of the package or domain it covers>",
                "owns_skills": ["<FILL: kebab-case skill ids this agent dispatches to; can be ALL the capabilities or a subset>"],
                "domain": "<FILL: domain key, e.g. spatial-he>",
                "bundle_rationale": "<FILL: why these skills are bundled under one agent — e.g. same package, same input modality, same admin surface>"
            }
        ],
        "pedagogy_only_tutorials": ["<FILL: pedagogy-only titles, or leave empty>"]
    }
    (root / PARTITION_JSON).write_text(
        json.dumps(stub, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    notebooks = manifest.notebooks or []
    notebook_block = (
        "\n".join(f"  - `{n}`" for n in notebooks) if notebooks else "  (none in manifest)"
    )
    instr = f"""# Phase 1 — Partition `{manifest.package}` into capabilities

The factory has written a stub `{PARTITION_JSON}`. Fill it in by following
`factory/prompt_contracts/partition.md`.

## Inputs

- Package: `{manifest.package}`
- Source: {manifest.source or "(none)"}
- Docs:   {manifest.docs or "(none)"}
- Tutorials / how-to pages listed in manifest.yaml:
{notebook_block}

## What to write

Replace every `<FILL: …>` in `{PARTITION_JSON}` per the partition.md prompt
contract. Cluster tutorials into independently-triggerable capabilities; mark
pedagogy-only pages in `pedagogy_only_tutorials`.

Aim for AT MOST {max_capabilities} capabilities. Prefer fewer, broader ones.

## How to check

```bash
python -m factory.partition status {root.name}
```

Lists resolved capabilities + any remaining <FILL:> markers.

## How to emit

Once all `<FILL:>` markers are replaced and the JSON parses cleanly:

```bash
python -m factory.partition emit {root.name}
```

This creates one `<capability_id>/manifest.yaml` per entry. From there,
each capability follows the normal `factory.draft init <root>/<id>` →
fill `<FILL:>`s → `optimizer init` → SkillOpt loop flow.
"""
    (root / PARTITION_INSTRUCTIONS).write_text(instr, encoding="utf-8")
    return {
        "partition_json": str((root / PARTITION_JSON).relative_to(root.parent)),
        "instructions": str((root / PARTITION_INSTRUCTIONS).relative_to(root.parent)),
    }


# --------------------------------------------------------------------------- #
# status                                                                      #
# --------------------------------------------------------------------------- #


def cmd_status(root: Path) -> dict[str, Any]:
    pj = root / PARTITION_JSON
    if not pj.exists():
        return {"error": f"no {PARTITION_JSON}; run `partition init` first"}
    raw = pj.read_text(encoding="utf-8")
    n_fills = raw.count("<FILL:")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": f"partition.json does not parse: {e}",
                "fills_remaining": n_fills}
    caps = data.get("capabilities", []) or []
    agents = data.get("agents", []) or []
    return {
        "package": data.get("package"),
        "n_capabilities": len(caps),
        "n_agents": len(agents),
        "capabilities": [
            {"id": c.get("id"), "title": c.get("title"),
             "n_tutorials": len(c.get("tutorials") or []),
             "skill_dir_exists": (root / "skills" / (c.get("id") or "")).is_dir()}
            for c in caps
        ],
        "agents": [
            {"id": a.get("id"), "title": a.get("title"),
             "n_owned_skills": len(a.get("owns_skills") or []),
             "agent_dir_exists": (root / "agents" / (a.get("id") or "")).is_dir()}
            for a in agents
        ],
        "fills_remaining": n_fills,
        "ready_to_emit": n_fills == 0 and len(caps) > 0,
    }


# --------------------------------------------------------------------------- #
# emit                                                                        #
# --------------------------------------------------------------------------- #


def cmd_emit(root: Path) -> dict[str, Any]:
    pj = root / PARTITION_JSON
    if not pj.exists():
        raise FileNotFoundError(f"no {pj}")
    raw = pj.read_text(encoding="utf-8")
    if "<FILL:" in raw:
        n = raw.count("<FILL:")
        raise ValueError(
            f"{n} unresolved <FILL:> markers in {PARTITION_JSON}; "
            f"finish filling it in first (see PARTITION_INSTRUCTIONS.md)"
        )
    data = json.loads(raw)
    caps = data.get("capabilities") or []
    agents = data.get("agents") or []
    if not caps:
        raise ValueError("partition.json has empty capabilities[]")

    parent_manifest = read_manifest(root)
    package_meta = {
        "package": data.get("package") or parent_manifest.package,
        "source": data.get("source") or parent_manifest.source,
        "docs": data.get("docs") or parent_manifest.docs,
        "python_env": parent_manifest.python_env,
        "r_env": parent_manifest.r_env,
    }

    skills_created: list[dict[str, Any]] = []
    agents_created: list[dict[str, Any]] = []

    # --- Skill-only targets under <root>/skills/<id>/ -------------------- #
    skills_dir = root / "skills"
    skills_dir.mkdir(exist_ok=True)
    for cap in caps:
        cap_id = cap.get("id")
        if not cap_id:
            raise ValueError("capability missing 'id'")
        target = skills_dir / cap_id
        if (target / "manifest.yaml").exists():
            skills_created.append({"id": cap_id, "status": "exists",
                                   "path": str(target)})
            continue
        target.mkdir(parents=True, exist_ok=True)
        write_initial_manifest(
            target_dir=target,
            capability_id=cap_id,
            kind="skill",
            package=package_meta["package"],
            source=package_meta["source"],
            docs=package_meta["docs"],
            notebooks=cap.get("tutorials") or [],
            cost_profile=cap.get("cost_profile") or "moderate",
            python_env=package_meta["python_env"],
            r_env=package_meta["r_env"],
            extra={
                "domain": cap.get("domain"),
                "title": cap.get("title"),
                "key_modules": cap.get("key_modules"),
                "key_apis": cap.get("key_apis"),
                "distinct_from": cap.get("distinct_from"),
            },
        )
        skills_created.append({"id": cap_id, "status": "created",
                               "path": str(target)})

    # --- Agent-only targets under <root>/agents/<id>/ -------------------- #
    agents_dir = root / "agents"
    if agents:
        agents_dir.mkdir(exist_ok=True)
    skill_ids = {c.get("id") for c in caps}
    for agent in agents:
        agent_id = agent.get("id")
        if not agent_id:
            raise ValueError("agent entry missing 'id'")
        owns = agent.get("owns_skills") or []
        unknown = [s for s in owns if s not in skill_ids]
        if unknown:
            raise ValueError(
                f"agent {agent_id!r} owns_skills references unknown skill ids: "
                f"{unknown}. Skill ids must match a `capabilities[].id`."
            )
        target = agents_dir / agent_id
        if (target / "manifest.yaml").exists():
            agents_created.append({"id": agent_id, "status": "exists",
                                   "path": str(target)})
            continue
        target.mkdir(parents=True, exist_ok=True)
        write_initial_manifest(
            target_dir=target,
            capability_id=agent_id,
            kind="agent",
            package=package_meta["package"],
            source=package_meta["source"],
            docs=package_meta["docs"],
            notebooks=[],
            cost_profile="moderate",
            python_env=package_meta["python_env"],
            r_env=package_meta["r_env"],
            extra={
                "domain": agent.get("domain"),
                "title": agent.get("title"),
                "owns_skills": owns,
                "bundle_rationale": agent.get("bundle_rationale"),
            },
        )
        agents_created.append({"id": agent_id, "status": "created",
                               "path": str(target)})

    return {
        "package": data.get("package"),
        "root": str(root),
        "n_skills_created": sum(1 for c in skills_created if c["status"] == "created"),
        "n_skills_existed": sum(1 for c in skills_created if c["status"] == "exists"),
        "skills": skills_created,
        "n_agents_created": sum(1 for c in agents_created if c["status"] == "created"),
        "n_agents_existed": sum(1 for c in agents_created if c["status"] == "exists"),
        "agents": agents_created,
        "next_step": (
            "1. For each skill: `python -m factory.draft init <root>/skills/<id>` "
            "→ fill → `draft check` → `draft promote` → `factory.cli publish-skill`.\n"
            "2. For each agent: `python -m factory.draft init <root>/agents/<id>` "
            "→ fill → `draft check` → `draft promote` → `factory.cli publish-agent` "
            "(which bundles the agent + its owned skills)."
        ),
    }


# --------------------------------------------------------------------------- #
# update — sync partition.json deltas into existing per-target manifests      #
# --------------------------------------------------------------------------- #


def cmd_update(root: Path, *, force: bool = False) -> dict[str, Any]:
    """Compare partition.json against existing per-target manifests and
    apply non-destructive merges (added key_apis / new agents / etc).

    Non-destructive policy:
    - Adding fields (key_apis entries, new capabilities, new agents) is OK.
    - Renaming an existing capability id is REFUSED unless --force.
    - Removing key_apis is REFUSED unless --force (would break grounding
      check for existing skills referencing the API).
    - Adding a new capability creates its subdir; adding a new agent same.
    - Removing a capability that has an existing subdir is REFUSED unless
      --force (we never silently delete LLM work).

    Returns a structured diff describing what changed / would change.
    """
    pj = root / PARTITION_JSON
    if not pj.exists():
        raise FileNotFoundError(f"no {pj}")
    raw = pj.read_text(encoding="utf-8")
    if "<FILL:" in raw:
        raise ValueError("partition.json has unresolved <FILL:> markers; "
                         "finish it before updating")
    data = json.loads(raw)

    caps = data.get("capabilities") or []
    agents = data.get("agents") or []
    changes: dict[str, list[Any]] = {
        "skills_added": [], "agents_added": [],
        "skills_with_apis_added": [], "skills_with_apis_removed": [],
        "skills_orphaned_existing": [], "agents_orphaned_existing": [],
        "refused_without_force": [],
    }

    # Compare against existing subdirs
    skills_dir = root / "skills"
    agents_dir = root / "agents"
    existing_skill_dirs = {d.name for d in skills_dir.iterdir()
                           if (d / "manifest.yaml").exists()} if skills_dir.is_dir() else set()
    existing_agent_dirs = {d.name for d in agents_dir.iterdir()
                           if (d / "manifest.yaml").exists()} if agents_dir.is_dir() else set()
    partition_skill_ids = {c.get("id") for c in caps if c.get("id")}
    partition_agent_ids = {a.get("id") for a in agents if a.get("id")}

    # Orphan detection
    skill_orphans = existing_skill_dirs - partition_skill_ids
    agent_orphans = existing_agent_dirs - partition_agent_ids
    if skill_orphans and not force:
        changes["refused_without_force"].append(
            f"skill subdirs not in partition.json: {sorted(skill_orphans)} "
            f"(removing would discard LLM work; pass --force to confirm)"
        )
    if agent_orphans and not force:
        changes["refused_without_force"].append(
            f"agent subdirs not in partition.json: {sorted(agent_orphans)}"
        )
    changes["skills_orphaned_existing"] = sorted(skill_orphans)
    changes["agents_orphaned_existing"] = sorted(agent_orphans)

    parent_manifest = read_manifest(root)
    package_meta = {
        "package": data.get("package") or parent_manifest.package,
        "source": data.get("source") or parent_manifest.source,
        "docs": data.get("docs") or parent_manifest.docs,
        "python_env": parent_manifest.python_env,
        "r_env": parent_manifest.r_env,
    }

    # --- Per-skill diff + create new ones --------------------------------- #
    for cap in caps:
        cap_id = cap.get("id")
        if not cap_id:
            continue
        target = skills_dir / cap_id
        if cap_id not in existing_skill_dirs:
            target.mkdir(parents=True, exist_ok=True)
            write_initial_manifest(
                target_dir=target,
                capability_id=cap_id, kind="skill",
                package=package_meta["package"],
                source=package_meta["source"],
                docs=package_meta["docs"],
                notebooks=cap.get("tutorials") or [],
                cost_profile=cap.get("cost_profile") or "moderate",
                python_env=package_meta["python_env"],
                r_env=package_meta["r_env"],
                extra={"domain": cap.get("domain"),
                       "title": cap.get("title"),
                       "key_modules": cap.get("key_modules"),
                       "key_apis": cap.get("key_apis"),
                       "distinct_from": cap.get("distinct_from")},
            )
            changes["skills_added"].append(cap_id)
            continue
        # Skill exists — diff its key_apis
        m = read_manifest(target)
        old_apis = (m.extra or {}).get("key_apis") or []
        new_apis = cap.get("key_apis") or []
        old_names = {_api_name(a) for a in old_apis}
        new_names = {_api_name(a) for a in new_apis}
        added = new_names - old_names
        removed = old_names - new_names
        if removed and not force:
            changes["refused_without_force"].append(
                f"skill {cap_id!r}: removing key_apis {sorted(removed)} would "
                f"break grounding; pass --force to confirm"
            )
            continue
        if added or (removed and force):
            # Merge: write the new key_apis (and other extra fields) into the
            # existing manifest, preserving anything the operator may have
            # added manually.
            merged_extra = dict(m.extra or {})
            merged_extra["key_apis"] = new_apis
            # Also refresh title / domain / distinct_from from partition.
            for k in ("domain", "title", "key_modules", "distinct_from"):
                if cap.get(k) is not None:
                    merged_extra[k] = cap.get(k)
            _rewrite_manifest_extra(target, m, merged_extra,
                                    new_notebooks=cap.get("tutorials") or m.notebooks,
                                    new_cost=cap.get("cost_profile") or m.cost_profile)
            changes["skills_with_apis_added"].append(
                {"id": cap_id, "added": sorted(added),
                 "removed": sorted(removed) if force else []}
            )

    # --- Per-agent diff --------------------------------------------------- #
    for agent in agents:
        agent_id = agent.get("id")
        if not agent_id:
            continue
        target = agents_dir / agent_id
        if agent_id not in existing_agent_dirs:
            agents_dir.mkdir(exist_ok=True)
            target.mkdir(parents=True, exist_ok=True)
            write_initial_manifest(
                target_dir=target,
                capability_id=agent_id, kind="agent",
                package=package_meta["package"],
                source=package_meta["source"],
                docs=package_meta["docs"],
                notebooks=[],
                cost_profile="moderate",
                python_env=package_meta["python_env"],
                r_env=package_meta["r_env"],
                extra={"domain": agent.get("domain"),
                       "title": agent.get("title"),
                       "owns_skills": agent.get("owns_skills") or [],
                       "bundle_rationale": agent.get("bundle_rationale")},
            )
            changes["agents_added"].append(agent_id)

    return changes


def _api_name(api: Any) -> str:
    if isinstance(api, str):
        return api
    return (api or {}).get("name") or ""


def _rewrite_manifest_extra(target_dir: Path, manifest, new_extra: dict,
                            *, new_notebooks: list[str], new_cost: str) -> None:
    """Rewrite manifest.yaml preserving the dataclass shape."""
    import yaml as _yaml
    new = Manifest(
        capability_id=manifest.capability_id,
        kind=manifest.kind,
        package=manifest.package,
        source=manifest.source,
        docs=manifest.docs,
        notebooks=new_notebooks,
        cost_profile=new_cost,
        python_env=manifest.python_env,
        r_env=manifest.r_env,
        splits=manifest.splits,
        optimizer=manifest.optimizer,
        quality=manifest.quality,
        factory_version=manifest.factory_version,
        extra=new_extra,
    )
    (target_dir / "manifest.yaml").write_text(
        _yaml.safe_dump(new.to_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Compatibility shim for cli.cmd_build                                        #
# --------------------------------------------------------------------------- #


def run(target_dir: Path) -> None:
    """Backwards-compat entry point called from cli.cmd_build.

    The new partition flow is per-package (multi-capability). The legacy
    cli.cmd_build pipeline calls partition.run(target_dir) on a single
    capability target. In that mode we just emit a minimal PARTITION.md
    pointer to the per-package partition file if one exists at the parent,
    otherwise note that partitioning was skipped.
    """
    out = target_dir / "PARTITION.md"
    if out.exists():
        return
    parent_pj = target_dir.parent / PARTITION_JSON
    if parent_pj.exists():
        out.write_text(
            f"# Capability Partition\n\n"
            f"This capability is one of N defined in `../{PARTITION_JSON}`.\n"
            f"See that file for the full package-level partition + reasoning.\n",
            encoding="utf-8",
        )
    else:
        out.write_text(
            "# Capability Partition\n\n"
            "Single-capability build (no package-level partition file present). "
            "Run `factory.partition init <package_root>` to drive the multi-"
            "capability flow.\n",
            encoding="utf-8",
        )


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="factory.partition",
        description="Cluster a package's tutorials into independently-triggerable capabilities.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="Write partition.json stub + instructions.")
    pi.add_argument("root")
    pi.add_argument("--max-capabilities", type=int, default=8)
    pi.add_argument("--package", default=None,
                    help="Bootstrap manifest.yaml if missing (required when "
                         "the root has no manifest yet).")
    pi.add_argument("--source", default=None)
    pi.add_argument("--docs", default=None)
    pi.add_argument("--notebooks", default="",
                    help="Comma-separated tutorial titles or paths.")
    pi.add_argument("--cost-profile",
                    choices=("cheap", "moderate", "gpu-required", "infeasible-local"),
                    default="moderate")

    ps = sub.add_parser("status", help="Show resolved capabilities + FILL count.")
    ps.add_argument("root")

    pe = sub.add_parser("emit", help="Scaffold one target subdir per capability.")
    pe.add_argument("root")

    pu = sub.add_parser("update",
                        help="Sync partition.json changes into existing target manifests.")
    pu.add_argument("root")
    pu.add_argument("--force", action="store_true",
                    help="Allow destructive changes (removed key_apis / orphaned subdirs).")

    return p


def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    try:
        if args.cmd == "init":
            out = cmd_init(
                root,
                max_capabilities=args.max_capabilities,
                package=args.package,
                source=args.source,
                docs=args.docs,
                notebooks=[n.strip() for n in args.notebooks.split(",") if n.strip()],
                cost_profile=args.cost_profile,
            )
        elif args.cmd == "status":
            out = cmd_status(root)
        elif args.cmd == "emit":
            out = cmd_emit(root)
        elif args.cmd == "update":
            out = cmd_update(root, force=args.force)
        else:
            return 2
    except (FileNotFoundError, ValueError) as e:
        print(f"[partition] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
