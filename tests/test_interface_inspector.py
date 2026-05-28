"""Unit tests for factory.interface_inspector — AST-only, no LazySlide install.

Uses small fixture packages written into a tempdir.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import interface_inspector as ii  # noqa: E402


def _write_pkg(root: Path) -> Path:
    """Write a fake `bio_pkg` with two modules: `bio_pkg.tools` and `bio_pkg.seg`."""
    pkg = root / "src" / "bio_pkg"
    (pkg / "tools").mkdir(parents=True)
    (pkg / "seg").mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from . import tools, seg\n", encoding="utf-8"
    )
    (pkg / "tools" / "__init__.py").write_text(
        "from ._features import feature_extraction, feature_aggregation\n"
        "from ._zero_shot import zero_shot_score\n"
        "__all__ = ['feature_extraction', 'feature_aggregation', 'zero_shot_score']\n",
        encoding="utf-8",
    )
    (pkg / "tools" / "_features.py").write_text(
        '''from typing import Literal
def feature_extraction(wsi, model: Literal["uni2", "virchow", "plip", "resnet50"] = "uni2",
                       amp: bool = False, device=None):
    """Extract features from each tile."""
    return None

def feature_aggregation(wsi, feature_key: str, encoder: Literal["prism", None] = None,
                        device=None):
    """Aggregate per-tile features to slide-level."""
    return None
''',
        encoding="utf-8",
    )
    (pkg / "tools" / "_zero_shot.py").write_text(
        '''def zero_shot_score(wsi, classes, feature_key: str, device="cpu"):
    """Compute zero-shot probability per tile."""
    return None
''',
        encoding="utf-8",
    )
    (pkg / "seg" / "__init__.py").write_text(
        "from ._cell import cells, CellRunner\n"
        "__all__ = ['cells', 'CellRunner']\n",
        encoding="utf-8",
    )
    (pkg / "seg" / "_cell.py").write_text(
        '''from typing import Literal
def cells(wsi, model: Literal["instanseg", "stardist"] = "instanseg", batch_size=4):
    """Cell-level segmentation."""
    return None

class CellRunner:
    """Runner wrapper."""
    def __init__(self, wsi, model_path: str, batch_size: int = 4):
        self.wsi = wsi
''',
        encoding="utf-8",
    )
    return root


class TestFindPackageRoot(unittest.TestCase):
    def test_src_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _write_pkg(Path(tmp))
            pkg_root = ii.find_package_root(root, "bio_pkg")
            self.assertIsNotNone(pkg_root)
            self.assertEqual(pkg_root.name, "bio_pkg")
            self.assertTrue((pkg_root / "__init__.py").exists())

    def test_flat_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "flatpkg").mkdir()
            (root / "flatpkg" / "__init__.py").write_text("", encoding="utf-8")
            pkg_root = ii.find_package_root(root, "flatpkg")
            self.assertIsNotNone(pkg_root)
            self.assertEqual(pkg_root.name, "flatpkg")

    def test_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(ii.find_package_root(Path(tmp), "nope"))


class TestInspectModule(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ii-"))
        _write_pkg(self.tmp)

    def test_tools_module_exports_three_functions(self):
        surface = ii.inspect_module(self.tmp, "bio_pkg", "bio_pkg.tools")
        names = [e.name for e in surface.exports]
        self.assertEqual(set(names), {"feature_extraction", "feature_aggregation", "zero_shot_score"})
        for ex in surface.exports:
            self.assertEqual(ex.kind, "function")
            self.assertTrue(ex.signature)

    def test_literal_branch_params_extracted(self):
        surface = ii.inspect_module(self.tmp, "bio_pkg", "bio_pkg.tools")
        fe = next(e for e in surface.exports if e.name == "feature_extraction")
        model_bp = next(bp for bp in fe.branch_params if bp.param == "model")
        self.assertEqual(set(model_bp.literals),
                         {"uni2", "virchow", "plip", "resnet50"})

    def test_seg_module_has_class_and_function(self):
        surface = ii.inspect_module(self.tmp, "bio_pkg", "bio_pkg.seg")
        kinds = {e.name: e.kind for e in surface.exports}
        self.assertEqual(kinds, {"cells": "function", "CellRunner": "class"})


class TestMarkdownRender(unittest.TestCase):
    def test_render_contains_branch_param_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _write_pkg(Path(tmp))
            tools = ii.inspect_module(root, "bio_pkg", "bio_pkg.tools")
            seg = ii.inspect_module(root, "bio_pkg", "bio_pkg.seg")
            md = ii.render_markdown([tools, seg], package="bio_pkg")
            self.assertIn("bio_pkg.tools", md)
            self.assertIn("feature_extraction", md)
            self.assertIn("Branch parameters", md)
            self.assertIn("instanseg", md)  # from seg.cells Literal


if __name__ == "__main__":
    unittest.main()
