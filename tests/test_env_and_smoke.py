"""Tests for env-var resolution + smoke-test framework.

Avoids assumptions about the user's machine: we monkey-patch os.environ
through unittest.mock.patch.dict and never assume conda envs exist.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import env as env_mod  # noqa: E402
from factory import smoke_test  # noqa: E402


class TestResolutionOrder(unittest.TestCase):
    def test_env_var_wins_over_manifest_and_default(self):
        with patch.dict(os.environ, {"OMICOS_PYTHON_ENV": "from_env"}, clear=False):
            self.assertEqual(env_mod.python_env("from_manifest"), "from_env")

    def test_manifest_wins_when_env_var_unset(self):
        # Unset the env var explicitly.
        env_copy = {k: v for k, v in os.environ.items() if k != "OMICOS_PYTHON_ENV"}
        with patch.dict(os.environ, env_copy, clear=True):
            self.assertEqual(env_mod.python_env("from_manifest"), "from_manifest")

    def test_default_wins_when_env_and_manifest_unset(self):
        env_copy = {k: v for k, v in os.environ.items() if k != "OMICOS_PYTHON_ENV"}
        with patch.dict(os.environ, env_copy, clear=True):
            self.assertEqual(env_mod.python_env(), "omicdev")

    def test_r_env_same_pattern(self):
        with patch.dict(os.environ, {"OMICOS_R_ENV": "Renv"}, clear=False):
            self.assertEqual(env_mod.r_env(), "Renv")
        env_copy = {k: v for k, v in os.environ.items() if k != "OMICOS_R_ENV"}
        with patch.dict(os.environ, env_copy, clear=True):
            self.assertEqual(env_mod.r_env(), "CMAP")

    def test_admin_root_uses_env_var(self):
        with patch.dict(os.environ, {"OMICOS_ADMIN_ROOT": "/tmp/some/admin"}, clear=False):
            self.assertEqual(env_mod.admin_root(), Path("/tmp/some/admin").resolve())

    def test_targets_root_uses_env_var(self):
        with patch.dict(os.environ, {"OMICOS_TARGETS_ROOT": "/tmp/some/targets"}, clear=False):
            self.assertEqual(env_mod.targets_root(), Path("/tmp/some/targets").resolve())


class TestSmokeChecks(unittest.TestCase):
    def test_python_version_check_passes(self):
        # CI hosts running this repo are guaranteed Python >= 3.10.
        r = smoke_test.check_python_version()
        self.assertTrue(r.ok)
        self.assertIn("have", r.note)

    def test_pyyaml_check_reports_status(self):
        # Don't assume PyYAML is installed; just confirm the check executes.
        r = smoke_test.check_pyyaml()
        self.assertIn(r.ok, (True, False))
        self.assertIsInstance(r.note, str)

    def test_python_env_check_reports_via_env_var(self):
        # Point at a clearly-nonexistent conda env. The check should fail
        # with a remediation hint, but not crash.
        with patch.dict(os.environ, {"OMICOS_PYTHON_ENV": "definitely_not_an_env"}, clear=False):
            r = smoke_test.check_python_env()
            self.assertFalse(r.ok)
            self.assertIn("definitely_not_an_env", r.note)

    def test_r_env_check_is_warn_only(self):
        with patch.dict(os.environ, {"OMICOS_R_ENV": "no_such_R_env"}, clear=False):
            r = smoke_test.check_r_env()
            self.assertTrue(r.warn_only)

    def test_admin_root_check_warn_when_missing(self):
        with patch.dict(os.environ, {"OMICOS_ADMIN_ROOT": "/tmp/does_not_exist_xyz"},
                        clear=False):
            r = smoke_test.check_admin_root()
            self.assertTrue(r.warn_only)
            self.assertFalse(r.ok)

    def test_run_all_returns_n_results(self):
        results, hard, warn = smoke_test.run_all()
        self.assertEqual(len(results), len(smoke_test.CHECKS))
        self.assertIsInstance(hard, int)
        self.assertIsInstance(warn, int)


class TestDescribe(unittest.TestCase):
    def test_describe_returns_all_keys(self):
        info = env_mod.describe()
        for key in (
            "OMICOS_PYTHON_ENV", "OMICOS_R_ENV",
            "OMICOS_ADMIN_ROOT", "OMICOS_AWESOME_SG", "OMICOS_TARGETS_ROOT",
            "python_env_python", "r_env_rscript",
        ):
            self.assertIn(key, info)


if __name__ == "__main__":
    unittest.main()
