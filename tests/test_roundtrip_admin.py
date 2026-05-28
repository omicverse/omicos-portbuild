"""Round-trip: publish a bundle → cp into fake omicos-admin → verify
admin's schema validator (our own publisher_adapter.validate_skill_for_publish
running in 'admin-side' mode) accepts it.

This catches drift between portbuild's output shape and the admin-side
contract — e.g. if portbuild starts emitting a frontmatter field admin
doesn't expect, this test breaks.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import draft, publisher  # noqa: E402
from factory.manifest import write_initial_manifest  # noqa: E402
from factory.publisher_adapter import (  # noqa: E402
    adapt_agent,
    validate_skill_for_publish,
)


_AGENT_BODY = """---
icon: 🧪
id: pkg_omni
name: Pkg Omni
title: Test omni
description: Use when running this on H&E slides.
category: spatial_analysis
category_order: 100
tier: pro
summary: x
use_when: |
  trigger
example_prompts: []
example_inputs: []
toolsets:
  - file_manager
skills:
  - skill-a
domain: spatial-he
---

You are the omni agent.

## Delivery contract

Standard contract.
"""


_SKILL_BODY = """---
id: skill-a
name: skill-a
title: Test skill
description: Use when running this skill on H&E slides.
category: spatial_analysis
category_order: 100
tier: pro
summary: x
use_when: H&E direct analysis.
---

# Test Skill

## Goal

Demo.

## Quick Workflow

1. Step one — calls `foo`.
2. Step two — calls `bar`.

## Branches: foo(model)

Branches.

## Validation

- check_a passes.
- Tested against pkg>=0.9.

## Smoke testing

```python
import pkg
```

References: references/source-grounding.md.
Reusable capability-first skill.
delivery contract: outputs/REPORT.html + handoff + next_agent_hints.
huggingface gate.

<!-- SLOW_UPDATE_START -->
<!-- protected -->
<!-- SLOW_UPDATE_END -->
"""


class TestRoundTrip(unittest.TestCase):
    """Publish a skill, copy into fake admin/, validate admin-side."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-rt-"))
        # Set up: pkg_root with a skill and an agent
        self.root = self.tmp / "as-pkg"
        # Skill target
        self.skill_target = self.root / "skills" / "skill-a"
        write_initial_manifest(
            target_dir=self.skill_target,
            capability_id="skill-a", kind="skill",
            package="pkg", source=None, docs=None,
            notebooks=[], cost_profile="moderate",
            extra={"key_apis": [{"name": "pkg.foo"}, {"name": "pkg.bar"}],
                   "domain": "spatial-he"},
        )
        draft.run(self.skill_target)
        v000 = self.skill_target / "candidates" / "v000"
        (v000 / "skill" / "SKILL.md").write_text(_SKILL_BODY, encoding="utf-8")
        # Fully clear FILLs in sibling assets/references so promote allows.
        (v000 / "skill" / "assets" / "acceptance.json").write_text(
            '{"version":1,"skill":"skill-a","sample_requests":["x"],'
            '"required_description_terms":[],"required_sections":[],'
            '"required_body_terms":[],"required_paths":[],"smoke_commands":[],'
            '"validation_budget":{"smoke_mode":"skip","max_wall_seconds":0,"skip_reason":"unit test"}}',
            encoding="utf-8",
        )
        (v000 / "skill" / "references" / "source-grounding.md").write_text(
            "# Source Grounding — test\n\nReal content.\n", encoding="utf-8",
        )
        draft.cmd_promote(self.skill_target)

        # Agent target
        self.agent_target = self.root / "agents" / "pkg_omni"
        write_initial_manifest(
            target_dir=self.agent_target,
            capability_id="pkg_omni", kind="agent",
            package="pkg", source=None, docs=None,
            notebooks=[], cost_profile="moderate",
            extra={"owns_skills": ["skill-a"], "domain": "spatial-he"},
        )
        draft.run(self.agent_target)
        (self.agent_target / "candidates" / "v000" / "agent.md").write_text(
            _AGENT_BODY, encoding="utf-8"
        )
        # routing_metadata.yaml may have FILL for domain — overwrite cleanly
        (self.agent_target / "candidates" / "v000" / "routing_metadata.yaml").write_text(
            "capability_id: pkg_omni\nkind: agent\ndomain: spatial-he\nrole: terminal\nowns_skills:\n  - skill-a\n",
            encoding="utf-8",
        )
        draft.cmd_promote(self.agent_target)

        # Fake admin
        self.fake_admin = self.tmp / "fake-admin"
        (self.fake_admin / "agents").mkdir(parents=True)
        (self.fake_admin / "skills").mkdir()

    def test_skill_publish_to_fake_admin_validates(self):
        # publish skill
        result = publisher.run(target_dir=self.skill_target)
        self.assertTrue(result["published"], msg=result.get("issues"))
        skill_pub = self.skill_target / "published" / "skills" / "skill-a"
        # cp into fake admin
        dst = self.fake_admin / "skills" / "skill-a"
        shutil.copytree(skill_pub, dst)
        # Admin-side validation: same schema check
        admin_issues = validate_skill_for_publish(
            dst, key_apis=[{"name": "pkg.foo"}, {"name": "pkg.bar"}]
        )
        self.assertEqual(admin_issues, [],
                         msg=f"admin-side validation failed: {admin_issues}")

    def test_agent_bundle_publish_to_fake_admin_validates(self):
        # publish skill first (agent bundle depends on sibling skill being published)
        skill_result = publisher.run(target_dir=self.skill_target)
        self.assertTrue(skill_result["published"], msg=skill_result.get("issues"))

        # publish agent bundle
        agent_result = publisher.run(target_dir=self.agent_target)
        self.assertTrue(agent_result["published"], msg=agent_result.get("issues"))
        agent_pub = self.agent_target / "published"
        # cp agent + skills into fake admin
        agent_md = agent_pub / "agents" / "pkg_omni.md"
        shutil.copy2(agent_md, self.fake_admin / "agents" / "pkg_omni.md")
        for skill_dir in (agent_pub / "skills").iterdir():
            shutil.copytree(skill_dir, self.fake_admin / "skills" / skill_dir.name)

        # Admin-side checks:
        # 1. agent frontmatter parses + has required fields
        text = (self.fake_admin / "agents" / "pkg_omni.md").read_text(encoding="utf-8")
        adapted, meta = adapt_agent(text)
        self.assertEqual(meta["id"], "pkg_omni")
        self.assertIn("skill-a", meta["skills"])
        # 2. each skill validates against its own key_apis
        admin_issues = validate_skill_for_publish(
            self.fake_admin / "skills" / "skill-a",
            key_apis=[{"name": "pkg.foo"}, {"name": "pkg.bar"}],
        )
        self.assertEqual(admin_issues, [])

    def test_published_paths_do_not_contain_portbuild_machine_paths(self):
        # Bundle should never embed /scratch/.../portbuild/ paths
        publisher.run(target_dir=self.skill_target)
        skill_md = (self.skill_target / "published" / "skills" / "skill-a" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("/scratch/users/", skill_md)
        self.assertNotIn("/Users/", skill_md)


if __name__ == "__main__":
    unittest.main()
