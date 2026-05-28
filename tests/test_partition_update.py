"""Tests for partition.cmd_update — non-destructive merge of partition.json
changes into existing per-target manifests.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import partition  # noqa: E402
from factory.manifest import read_manifest, write_initial_manifest  # noqa: E402


def _bootstrap(root: Path, capabilities, agents=None):
    """Write a package root with manifest + partition.json + initial emit."""
    write_initial_manifest(
        target_dir=root, capability_id="pkg_root", kind="capability",
        package="lazyslide", source="https://github.com/x", docs="https://x.docs",
        notebooks=[], cost_profile="moderate",
    )
    (root / "partition.json").write_text(json.dumps({
        "package": "lazyslide",
        "source": "https://github.com/x",
        "docs": "https://x.docs",
        "reasoning": "x",
        "capabilities": capabilities,
        "agents": agents or [],
        "pedagogy_only_tutorials": [],
    }), encoding="utf-8")
    partition.cmd_emit(root)


class TestPartitionUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-update-"))
        self.root = self.tmp / "pkg"

    def test_add_new_key_api_to_existing_skill(self):
        initial = [{
            "id": "skill-a", "title": "x", "tutorials": ["t1"],
            "key_modules": ["pkg.tools"],
            "key_apis": [{"name": "pkg.tools.foo", "purpose": "..."}],
            "distinct_from": "x", "domain": "d", "cost_profile": "moderate",
        }]
        _bootstrap(self.root, initial)

        # User edits partition.json — adds a new API
        pj = json.loads((self.root / "partition.json").read_text())
        pj["capabilities"][0]["key_apis"].append(
            {"name": "pkg.tools.bar", "purpose": "newly discovered"}
        )
        (self.root / "partition.json").write_text(json.dumps(pj), encoding="utf-8")

        # Apply update
        changes = partition.cmd_update(self.root)
        self.assertEqual(len(changes["skills_with_apis_added"]), 1)
        delta = changes["skills_with_apis_added"][0]
        self.assertEqual(delta["id"], "skill-a")
        self.assertIn("pkg.tools.bar", delta["added"])

        # Verify manifest was rewritten with the new API
        m = read_manifest(self.root / "skills" / "skill-a")
        names = [a["name"] for a in m.extra["key_apis"]]
        self.assertIn("pkg.tools.bar", names)

    def test_add_new_skill_creates_subdir(self):
        initial = [{
            "id": "skill-a", "title": "x", "tutorials": ["t1"],
            "key_modules": ["pkg.tools"],
            "key_apis": [{"name": "pkg.tools.foo", "purpose": "..."}],
            "distinct_from": "x", "domain": "d", "cost_profile": "moderate",
        }]
        _bootstrap(self.root, initial)

        # Add a second capability
        pj = json.loads((self.root / "partition.json").read_text())
        pj["capabilities"].append({
            "id": "skill-b", "title": "y", "tutorials": ["t2"],
            "key_modules": ["pkg.seg"],
            "key_apis": [{"name": "pkg.seg.bar", "purpose": "..."}],
            "distinct_from": "y", "domain": "d", "cost_profile": "moderate",
        })
        (self.root / "partition.json").write_text(json.dumps(pj), encoding="utf-8")

        changes = partition.cmd_update(self.root)
        self.assertIn("skill-b", changes["skills_added"])
        self.assertTrue((self.root / "skills" / "skill-b" / "manifest.yaml").exists())

    def test_remove_api_refused_without_force(self):
        initial = [{
            "id": "skill-a", "title": "x", "tutorials": ["t1"],
            "key_modules": ["pkg.tools"],
            "key_apis": [{"name": "pkg.tools.foo"}, {"name": "pkg.tools.bar"}],
            "distinct_from": "x", "domain": "d", "cost_profile": "moderate",
        }]
        _bootstrap(self.root, initial)

        pj = json.loads((self.root / "partition.json").read_text())
        pj["capabilities"][0]["key_apis"] = [{"name": "pkg.tools.foo"}]
        (self.root / "partition.json").write_text(json.dumps(pj), encoding="utf-8")

        changes = partition.cmd_update(self.root, force=False)
        self.assertTrue(changes["refused_without_force"])
        # Manifest still has bar
        m = read_manifest(self.root / "skills" / "skill-a")
        names = [a["name"] for a in m.extra["key_apis"]]
        self.assertIn("pkg.tools.bar", names)

    def test_remove_api_with_force_applies(self):
        initial = [{
            "id": "skill-a", "title": "x", "tutorials": ["t1"],
            "key_modules": ["pkg.tools"],
            "key_apis": [{"name": "pkg.tools.foo"}, {"name": "pkg.tools.bar"}],
            "distinct_from": "x", "domain": "d", "cost_profile": "moderate",
        }]
        _bootstrap(self.root, initial)

        pj = json.loads((self.root / "partition.json").read_text())
        pj["capabilities"][0]["key_apis"] = [{"name": "pkg.tools.foo"}]
        (self.root / "partition.json").write_text(json.dumps(pj), encoding="utf-8")

        partition.cmd_update(self.root, force=True)
        m = read_manifest(self.root / "skills" / "skill-a")
        names = [a["name"] for a in m.extra["key_apis"]]
        self.assertNotIn("pkg.tools.bar", names)
        self.assertIn("pkg.tools.foo", names)


if __name__ == "__main__":
    unittest.main()
