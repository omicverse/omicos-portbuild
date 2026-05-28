"""Phase 6 — render a portable agent + skill bundle under the target dir.

The bundle layout is framework-agnostic — the same files install into
OmicOS, Claude Code, Codex, OpenCode, or any other agent runtime that
reads a YAML-frontmatter agent.md + a `skills/<id>/SKILL.md`-shaped
folder. Deploy is the operator's concern; portbuild only produces the
artifacts.

    <target_dir>/published/
    ├── agents/<id>.md
    └── skills/<skill_id>/
        ├── SKILL.md
        ├── references/ assets/ scripts/

Three target kinds (per the *capability* manifest):

  - kind=skill       — only writes published/skills/<skill_id>/. No agent.
  - kind=agent       — writes published/agents/<agent_id>.md + collects each
                       owned skill (by id) from sibling
                       `<root>/skills/<skill_id>/published/skills/<skill_id>/`
                       into a single published/skills/ folder.
  - kind=capability  — legacy single-target (agent + one skill, same root).

OmicOS-specific anti-write policy (`_looks_like_admin_path`): the publisher
refuses to write into a directory whose path contains `omicos-admin`. This
prevents accidental overwrites of the operator's deploy checkout during
iterate's auto-republish; it is OmicOS convention, not a generic runtime
constraint.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from factory.manifest import read_manifest
from factory.publisher_adapter import adapt_agent, validate_skill_for_publish


DEFAULT_PUBLISHED_SUBDIR = "published"


def run(
    *,
    target_dir: Path,
    out_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Publish based on the target's manifest.kind.

    Produces a portable bundle at `<target_dir>/published/`:
    `agents/<id>.md` and/or `skills/<id>/...`. No install instructions —
    the caller drops the files into whatever runtime they use.
    """
    try:
        manifest = read_manifest(target_dir)
        kind = manifest.kind or "capability"
    except FileNotFoundError:
        manifest = None
        kind = "capability"

    out_dir = (out_dir or (target_dir / DEFAULT_PUBLISHED_SUBDIR)).resolve()
    if _looks_like_admin_path(out_dir):
        raise RuntimeError(
            f"refusing to write into omicos-admin: {out_dir}. "
            f"Publish to a sandbox folder; deploy is a separate step."
        )

    if kind == "skill":
        return _publish_skill_only(target_dir, out_dir, manifest)
    if kind == "agent":
        return _publish_agent_bundle(target_dir, out_dir, manifest)
    return _publish_capability_legacy(target_dir, out_dir, manifest)


# --------------------------------------------------------------------------- #
# kind=skill — skill-only target                                              #
# --------------------------------------------------------------------------- #


def _publish_skill_only(target_dir: Path, out_dir: Path, manifest) -> dict[str, Any]:
    best = target_dir / "best"
    skill_src = best / "skill"
    if not skill_src.is_dir():
        raise FileNotFoundError(f"missing {skill_src}")

    skill_id = manifest.capability_id.replace("_", "-")
    key_apis = (manifest.extra or {}).get("key_apis") if manifest else None
    issues = validate_skill_for_publish(skill_src, key_apis=key_apis)
    if issues:
        return {"published": False, "kind": "skill", "skill_id": skill_id,
                "out_dir": str(out_dir), "issues": issues}

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "skills").mkdir(parents=True)
    skill_dst = out_dir / "skills" / skill_id
    shutil.copytree(skill_src, skill_dst)

    return {"published": True, "kind": "skill", "skill_id": skill_id,
            "skill_path": str(skill_dst), "out_dir": str(out_dir)}


# --------------------------------------------------------------------------- #
# kind=agent — agent bundle target                                            #
# --------------------------------------------------------------------------- #


def _publish_agent_bundle(target_dir: Path, out_dir: Path, manifest) -> dict[str, Any]:
    best = target_dir / "best"
    agent_src = best / "agent.md"
    if not agent_src.exists():
        raise FileNotFoundError(f"missing {agent_src}")

    agent_text = agent_src.read_text(encoding="utf-8")
    adapted_agent_text, agent_meta = adapt_agent(agent_text)

    # Gap #6: if the agent body promises a routing fixture, the file must
    # actually exist and parse as JSONL with the required schema.
    fixture_issues = _check_routing_fixture_if_promised(target_dir, agent_text)
    if fixture_issues:
        return {
            "published": False, "kind": "agent",
            "agent_id": agent_meta.get("id"),
            "out_dir": str(out_dir),
            "issues": fixture_issues,
        }

    # Find the package root (parent of agents/<agent_id>/).
    # Convention from partition.emit: <root>/agents/<agent_id>/ and <root>/skills/<skill_id>/.
    package_root = target_dir.parent.parent  # agents/<agent_id>/ → <root>
    skills_root = package_root / "skills"
    if not skills_root.is_dir():
        raise FileNotFoundError(
            f"no sibling skills/ directory at {skills_root}; "
            f"agent targets must live under <root>/agents/<id>/ with "
            f"<root>/skills/<id>/ siblings"
        )

    owns = (manifest.extra or {}).get("owns_skills") or []
    if not owns:
        raise ValueError(
            f"agent {manifest.capability_id} manifest has empty owns_skills; "
            f"cannot bundle anything"
        )

    # Collect each owned skill's published bundle. Also re-validate each
    # sibling skill's grounding using its own manifest so the agent bundle
    # cannot be assembled from skills with stale / wrong-capability content.
    from factory.manifest import read_manifest as _rm
    collected: list[tuple[str, Path]] = []
    missing: list[str] = []
    grounding_issues: list[str] = []
    for skill_id in owns:
        sibling_target = skills_root / skill_id
        sibling_pub = sibling_target / "published" / "skills" / skill_id
        if not sibling_pub.is_dir():
            missing.append(skill_id)
            continue
        # Re-run grounding check against the sibling's manifest.key_apis.
        try:
            sib_manifest = _rm(sibling_target)
            sib_key_apis = (sib_manifest.extra or {}).get("key_apis")
            sib_issues = validate_skill_for_publish(
                sibling_pub, key_apis=sib_key_apis
            )
            if sib_issues:
                grounding_issues.extend(
                    f"  {skill_id}: {iss}" for iss in sib_issues
                )
        except Exception:
            pass
        collected.append((skill_id, sibling_pub))

    if missing or grounding_issues:
        return {
            "published": False, "kind": "agent",
            "agent_id": agent_meta.get("id"),
            "out_dir": str(out_dir),
            "issues": [
                f"sibling skill {sid!r} not published yet at "
                f"{skills_root / sid / 'published' / 'skills' / sid}"
                for sid in missing
            ] + grounding_issues + (
                ["Run `omicos-portbuild publish <root>/skills/<id>` for each "
                 "owned skill before publishing the agent bundle."] if missing else []
            ),
        }

    # Write the bundle.
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "agents").mkdir(parents=True)
    (out_dir / "skills").mkdir()

    agent_dst = out_dir / "agents" / f"{agent_meta['id']}.md"
    agent_dst.write_text(adapted_agent_text, encoding="utf-8")

    for skill_id, src in collected:
        shutil.copytree(src, out_dir / "skills" / skill_id)

    return {
        "published": True, "kind": "agent",
        "agent_id": agent_meta["id"],
        "agent_path": str(agent_dst),
        "skill_ids": [sid for sid, _ in collected],
        "domain": agent_meta.get("domain"),
        "out_dir": str(out_dir),
    }


