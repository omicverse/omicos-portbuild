"""Verify the kit runs on a fresh checkout with no omicos-admin nearby.

A second user clones this repo into `/somewhere/omicos-portbuild`,
exports OMICOS_PYTHON_ENV, and runs the loop. They have no admin checkout.

The expected behavior:
  - smoke_test passes with a warn on OMICOS_ADMIN_ROOT (not a hard fail)
  - discovery.run() with a missing admin path proceeds (no duplicates found)
  - route.resolve_members() returns []
  - publisher still writes to targets/<id>/published/ successfully
  - the full pipeline never touches a nonexistent admin path

This file exercises each of those without spinning up any LLM.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import discovery, publisher, route, smoke_test  # noqa: E402


_NONEXISTENT_ADMIN = "/this/path/does/not/exist/omicos-admin"


class TestSmokeStandalone(unittest.TestCase):
    def test_admin_check_warn_only_when_missing(self):
        with patch.dict(os.environ,
                        {"OMICOS_ADMIN_ROOT": _NONEXISTENT_ADMIN},
                        clear=False):
            results, hard_fail, warn = smoke_test.run_all()
            admin_results = [r for r in results if "ADMIN_ROOT" in r.name]
            self.assertEqual(len(admin_results), 1)
            admin = admin_results[0]
            self.assertTrue(admin.warn_only,
                            "OMICOS_ADMIN_ROOT must be warn-only so standalone "
                            "use is not blocked")
            self.assertFalse(admin.ok)
            # The remediation hint must say standalone is fine.
            self.assertIn("standalone", admin.note.lower())


class TestDiscoveryStandalone(unittest.TestCase):
    def test_discovery_with_missing_admin_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "targets" / "novel_cap"
            target.mkdir(parents=True)
            result = discovery.run(
                capability_id="novel_cap",
                package="newpkg",
                source=None,
                docs=None,
                admin_root=_NONEXISTENT_ADMIN,
                target_dir=target,
            )
            self.assertFalse(result["stop"],
                             "missing admin must not trigger STOP")
            self.assertEqual(result["existing_agents"], [])
            self.assertEqual(result["existing_skills"], [])
            self.assertFalse(result["admin_root_exists"])
            # DISCOVERY.md is still written (for the audit trail).
            md = (target / "DISCOVERY.md").read_text(encoding="utf-8")
            self.assertIn("novel_cap", md)


class TestRouteStandalone(unittest.TestCase):
    def test_resolve_members_empty_when_admin_missing(self):
        members = route.resolve_members(
            domain="spatial-he",
            members_arg="auto",
            admin_root=_NONEXISTENT_ADMIN,
        )
        self.assertEqual(members, [])

    def test_resolve_members_works_with_explicit_ids_even_without_admin(self):
        # An operator can build a router from a known list even if they
        # haven't yet copied any agents into admin.
        members = route.resolve_members(
            domain="spatial-he",
            members_arg="he_to_st_predictor,pathology_lazyslide_analyst",
            admin_root=_NONEXISTENT_ADMIN,
        )
        self.assertEqual([m["id"] for m in members],
                         ["he_to_st_predictor", "pathology_lazyslide_analyst"])


class TestPublishStandalone(unittest.TestCase):
    """The publisher already lands inside the target dir, so missing admin
    must not change anything. This is the safety-net test."""

    def setUp(self):
        from tests.test_publisher import _build_best  # reuse fixture
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-stand-"))
        self.target = self.tmp / "targets" / "pathology_lazyslide_he_analysis"
        _build_best(self.target)

    def test_publish_without_admin_root_anywhere(self):
        with patch.dict(os.environ,
                        {"OMICOS_ADMIN_ROOT": _NONEXISTENT_ADMIN},
                        clear=False):
            result = publisher.run(target_dir=self.target)
        self.assertTrue(result["published"], msg=result.get("issues"))
        self.assertIn("published", result["out_dir"])
        # The published bundle landed under the target, not anywhere near
        # the (nonexistent) admin path.
        self.assertNotIn("omicos-admin", result["out_dir"])
        # Bundle is just files; the published agent.md exists.
        # (No HOW_TO_INSTALL — deploy is the operator's concern.)
        self.assertTrue((self.target / "published" / "agents" /
                         f"{result['agent_id']}.md").exists())


if __name__ == "__main__":
    unittest.main()
