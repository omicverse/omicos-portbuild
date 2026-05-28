"""Verify draft init auto-pre-fills Quick Workflow from SOURCE_GROUNDING.md."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import draft  # noqa: E402
from factory.manifest import write_initial_manifest  # noqa: E402


_GROUNDING_MD = """# Source Grounding — pkg-skill

Auto-inspected.

## `pkg.tools`

| Name | Kind | Signature | Summary |
|---|---|---|---|
| `feature_extraction` | function | `(wsi, model)` | Extract features |
| `feature_aggregation` | function | `(wsi, key)` | Aggregate |
| `RNALinker` | class | `(agg, others)` | Link to RNA |
"""


class TestDraftSkeletonFromGrounding(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-skel-"))
        self.target = self.tmp / "as-x" / "skills" / "skill-a"
        write_initial_manifest(
            target_dir=self.target,
            capability_id="skill-a", kind="skill",
            package="pkg", source=None, docs=None, notebooks=[],
            cost_profile="moderate",
        )

    def test_no_grounding_falls_back_to_FILL(self):
        draft.run(self.target)
        body = (self.target / "candidates" / "v000" / "skill" / "SKILL.md").read_text()
        # The generic FILL is still there
        self.assertIn("<FILL: 4-8 ordered steps", body)

    def test_grounding_present_prefills_quick_workflow(self):
        (self.target / "SOURCE_GROUNDING.md").write_text(_GROUNDING_MD, encoding="utf-8")
        draft.run(self.target)
        body = (self.target / "candidates" / "v000" / "skill" / "SKILL.md").read_text()
        # The generic FILL replaced by per-API placeholders
        self.assertNotIn("<FILL: 4-8 ordered steps", body)
        self.assertIn("`feature_extraction`(...)", body)
        self.assertIn("`feature_aggregation`(...)", body)
        self.assertIn("`RNALinker`(...)", body)


if __name__ == "__main__":
    unittest.main()