# --------------------------------------------------------------------------- #
# kind=capability (legacy)                                                    #
# --------------------------------------------------------------------------- #


def _publish_capability_legacy(target_dir: Path, out_dir: Path, manifest) -> dict[str, Any]:
    best = target_dir / "best"
    agent_src = best / "agent.md"
    skill_src = best / "skill"
    if not agent_src.exists():
        raise FileNotFoundError(f"missing {agent_src}")
    if not skill_src.is_dir():
        raise FileNotFoundError(f"missing {skill_src}")

    agent_text = agent_src.read_text(encoding="utf-8")
    adapted_agent_text, agent_meta = adapt_agent(agent_text)
    skill_id = agent_meta.get("id", target_dir.name).replace("_", "-")

    issues = validate_skill_for_publish(skill_src)
    if issues:
        return {"published": False, "kind": "capability",
                "agent_id": agent_meta.get("id"), "skill_id": skill_id,
                "domain": agent_meta.get("domain"),
                "out_dir": str(out_dir), "issues": issues}

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "agents").mkdir(parents=True)
    (out_dir / "skills").mkdir()
    agent_dst = out_dir / "agents" / f"{agent_meta['id']}.md"
    skill_dst = out_dir / "skills" / skill_id
    agent_dst.write_text(adapted_agent_text, encoding="utf-8")
    shutil.copytree(skill_src, skill_dst)

    return {
        "published": True, "kind": "capability",
        "agent_id": agent_meta["id"],
        "agent_path": str(agent_dst),
        "skill_id": skill_id, "skill_path": str(skill_dst),
        "domain": agent_meta.get("domain"),
        "out_dir": str(out_dir),
    }


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _looks_like_admin_path(p: Path) -> bool:
    parts = p.resolve().parts
    return "omicos-admin" in parts


_ROUTING_FIXTURE_PROMISE_RE = __import__("re").compile(
    r"assets/routing_fixture\.jsonl", __import__("re").IGNORECASE
)


def _check_routing_fixture_if_promised(target_dir: Path, agent_text: str) -> list[str]:
    """If agent.md mentions `assets/routing_fixture.jsonl`, the file MUST
    exist under target_dir/best/assets/ and parse as JSONL with the schema
    {"user_query": str, "expected_skill_id": str} (or {"q": ..., "expected_skill_id": ...}).

    Returns a list of issues; empty == OK.
    """
    import json as _json
    if not _ROUTING_FIXTURE_PROMISE_RE.search(agent_text):
        return []
    fixture = target_dir / "best" / "assets" / "routing_fixture.jsonl"
    if not fixture.exists():
        return [
            f"agent.md promises `assets/routing_fixture.jsonl` but no file "
            f"at {fixture.relative_to(target_dir)} exists. Either add the "
            f"fixture under best/assets/ or remove the promise from agent.md."
        ]
    n_rows = 0
    issues: list[str] = []
    for ln_no, line in enumerate(fixture.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = _json.loads(line)
        except _json.JSONDecodeError as e:
            issues.append(f"routing_fixture.jsonl line {ln_no}: invalid JSON ({e})")
            continue
        # Accept both {"user_query": ..., "expected_skill_id": ...}
        # and the shorter {"q": ..., "expected_skill_id": ...}.
        has_query = ("user_query" in row) or ("q" in row)
        has_expected = "expected_skill_id" in row
        if not (has_query and has_expected):
            issues.append(
                f"routing_fixture.jsonl line {ln_no}: missing required keys "
                f"(need user_query/q + expected_skill_id; got {sorted(row.keys())})"
            )
            continue
        n_rows += 1
    if n_rows == 0 and not issues:
        issues.append("routing_fixture.jsonl is empty")
    return issues


