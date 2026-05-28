"""Verify Phase 3 produces BOTH agent.md and skill/SKILL.md with FILL markers
that the publisher refuses, until the user (or Claude Code in conversation)
fills them in.
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import draft, publisher  # noqa: E402
from factory.manifest import write_initial_manifest  # noqa: E402
from factory.patcher import has_slow_update_block  # noqa: E402


def _bootstrap(tmp: Path) -> Path:
    target = tmp / "targets" / "pathology_lazyslide_he_analysis"
    write_initial_manifest(
        target_dir=target,
        capability_id="pathology_lazyslide_he_analysis",
        kind="capability",
        package="lazyslide",
        source="https://github.com/RendeiroLab/LazySlide",
        docs="https://lazyslide.readthedocs.io/en/latest/",
        notebooks=[],
        cost_profile="gpu-required",
        extra={"domain": "spatial-he"},
    )
    return target


class TestDraftProducesAgentAndSkillTogether(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-draft-"))
        self.target = _bootstrap(self.tmp)

    def test_v000_has_both_artifacts(self):
        draft.run(self.target)
        v000 = self.target / "candidates" / "v000"
        agent = v000 / "agent.md"
        skill_md = v000 / "skill" / "SKILL.md"
        acceptance = v000 / "skill" / "assets" / "acceptance.json"

        self.assertTrue(agent.exists(), "agent.md must be produced alongside skill")
        self.assertTrue(skill_md.exists(), "SKILL.md must be produced")
        self.assertTrue(acceptance.exists(), "acceptance.json must be produced")

        # Both share the capability_id / skill_id link via frontmatter.
        agent_text = agent.read_text(encoding="utf-8")
        self.assertIn("id: pathology_lazyslide_he_analysis", agent_text)
        self.assertIn("- pathology-lazyslide-he-analysis", agent_text)  # in skills:

        # Domain from manifest landed in agent frontmatter.
        self.assertIn("domain: spatial-he", agent_text)

    def test_skill_md_includes_slow_update_block_at_v000(self):
        draft.run(self.target)
        text = (self.target / "candidates" / "v000" / "skill" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertTrue(has_slow_update_block(text),
                        "SkillOpt invariant: v000 must already carry SLOW_UPDATE")

    def test_draft_writes_draft_instructions(self):
        draft.run(self.target)
        instr = (self.target / "DRAFT_INSTRUCTIONS.md").read_text(encoding="utf-8")
        self.assertIn("agent.md", instr)
        self.assertIn("skill/SKILL.md", instr)
        self.assertIn("factory.draft check", instr)


class TestFillMarkerCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-draft-"))
        self.target = _bootstrap(self.tmp)
        draft.run(self.target)

    def test_v000_has_unresolved_fills(self):
        fills = draft.find_fill_markers(self.target)
        self.assertGreater(len(fills), 0,
                           "fresh v000 should have <FILL:> markers in both agent.md and SKILL.md")
        # Both files must show up.
        agent_paths = [p for p in fills if p.endswith("agent.md")]
        skill_paths = [p for p in fills if p.endswith("SKILL.md")]
        self.assertEqual(len(agent_paths), 1)
        self.assertEqual(len(skill_paths), 1)

    def test_check_cli_returns_nonzero_when_fills_remain(self):
        rc = draft.cmd_check(self.target)
        self.assertEqual(rc, 1)

    def test_check_cli_returns_zero_when_all_fills_resolved(self):
        # Replace every <FILL: …> with a concrete value across all v000 files.
        v000 = self.target / "candidates" / "v000"
        for f in (
            v000 / "agent.md",
            v000 / "skill" / "SKILL.md",
            v000 / "skill" / "assets" / "acceptance.json",
            v000 / "routing_metadata.yaml",
        ):
            if not f.exists():
                continue
            text = f.read_text(encoding="utf-8")
            text = re.sub(r"<FILL:\s*[^>]+>", "REAL_VALUE", text)
            f.write_text(text, encoding="utf-8")

        rc = draft.cmd_check(self.target)
        self.assertEqual(rc, 0)


class TestPublisherRefusesUnfilledDraft(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-draft-pub-"))
        self.target = _bootstrap(self.tmp)
        draft.run(self.target)
        # Mimic optimizer.run's "copy v000 to best" placeholder so publisher
        # has something to act on.
        from shutil import copytree, copy2
        best = self.target / "best"
        v000 = self.target / "candidates" / "v000"
        copy2(v000 / "agent.md", best.with_name("best").joinpath("agent.md")) if False else None
        best.mkdir(exist_ok=True)
        copy2(v000 / "agent.md", best / "agent.md")
        copytree(v000 / "skill", best / "skill")

    def test_publisher_rejects_fill_markers_in_agent(self):
        with self.assertRaises(ValueError) as ctx:
            publisher.run(target_dir=self.target)
        self.assertIn("FILL", str(ctx.exception))

    def test_publisher_succeeds_after_user_fills_in_everything(self):
        # Replace ALL <FILL:> markers + remove "TODO" in source-grounding.
        best = self.target / "best"
        for f in (best / "agent.md", best / "skill" / "SKILL.md"):
            text = f.read_text(encoding="utf-8")
            text = re.sub(r"<FILL:\s*[^>]+>", "REAL_VALUE", text)
            text = text.replace("TODO", "DONE")
            f.write_text(text, encoding="utf-8")
        # The source-grounding placeholder uses 'TODO' too; replace.
        sg = best / "skill" / "references" / "source-grounding.md"
        if sg.exists():
            sg.write_text(sg.read_text(encoding="utf-8").replace("TODO", "see PARTITION.md"),
                          encoding="utf-8")

        result = publisher.run(target_dir=self.target)
        self.assertTrue(result["published"], msg=result.get("issues"))
        self.assertTrue((self.target / "published" / "agents" /
                         "pathology_lazyslide_he_analysis.md").exists())
        self.assertTrue((self.target / "published" / "skills" /
                         "pathology-lazyslide-he-analysis" / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
