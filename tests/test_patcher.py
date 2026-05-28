"""Unit tests for factory.patcher.

These tests are LLM-free and stdlib-only so they run on bare python 3.10+.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory.patcher import (  # noqa: E402
    SLOW_UPDATE_END,
    SLOW_UPDATE_START,
    Edit,
    apply_patch,
    edits_from_optimizer_json,
    ensure_slow_update_block,
    has_slow_update_block,
    slow_update_span,
)


SKILL_FIXTURE = """# Lazyslide Direct Analysis

## Goal

Run an H&E analysis with lazyslide.

## Quick Workflow

1. Open the WSI.
2. Tile at 224px / 0.5 mpp.
3. Embed with UNI.

## Validation

- Confirm tile count > 0.

<!-- SLOW_UPDATE_START -->
<!-- protected -->
<!-- SLOW_UPDATE_END -->
"""


class TestSlowUpdateMarkers(unittest.TestCase):
    def test_detects_block(self):
        self.assertTrue(has_slow_update_block(SKILL_FIXTURE))
        span = slow_update_span(SKILL_FIXTURE)
        self.assertIsNotNone(span)
        s, e = span  # type: ignore[misc]
        self.assertEqual(SKILL_FIXTURE[s : s + len(SLOW_UPDATE_START)], SLOW_UPDATE_START)
        self.assertTrue(SKILL_FIXTURE[:e].endswith(SLOW_UPDATE_END))

    def test_missing_block(self):
        self.assertFalse(has_slow_update_block("no markers here"))
        self.assertIsNone(slow_update_span("no markers here"))

    def test_only_start_returns_none(self):
        self.assertIsNone(slow_update_span(f"{SLOW_UPDATE_START}\nstuff"))

    def test_inverted_order_returns_none(self):
        self.assertIsNone(slow_update_span(f"{SLOW_UPDATE_END}\nstuff\n{SLOW_UPDATE_START}"))

    def test_ensure_idempotent(self):
        unchanged = ensure_slow_update_block(SKILL_FIXTURE)
        self.assertEqual(unchanged, SKILL_FIXTURE)

    def test_ensure_adds_when_missing(self):
        bare = "# Skill\n\nbody.\n"
        out = ensure_slow_update_block(bare)
        self.assertTrue(has_slow_update_block(out))
        self.assertTrue(out.startswith(bare))


class TestEditFromDict(unittest.TestCase):
    def test_valid_append(self):
        e = Edit.from_dict({"op": "append", "content": "foo"})
        self.assertEqual(e.op, "append")
        self.assertEqual(e.content, "foo")

    def test_unknown_op_raises(self):
        with self.assertRaises(ValueError):
            Edit.from_dict({"op": "rewrite", "content": "x"})

    def test_insert_after_requires_target(self):
        with self.assertRaises(ValueError):
            Edit.from_dict({"op": "insert_after", "content": "x"})

    def test_replace_requires_target(self):
        with self.assertRaises(ValueError):
            Edit.from_dict({"op": "replace", "target": "", "content": "x"})

    def test_delete_requires_target(self):
        with self.assertRaises(ValueError):
            Edit.from_dict({"op": "delete", "target": ""})

    def test_invalid_source_type_raises(self):
        with self.assertRaises(ValueError):
            Edit.from_dict({"op": "append", "content": "x", "source_type": "unknown"})

    def test_support_count_coerced(self):
        e = Edit.from_dict({"op": "append", "content": "x", "support_count": "7"})
        self.assertEqual(e.support_count, 7)

    def test_support_count_bad_value_falls_back(self):
        e = Edit.from_dict({"op": "append", "content": "x", "support_count": "abc"})
        self.assertEqual(e.support_count, 1)


class TestApplyAppend(unittest.TestCase):
    def test_append_inserts_before_slow_update(self):
        # An append must not push the protected block off the bottom.
        edits = [Edit.from_dict({"op": "append", "content": "## New Section\n\nhello\n"})]
        new_text, report = apply_patch(SKILL_FIXTURE, edits)
        self.assertEqual(report.n_applied, 1)
        self.assertEqual(report.n_skipped, 0)
        # The appended content should appear BEFORE the SLOW_UPDATE block.
        idx_content = new_text.index("## New Section")
        idx_su = new_text.index(SLOW_UPDATE_START)
        self.assertLess(idx_content, idx_su)
        # The SLOW_UPDATE block must still be intact.
        self.assertTrue(has_slow_update_block(new_text))

    def test_append_when_no_slow_update_goes_at_end(self):
        text = "# Skill\n\nbody.\n"
        edits = [Edit.from_dict({"op": "append", "content": "tail rule"})]
        new_text, report = apply_patch(text, edits)
        self.assertEqual(report.n_applied, 1)
        self.assertTrue(new_text.endswith("tail rule"))


class TestApplyInsertAfter(unittest.TestCase):
    def test_inserts_immediately_after_target(self):
        edits = [
            Edit.from_dict(
                {
                    "op": "insert_after",
                    "target": "2. Tile at 224px / 0.5 mpp.",
                    "content": "\n2a. Confirm tile shape == (n, 3, 224, 224).",
                }
            )
        ]
        new_text, report = apply_patch(SKILL_FIXTURE, edits)
        self.assertEqual(report.n_applied, 1)
        self.assertIn("2a. Confirm tile shape", new_text)
        # The new line must come AFTER the target line.
        self.assertGreater(new_text.index("2a."), new_text.index("2. Tile"))


class TestApplyReplace(unittest.TestCase):
    def test_replace_changes_only_targeted_substring(self):
        edits = [
            Edit.from_dict(
                {
                    "op": "replace",
                    "target": "Embed with UNI.",
                    "content": "Embed with UNI2 by default; fall back to CTransPath if UNI2 weights are gated.",
                }
            )
        ]
        new_text, report = apply_patch(SKILL_FIXTURE, edits)
        self.assertEqual(report.n_applied, 1)
        self.assertNotIn("Embed with UNI.", new_text)
        self.assertIn("UNI2 by default", new_text)


class TestApplyDelete(unittest.TestCase):
    def test_delete_removes_target(self):
        edits = [Edit.from_dict({"op": "delete", "target": "- Confirm tile count > 0.\n"})]
        new_text, report = apply_patch(SKILL_FIXTURE, edits)
        self.assertEqual(report.n_applied, 1)
        self.assertNotIn("Confirm tile count > 0", new_text)


class TestSkipReasons(unittest.TestCase):
    def test_target_not_found(self):
        edits = [Edit.from_dict({"op": "replace", "target": "NONEXISTENT", "content": "x"})]
        _, report = apply_patch(SKILL_FIXTURE, edits)
        self.assertEqual(report.n_applied, 0)
        self.assertEqual(report.results[0].reason, "target_not_found")

    def test_target_ambiguous(self):
        text = "alpha beta alpha\n"
        edits = [Edit.from_dict({"op": "replace", "target": "alpha", "content": "ALPHA"})]
        _, report = apply_patch(text, edits)
        self.assertEqual(report.n_applied, 0)
        self.assertEqual(report.results[0].reason, "target_ambiguous")

    def test_target_inside_slow_update_blocked(self):
        edits = [Edit.from_dict({"op": "replace", "target": "<!-- protected -->", "content": "ha"})]
        _, report = apply_patch(SKILL_FIXTURE, edits)
        self.assertEqual(report.n_applied, 0)
        self.assertEqual(report.results[0].reason, "target_inside_slow_update_region")

    def test_replace_targeting_slow_update_delimiter_blocked(self):
        edits = [Edit.from_dict({"op": "delete", "target": SLOW_UPDATE_END})]
        _, report = apply_patch(SKILL_FIXTURE, edits)
        self.assertEqual(report.n_applied, 0)
        self.assertEqual(report.results[0].reason, "target_inside_slow_update_region")


class TestIndependenceRule(unittest.TestCase):
    def test_two_edits_overlapping_targets_skip_later(self):
        # Both target the SAME substring; the second must be rejected.
        edits = [
            Edit.from_dict({"op": "replace", "target": "Embed with UNI.", "content": "A"}),
            Edit.from_dict({"op": "delete", "target": "Embed with UNI."}),
        ]
        _, report = apply_patch(SKILL_FIXTURE, edits)
        self.assertEqual(report.n_applied, 1)
        self.assertEqual(report.results[1].reason, "overlaps_earlier_edit_target_region")

    def test_overlapping_substrings_partial_collision(self):
        # First targets "Embed with UNI.", second targets "with UNI." — overlapping spans.
        edits = [
            Edit.from_dict({"op": "replace", "target": "Embed with UNI.", "content": "A"}),
            Edit.from_dict({"op": "replace", "target": "with UNI.", "content": "B"}),
        ]
        _, report = apply_patch(SKILL_FIXTURE, edits)
        self.assertEqual(report.n_applied, 1)
        self.assertEqual(report.results[1].reason, "overlaps_earlier_edit_target_region")

    def test_disjoint_targets_both_apply(self):
        edits = [
            Edit.from_dict(
                {
                    "op": "insert_after",
                    "target": "Run an H&E analysis with lazyslide.",
                    "content": "\nUse omicdev conda env.",
                }
            ),
            Edit.from_dict(
                {
                    "op": "replace",
                    "target": "Embed with UNI.",
                    "content": "Embed with UNI2.",
                }
            ),
        ]
        _, report = apply_patch(SKILL_FIXTURE, edits)
        self.assertEqual(report.n_applied, 2)


class TestSequentialApplyAgainstEvolvingText(unittest.TestCase):
    def test_second_edit_targets_text_inserted_by_first(self):
        # First edit appends. Second edit targets the new content.
        edits = [
            Edit.from_dict({"op": "append", "content": "## EXTRA\n\nfoo bar."}),
            Edit.from_dict({"op": "replace", "target": "foo bar.", "content": "baz."}),
        ]
        new_text, report = apply_patch(SKILL_FIXTURE, edits)
        # The second edit DOES apply because at the time it runs, the text
        # already contains the appended content. (Independence check only
        # looked at the original text and saw no collision.)
        self.assertEqual(report.n_applied, 2)
        self.assertIn("baz.", new_text)

    def test_second_edit_blocked_by_earlier_delete(self):
        edits = [
            Edit.from_dict({"op": "delete", "target": "Embed with UNI."}),
            Edit.from_dict({"op": "replace", "target": "Embed with UNI.", "content": "X"}),
        ]
        _, report = apply_patch(SKILL_FIXTURE, edits)
        # Edit 0 applies (independence check accepted both because target
        # appeared once). Edit 1 then sees a text where the target is gone.
        # Independence pre-pass already rejected edit 1 for overlap, so
        # status is overlap; this also correctly prevents an apparent ghost
        # edit on text the first edit just deleted.
        self.assertEqual(report.n_applied, 1)
        self.assertEqual(report.results[1].reason, "overlaps_earlier_edit_target_region")


class TestPatchReportSerialization(unittest.TestCase):
    def test_report_json_round_trip(self):
        edits = [
            Edit.from_dict({"op": "append", "content": "foo"}),
            Edit.from_dict({"op": "replace", "target": "MISSING", "content": "x"}),
        ]
        _, report = apply_patch(SKILL_FIXTURE, edits)
        payload = json.loads(report.to_json())
        self.assertEqual(payload["n_edits"], 2)
        self.assertEqual(payload["n_applied"], 1)
        self.assertEqual(payload["n_skipped"], 1)
        self.assertEqual(payload["results"][1]["reason"], "target_not_found")


class TestJsonIngress(unittest.TestCase):
    def test_analyst_stage_shape(self):
        payload = {
            "batch_size": 8,
            "failure_summary": [],
            "patch": {
                "reasoning": "test",
                "edits": [
                    {"op": "append", "content": "rule A"},
                    {
                        "op": "replace",
                        "target": "Embed with UNI.",
                        "content": "Embed with UNI2.",
                    },
                ],
            },
        }
        edits = edits_from_optimizer_json(payload)
        self.assertEqual(len(edits), 2)
        self.assertEqual(edits[0].op, "append")
        self.assertEqual(edits[1].op, "replace")

    def test_merge_stage_shape(self):
        payload = {
            "reasoning": "merged",
            "edits": [
                {
                    "op": "delete",
                    "target": "Embed with UNI.",
                    "support_count": 4,
                    "source_type": "failure",
                }
            ],
        }
        edits = edits_from_optimizer_json(payload)
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0].support_count, 4)
        self.assertEqual(edits[0].source_type, "failure")

    def test_bad_op_propagates(self):
        payload = {"edits": [{"op": "rewrite_everything", "content": "..."}]}
        with self.assertRaises(ValueError):
            edits_from_optimizer_json(payload)


if __name__ == "__main__":
    unittest.main()
