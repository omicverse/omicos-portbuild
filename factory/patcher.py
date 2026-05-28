"""Skill-document patcher.

Implements the SkillOpt patch contract from arXiv:2605.23904v2 §C.3:

  - Four atomic operations: append, insert_after, replace, delete.
  - target= must match exactly (no regex, no fuzzy).
  - <!-- SLOW_UPDATE_START --> ... <!-- SLOW_UPDATE_END --> is a protected
    region. Step-level edits MUST NOT touch any byte inside it.
  - Within one merged patch, no two edits may target the same text region
    (merge_failure.md rule #5: "no two edits in the merged patch may target
    the same text region").
  - Every step produces an edit_apply_report.json with per-edit accept/skip
    status, mirroring the paper's `edit_apply_report.json` artifact.

This module is intentionally LLM-free and pure-Python so it can be unit-tested
independently of the optimizer loop.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

SLOW_UPDATE_START = "<!-- SLOW_UPDATE_START -->"
SLOW_UPDATE_END = "<!-- SLOW_UPDATE_END -->"

EditOp = Literal["append", "insert_after", "replace", "delete"]
SourceType = Literal["failure", "success"]

_VALID_OPS: frozenset[str] = frozenset(("append", "insert_after", "replace", "delete"))


@dataclass
class Edit:
    """One patch edit. Mirrors the JSON contract used by analyst_*/merge_*."""

    op: EditOp
    content: str = ""
    target: str = ""
    support_count: int = 1
    source_type: Optional[SourceType] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Edit":
        op = data.get("op")
        if op not in _VALID_OPS:
            raise ValueError(
                f"Edit.op must be one of {sorted(_VALID_OPS)}, got {op!r}"
            )
        target = data.get("target", "") or ""
        content = data.get("content", "") or ""
        if op in ("insert_after", "replace", "delete") and not target:
            raise ValueError(f"Edit.op={op!r} requires non-empty target")
        if op in ("append", "insert_after", "replace") and content == "" and op != "delete":
            # delete intentionally has no content; the others should not be empty
            # but we allow empty content as a no-op edit rather than raising.
            pass
        source_type = data.get("source_type")
        if source_type is not None and source_type not in ("failure", "success"):
            raise ValueError(
                f"Edit.source_type must be 'failure' or 'success' or None, "
                f"got {source_type!r}"
            )
        try:
            support_count = int(data.get("support_count", 1))
        except (TypeError, ValueError):
            support_count = 1
        return cls(
            op=op,  # type: ignore[arg-type]
            content=content,
            target=target,
            support_count=support_count,
            source_type=source_type,  # type: ignore[arg-type]
        )


@dataclass
class EditApplyResult:
    """Per-edit accept/skip record. Serialized into edit_apply_report.json."""

    index: int
    op: str
    status: Literal["applied", "skipped"]
    reason: str = ""
    target_preview: str = ""
    content_preview: str = ""


@dataclass
class PatchReport:
    """Aggregate report for one applied patch."""

    n_edits: int
    n_applied: int
    n_skipped: int
    results: list[EditApplyResult] = field(default_factory=list)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(
            {
                "n_edits": self.n_edits,
                "n_applied": self.n_applied,
                "n_skipped": self.n_skipped,
                "results": [asdict(r) for r in self.results],
            },
            indent=indent,
            ensure_ascii=False,
        )

    def write(self, path: Path | str) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Protected slow-update region                                                #
# --------------------------------------------------------------------------- #


def slow_update_span(skill_text: str) -> Optional[tuple[int, int]]:
    """Return (start, end) byte offsets of the SLOW_UPDATE block (inclusive of
    both delimiters), or None if either delimiter is missing or malformed."""
    start = skill_text.find(SLOW_UPDATE_START)
    end = skill_text.find(SLOW_UPDATE_END)
    if start == -1 or end == -1 or end < start:
        return None
    return (start, end + len(SLOW_UPDATE_END))


def has_slow_update_block(skill_text: str) -> bool:
    return slow_update_span(skill_text) is not None


def ensure_slow_update_block(skill_text: str) -> str:
    """Append an empty SLOW_UPDATE block if the skill lacks one.

    SkillOpt requires this region to exist before the loop can do epoch
    boundary slow-updates. draft.py uses this when emitting v000.
    """
    if has_slow_update_block(skill_text):
        return skill_text
    sep = "" if skill_text.endswith("\n") else "\n"
    block = (
        f"{sep}\n{SLOW_UPDATE_START}\n"
        f"<!-- Epoch-boundary longitudinal guidance is written here by the "
        f"slow_update prompt. Step-level edits never touch this region. -->\n"
        f"{SLOW_UPDATE_END}\n"
    )
    return skill_text + block


# --------------------------------------------------------------------------- #
# Span / overlap utilities                                                    #
# --------------------------------------------------------------------------- #


def _all_target_spans(skill_text: str, target: str) -> list[tuple[int, int]]:
    if not target:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        idx = skill_text.find(target, start)
        if idx == -1:
            return spans
        spans.append((idx, idx + len(target)))
        start = idx + 1


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


def _inside_slow_update(skill_text: str, span: tuple[int, int]) -> bool:
    su = slow_update_span(skill_text)
    if not su:
        return False
    return _overlaps(span, su)


# --------------------------------------------------------------------------- #
# Apply                                                                       #
# --------------------------------------------------------------------------- #


def apply_patch(
    skill_text: str,
    edits: Iterable[Edit | dict[str, Any]],
) -> tuple[str, PatchReport]:
    """Apply a merged patch to a skill document with full safety checks.

    Returns (new_text, report). Each edit either applies cleanly or is
    skipped with a recorded reason; nothing partially applies. The
    independence check (merge_failure.md rule #5) is enforced against the
    ORIGINAL text so that ordering of edits in the merged patch does not
    secretly create masking effects.

    Skip reasons:
      - empty_target               (op needs target but target is "")
      - target_not_found
      - target_ambiguous           (target matches more than once)
      - target_inside_slow_update_region
      - overlaps_earlier_edit_target_region
      - unknown_op

    Order semantics:
      - Independence check uses the ORIGINAL skill text (so two edits
        proposed in the same patch cannot silently invalidate each other
        based on their ordering).
      - Apply step walks edits in list order against the EVOLVING text;
        an edit whose target was deleted by an earlier accepted edit will
        skip with target_not_found.
    """
    edit_list: list[Edit] = [
        e if isinstance(e, Edit) else Edit.from_dict(e) for e in edits
    ]

    # ----- Pass 1: pre-compute spans against the ORIGINAL text and
    #               detect overlapping targets (rule #5).
    original_spans: dict[int, tuple[int, int]] = {}
    for i, e in enumerate(edit_list):
        if e.op == "append":
            continue
        spans = _all_target_spans(skill_text, e.target)
        if len(spans) == 1:
            original_spans[i] = spans[0]

    rejected_for_overlap: set[int] = set()
    indices = sorted(original_spans.keys())
    for i in indices:
        for j in indices:
            if j <= i:
                continue
            if _overlaps(original_spans[i], original_spans[j]):
                rejected_for_overlap.add(max(i, j))

    # ----- Pass 2: apply each edit against the evolving text.
    text = skill_text
    results: list[EditApplyResult] = []

    for idx, e in enumerate(edit_list):
        target_preview = e.target[:80]
        content_preview = e.content[:80]

        if idx in rejected_for_overlap:
            results.append(
                EditApplyResult(
                    idx, e.op, "skipped",
                    "overlaps_earlier_edit_target_region",
                    target_preview, content_preview,
                )
            )
            continue

        if e.op == "append":
            text = _append_outside_slow_update(text, e.content)
            results.append(
                EditApplyResult(idx, "append", "applied", "", "", content_preview)
            )
            continue

        if e.op not in _VALID_OPS:
            results.append(
                EditApplyResult(
                    idx, e.op, "skipped",
                    f"unknown_op:{e.op}", target_preview, content_preview,
                )
            )
            continue

        if not e.target:
            results.append(
                EditApplyResult(
                    idx, e.op, "skipped",
                    "empty_target", target_preview, content_preview,
                )
            )
            continue

        spans = _all_target_spans(text, e.target)
        if not spans:
            results.append(
                EditApplyResult(
                    idx, e.op, "skipped",
                    "target_not_found", target_preview, content_preview,
                )
            )
            continue
        if len(spans) > 1:
            results.append(
                EditApplyResult(
                    idx, e.op, "skipped",
                    "target_ambiguous", target_preview, content_preview,
                )
            )
            continue

        span = spans[0]
        if _inside_slow_update(text, span):
            results.append(
                EditApplyResult(
                    idx, e.op, "skipped",
                    "target_inside_slow_update_region",
                    target_preview, content_preview,
                )
            )
            continue

        s, t = span
        if e.op == "insert_after":
            text = text[:t] + e.content + text[t:]
        elif e.op == "replace":
            text = text[:s] + e.content + text[t:]
        elif e.op == "delete":
            text = text[:s] + text[t:]

        results.append(
            EditApplyResult(idx, e.op, "applied", "", target_preview, content_preview)
        )

    report = PatchReport(
        n_edits=len(edit_list),
        n_applied=sum(1 for r in results if r.status == "applied"),
        n_skipped=sum(1 for r in results if r.status == "skipped"),
        results=results,
    )
    return text, report


def _append_outside_slow_update(text: str, content: str) -> str:
    """Append content. If the skill ends with the SLOW_UPDATE block (typical
    layout), insert content just before the block so that step-level appends
    never visually push the protected region around. Otherwise append at end.
    """
    if not content:
        return text
    su = slow_update_span(text)
    sep = "" if text.endswith("\n") else "\n"
    if su is None:
        return text + sep + content
    su_start, _ = su
    # Walk back from su_start over any whitespace so the SLOW_UPDATE block
    # stays cleanly separated.
    insert_at = su_start
    while insert_at > 0 and text[insert_at - 1] in "\n":
        insert_at -= 1
    prefix = text[:insert_at]
    suffix = text[insert_at:]
    prefix_sep = "" if prefix.endswith("\n") else "\n"
    return prefix + prefix_sep + content.rstrip() + "\n\n" + suffix.lstrip("\n")


# --------------------------------------------------------------------------- #
# JSON ingress                                                                #
# --------------------------------------------------------------------------- #


def edits_from_optimizer_json(payload: dict[str, Any]) -> list[Edit]:
    """Pull edits out of an analyst_*/merge_* JSON response.

    The optimizer prompts return either:
      {"patch": {"reasoning": "...", "edits": [...]}}   (analyst stage)
    or
      {"reasoning": "...", "edits": [...]}              (merge stage)
    """
    if "patch" in payload and isinstance(payload["patch"], dict):
        raw_edits = payload["patch"].get("edits", [])
    else:
        raw_edits = payload.get("edits", [])
    if not isinstance(raw_edits, list):
        raise ValueError("expected 'edits' to be a list")
    return [Edit.from_dict(e) for e in raw_edits]
