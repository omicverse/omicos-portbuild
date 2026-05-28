"""Adapt factory-shape (agent.md with admin-style frontmatter) into the exact
omicos-admin schema, and lint best/skill/ before publish.

OmicOS-targeted module. The frontmatter contract here is the omicos-admin
schema specifically; if you ever needed to target a different host (Biomni,
some other agent runtime), this is the file you would replace.

Admin agent frontmatter (observed in omicos-admin/agents/he_to_st_predictor.md):
  icon, id, name, tier, category, category_order, summary, use_when,
  example_prompts, example_inputs, toolsets, skills, description

We don't try to *generate* missing fields here — we validate them.

Round 4 bug fix: `validate_skill_for_publish` now also accepts the
capability's `key_apis` from manifest and refuses to publish if SKILL.md
body doesn't reference at least 40% of those API basenames. This blocks
the failure mode where a batch helper pasted the SAME 'worked example'
code (using lazyslide.tools.feature_extraction) into 6 different skills
including ones whose actual APIs were lazyslide.segmentation.cells,
lazyslide.tools.virtual_stain, etc.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Tuple

REQUIRED_AGENT_FIELDS = (
    "id", "name", "title", "description", "category", "tier", "summary", "use_when",
)

FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

MACHINE_PATH_RE = re.compile(r"(/Users/|/home/[^/]+/|/scratch/[^/]+/|C:\\Users\\)")
SLOW_UPDATE_RE = re.compile(
    r"<!--\s*SLOW_UPDATE_START\s*-->.*?<!--\s*SLOW_UPDATE_END\s*-->",
    re.DOTALL,
)


def adapt_agent(agent_text: str) -> Tuple[str, dict[str, Any]]:
    """Parse the agent frontmatter, ensure all required admin fields exist,
    and return (text, parsed_frontmatter)."""
    m = FRONT_RE.match(agent_text)
    if not m:
        raise ValueError("agent.md missing YAML frontmatter")

    raw = m.group(1)
    try:
        import yaml
        front = yaml.safe_load(raw)
        if not isinstance(front, dict):
            raise ValueError("frontmatter did not parse to a mapping")
    except Exception as e:  # pragma: no cover
        raise ValueError(f"could not parse frontmatter: {e}")

    missing = [k for k in REQUIRED_AGENT_FIELDS if not front.get(k)]
    if missing:
        raise ValueError(f"agent frontmatter missing required fields: {missing}")

    if "TODO" in str(front.get("description")):
        raise ValueError("agent description still contains TODO; refusing to publish")

    # Hard block: <FILL:…> markers. draft.py uses these for the parts that
    # need LLM input; the publisher refuses to ship any draft that wasn't
    # finished. Use `factory.draft check` to see what's left.
    if "<FILL:" in agent_text:
        raise ValueError(
            "agent.md contains unresolved <FILL:> markers; "
            "run `python -m factory.draft check <target>` to see what's left"
        )

    # Pass-through for now — agent_text is already admin-shape. Future commits
    # might inject `icon`, derive `category_order` from manifest, etc.
    return agent_text, front


def validate_skill_for_publish(
    skill_dir: Path,
    *,
    key_apis: Any = None,
    grounding_min_ratio: float = 0.4,
) -> list[str]:
    """Return a list of human-readable issues that block publish. Empty list
    means OK to publish.

    If `key_apis` is supplied (from the capability's manifest.extra), the
    validator ALSO checks that the SKILL.md body actually references at
    least `grounding_min_ratio` (default 40%) of those API basenames. This
    blocks the bug where a batch helper pasted identical worked-example
    code into multiple skills whose actual APIs differ.
    """
    issues: list[str] = []
    skill_md = skill_dir / "SKILL.md"

    if not skill_md.exists():
        return [f"missing {skill_md}"]
    text = skill_md.read_text(encoding="utf-8")

    if not SLOW_UPDATE_RE.search(text):
        issues.append("SKILL.md missing well-formed <!-- SLOW_UPDATE_START ... END --> block")

    for line in text.splitlines():
        if MACHINE_PATH_RE.search(line):
            issues.append(f"SKILL.md contains machine-specific path: {line.strip()[:120]}")
            break

    for refs_md in (skill_dir / "references").rglob("*.md") if (skill_dir / "references").exists() else []:
        text_r = refs_md.read_text(encoding="utf-8", errors="ignore")
        for line in text_r.splitlines():
            if MACHINE_PATH_RE.search(line):
                issues.append(
                    f"{refs_md.relative_to(skill_dir)} contains machine-specific path"
                )
                break

    if "TODO" in text:
        issues.append("SKILL.md still contains TODO markers; refusing to publish")

    if "<FILL:" in text:
        issues.append(
            "SKILL.md contains unresolved <FILL:> markers; "
            "run `python -m factory.draft check <target>` to see what's left"
        )

    # ---- API grounding: SKILL.md must reference its own key_apis -------- #
    if key_apis:
        allowed = set(_extract_api_basenames(key_apis))
        if allowed:
            tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
            hits = [n for n in allowed if n in tokens]
            ratio = len(hits) / len(allowed)
            if ratio < grounding_min_ratio:
                missing = [n for n in allowed if n not in tokens]
                issues.append(
                    f"SKILL.md references only {len(hits)}/{len(allowed)} "
                    f"({ratio:.0%}) of its capability's key_apis (threshold "
                    f"{grounding_min_ratio:.0%}). Missing examples: "
                    f"{sorted(missing)[:5]}. The SKILL body likely belongs "
                    f"to a different capability or has stale boilerplate."
                )

            # Stricter check: code blocks in SKILL.md MUST NOT call
            # capability-specific APIs that aren't in key_apis. Generic
            # helpers (open_wsi / find_tissues / tile_tissues / write /
            # tile_graph / tissue_props) are exempt because every WSI
            # workflow uses them as preconditions.
            unauthorized = _unauthorized_api_calls_in_code(text, allowed)
            if unauthorized:
                issues.append(
                    f"SKILL.md code block(s) call API(s) NOT in this "
                    f"capability's key_apis: {sorted(unauthorized)[:8]}. "
                    f"This usually means the worked-example or smoke block "
                    f"was copy-pasted from a different skill. Either add "
                    f"the API(s) to manifest.key_apis (legitimate cross-"
                    f"capability use) or rewrite the code block in terms "
                    f"of this skill's own APIs."
                )

    return issues


# Generic preconditions every WSI workflow uses; these never count as
# "unauthorized" even if they're not in a capability's key_apis.
_GENERIC_WSI_HELPERS = frozenset({
    "open_wsi", "find_tissues", "tile_tissues", "tissue_props",
    "tile_graph", "write", "fetch", "shapes", "tables", "images",
    "whoami", "scan_cache_dir", "huggingface_cli", "huggingface_hub",
})

_CODE_API_CALL_RE = re.compile(
    r"(?:zs|lazyslide)\.[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)"
)


def _unauthorized_api_calls_in_code(text: str, allowed: set[str]) -> set[str]:
    """Walk code blocks in SKILL.md, find capability-specific API calls,
    return the set NOT in `allowed` and NOT in the generic-helpers list.
    """
    found: set[str] = set()
    in_fence = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            continue
        for m in _CODE_API_CALL_RE.finditer(line):
            name = m.group(1)
            if name in _GENERIC_WSI_HELPERS:
                continue
            if name not in allowed:
                found.add(name)
    return found


def _extract_api_basenames(key_apis: Any) -> list[str]:
    """Turn manifest.key_apis entries into searchable function basenames.

    Accepts compound forms commonly used in partition.json:
      [{"name": "lazyslide.tools.feature_extraction", ...}, ...]
      [{"name": "zs.tl.feature_extraction(wsi, ...)", ...}, ...]
      ["wsidata.open_wsi"]
      [{"name": "lazyslide.segmentation.artifact / semantic / zero_shot"}]
      [{"name": "lazyslide.tools.text_embedding + tools.text_image_similarity"}]
      [{"name": "lazyslide.segmentation.CellSegmentationRunner / SemanticSegmentationRunner"}]

    Returns the set of last-segment identifiers across all those forms.
    """
    if not isinstance(key_apis, list):
        return []
    out: list[str] = []
    for item in key_apis:
        raw = item if isinstance(item, str) else (item or {}).get("name") or ""
        if not raw:
            continue
        head = raw.split("(", 1)[0]
        # Split on common compound separators: / , + and , so one entry
        # can declare multiple APIs at once.
        for piece in re.split(r"\s*[/,+]\s*", head):
            piece = piece.strip()
            if not piece:
                continue
            basename = piece.rsplit(".", 1)[-1].strip()
            # Validate identifier shape — skip non-identifiers like
            # "wsi.write" right-hand sides that happen to contain dots.
            if basename and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", basename):
                if basename not in out:
                    out.append(basename)
    return out
