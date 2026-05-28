"""Verify validate_skill_for_publish rejects skills whose body doesn't
reference enough of their manifest.key_apis. This is the Round 4 bug:
batch helper pasted feature_extraction example into 5 wrong skills.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory.publisher_adapter import (  # noqa: E402
    _extract_api_basenames,
    validate_skill_for_publish,
)


_SKILL_HE_ANALYSIS = """---
id: pathology-lazyslide-he-analysis
name: pathology-lazyslide-he-analysis
title: x
description: x
---

# x

## Goal

x

## Quick Workflow

1. open_wsi the slide.
2. zs.pp.find_tissues(wsi).
3. zs.pp.tile_tissues(wsi, 256, mpp=0.5).
4. zs.tl.feature_extraction(wsi, 'uni2').
5. zs.tl.feature_aggregation(wsi, 'uni2').
6. zs.tl.zero_shot_score(wsi, classes, feature_key='virchow_tiles').

## Validation

- okay.

<!-- SLOW_UPDATE_START -->
<!-- protected -->
<!-- SLOW_UPDATE_END -->
"""


_SKILL_CELL_SEG_WRONG = """---
id: pathology-lazyslide-cell-segmentation
name: pathology-lazyslide-cell-segmentation
title: x
description: x
---

# x

## Goal

x

## Quick Workflow

1. open_wsi the slide.
2. zs.pp.find_tissues(wsi).
3. zs.pp.tile_tissues(wsi, 256, mpp=0.5).
4. zs.tl.feature_extraction(wsi, 'uni2').
5. zs.tl.feature_aggregation(wsi, 'uni2').

## Validation

- okay.

<!-- SLOW_UPDATE_START -->
<!-- protected -->
<!-- SLOW_UPDATE_END -->
"""


HE_KEY_APIS = [
    {"name": "wsidata.open_wsi", "purpose": "..."},
    {"name": "lazyslide.preprocess.find_tissues", "purpose": "..."},
    {"name": "lazyslide.preprocess.tile_tissues", "purpose": "..."},
    {"name": "lazyslide.tools.feature_extraction", "purpose": "..."},
    {"name": "lazyslide.tools.feature_aggregation", "purpose": "..."},
    {"name": "lazyslide.tools.zero_shot_score", "purpose": "..."},
]

CELL_SEG_KEY_APIS = [
    {"name": "wsidata.open_wsi", "purpose": "..."},
    {"name": "lazyslide.preprocess.find_tissues", "purpose": "..."},
    {"name": "lazyslide.segmentation.cells", "purpose": "..."},
    {"name": "lazyslide.segmentation.cell_types", "purpose": "..."},
    {"name": "lazyslide.segmentation.tissue", "purpose": "..."},
    {"name": "lazyslide.segmentation.artifact", "purpose": "..."},
]


def _build_skill_dir(tmp: Path, body: str) -> Path:
    d = tmp / "skill"
    (d / "references").mkdir(parents=True)
    (d / "assets").mkdir()
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


class TestApiBasenameExtraction(unittest.TestCase):
    def test_dotted_path(self):
        names = _extract_api_basenames(
            [{"name": "lazyslide.tools.feature_extraction"}]
        )
        self.assertEqual(names, ["feature_extraction"])

    def test_call_form_stripped(self):
        names = _extract_api_basenames(
            [{"name": "zs.tl.feature_extraction(wsi, 'uni2')"}]
        )
        self.assertEqual(names, ["feature_extraction"])

    def test_plain_string(self):
        names = _extract_api_basenames(["wsidata.open_wsi"])
        self.assertEqual(names, ["open_wsi"])

    def test_deduplicates(self):
        names = _extract_api_basenames(
            [{"name": "zs.tl.foo"}, {"name": "zs.tools.foo(x)"}]
        )
        self.assertEqual(names, ["foo"])


class TestSkillBelongsToOwnCapability(unittest.TestCase):
    """The actual bug: a SKILL.md for cell_segmentation that contains the
    he-analysis worked example should FAIL grounding."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-grounding-"))

    def test_he_analysis_passes_against_its_own_key_apis(self):
        d = _build_skill_dir(self.tmp, _SKILL_HE_ANALYSIS)
        issues = validate_skill_for_publish(d, key_apis=HE_KEY_APIS)
        # Should pass — 6/6 APIs referenced
        grounding_issues = [i for i in issues if "key_apis" in i]
        self.assertEqual(grounding_issues, [])

    def test_cell_seg_wrong_body_fails_grounding(self):
        # This is exactly the Round 4 bug: cell_segmentation's body uses
        # feature_extraction (he-analysis's API), not seg.cells.
        d = _build_skill_dir(self.tmp, _SKILL_CELL_SEG_WRONG)
        issues = validate_skill_for_publish(d, key_apis=CELL_SEG_KEY_APIS)
        # Should fail: only open_wsi + find_tissues match (2/6 = 33%)
        # The missing should include cells, cell_types, tissue, artifact.
        grounding_issues = [i for i in issues if "key_apis" in i]
        self.assertEqual(len(grounding_issues), 1)
        msg = grounding_issues[0]
        self.assertIn("cells", msg)
        self.assertIn("cell_types", msg)
        self.assertIn("artifact", msg)

    def test_no_key_apis_supplied_no_grounding_check(self):
        d = _build_skill_dir(self.tmp, _SKILL_CELL_SEG_WRONG)
        issues = validate_skill_for_publish(d, key_apis=None)
        grounding_issues = [i for i in issues if "key_apis" in i]
        self.assertEqual(grounding_issues, [])


if __name__ == "__main__":
    unittest.main()
