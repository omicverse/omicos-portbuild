"""Publisher tests — confirm the kit never writes into omicos-admin.

Two-axis verification:
  1. Output bundle lands under `targets/<id>/published/` (or `--out`),
     in admin-shape layout.
  2. Even if the caller TRIES to point `--out` at a path containing
     'omicos-admin', the publisher refuses.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import publisher  # noqa: E402


_GOOD_AGENT = """---
id: pathology_lazyslide_he_analysis
name: pathology_lazyslide_he_analysis
title: H&E direct analysis with lazyslide
description: Direct H&E whole-slide analysis using lazyslide.
category: spatial_analysis
category_order: 30
tier: pro
summary: lazyslide-driven H&E report (no transcriptomic prediction).
use_when: |
  Pure H&E morphology or zero-shot tissue typing.
domain: spatial-he
---

You are the lazyslide H&E analyst.
"""

_GOOD_SKILL = """---
id: pathology-lazyslide-he-analysis
name: pathology-lazyslide-he-analysis
title: Direct H&E analysis (lazyslide)
description: Real description. Use when running lazyslide on H&E.
category: spatial_analysis
category_order: 30
tier: pro
summary: lazyslide H&E report.
use_when: H&E direct analysis.
---

# Lazyslide H&E

## Goal

Run lazyslide on an H&E whole-slide image.

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


def _build_best(target_dir: Path) -> None:
    best = target_dir / "best"
    (best / "skill" / "references").mkdir(parents=True, exist_ok=True)
    (best / "skill" / "assets").mkdir(exist_ok=True)
    (best / "agent.md").write_text(_GOOD_AGENT, encoding="utf-8")
    (best / "skill" / "SKILL.md").write_text(_GOOD_SKILL, encoding="utf-8")
    (best / "skill" / "assets" / "acceptance.json").write_text(
        '{"version": 1, "skill": "pathology-lazyslide-he-analysis", '
        '"sample_requests": ["x"], "validation_budget": '
        '{"smoke_mode": "skip", "max_wall_seconds": 0, "skip_reason": "test"}}',
        encoding="utf-8",
    )


class TestPublisherProducesAdminShapeBundle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-pub-"))
        self.target = self.tmp / "targets" / "pathology_lazyslide_he_analysis"
        _build_best(self.target)

    def test_publish_defaults_to_published_under_target(self):
        result = publisher.run(target_dir=self.target)
        self.assertTrue(result["published"], msg=result.get("issues"))
        self.assertEqual(result["agent_id"], "pathology_lazyslide_he_analysis")
        self.assertEqual(result["skill_id"], "pathology-lazyslide-he-analysis")
        self.assertEqual(result["domain"], "spatial-he")

        out = self.target / "published"
        self.assertTrue(out.exists())
        # Admin-shape layout: agents/<id>.md + skills/<skill_id>/
        self.assertTrue((out / "agents" / "pathology_lazyslide_he_analysis.md").exists())
        self.assertTrue((out / "skills" / "pathology-lazyslide-he-analysis" / "SKILL.md").exists())
        # Bundle is just files — no install hints, no readme.
        self.assertFalse((out / "HOW_TO_INSTALL.md").exists())

    def test_publish_to_custom_out(self):
        custom = self.tmp / "elsewhere"
        result = publisher.run(target_dir=self.target, out_dir=custom)
        self.assertTrue(result["published"])
        self.assertTrue((custom / "agents" / "pathology_lazyslide_he_analysis.md").exists())

    def test_publish_refuses_to_write_into_omicos_admin(self):
        # Even when the caller explicitly asks, we refuse.
        admin_like = self.tmp / "fake-checkouts" / "omicos-admin"
        with self.assertRaises(RuntimeError) as ctx:
            publisher.run(target_dir=self.target, out_dir=admin_like)
        self.assertIn("refusing", str(ctx.exception).lower())
        self.assertFalse(admin_like.exists())

    def test_published_again_overwrites_cleanly(self):
        publisher.run(target_dir=self.target)
        # Drop a stale file inside the published folder; second run must
        # remove it.
        stale = self.target / "published" / "agents" / "stale.md"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("old", encoding="utf-8")
        publisher.run(target_dir=self.target)
        self.assertFalse(stale.exists())


class TestPublisherDoesNotTouchAnyAdminCheckout(unittest.TestCase):
    """End-to-end paranoia: spin up a fake omicos-admin/, run publish, confirm
    nothing inside it changed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-pub-paranoia-"))
        self.target = self.tmp / "targets" / "pathology_lazyslide_he_analysis"
        _build_best(self.target)
        self.fake_admin = self.tmp / "omicos-admin"
        (self.fake_admin / "agents").mkdir(parents=True)
        (self.fake_admin / "skills").mkdir()
        # Sentinel file we will verify is unchanged.
        sentinel = self.fake_admin / "agents" / "_sentinel.md"
        sentinel.write_text("DO NOT TOUCH", encoding="utf-8")
        self._sentinel = sentinel
        self._sentinel_mtime = sentinel.stat().st_mtime_ns

    def test_publish_does_not_modify_admin(self):
        publisher.run(target_dir=self.target)
        # The fake admin must be untouched.
        self.assertEqual(self._sentinel.read_text(encoding="utf-8"), "DO NOT TOUCH")
        self.assertEqual(self._sentinel.stat().st_mtime_ns, self._sentinel_mtime)
        # And no new files appeared.
        files_in_admin = list(self.fake_admin.rglob("*"))
        # Three entries: agents/, skills/, agents/_sentinel.md
        names = sorted(p.name for p in files_in_admin)
        self.assertEqual(names, ["_sentinel.md", "agents", "skills"])


if __name__ == "__main__":
    unittest.main()
