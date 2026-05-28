"""Verify the new partition CLI: init → fill → status → emit produces N
capability scaffolds from one package root.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import partition  # noqa: E402
from factory.manifest import write_initial_manifest  # noqa: E402


def _make_package_root(tmp: Path) -> Path:
    root = tmp / "as-pkg"
    write_initial_manifest(
        target_dir=root,
        capability_id="package_root",
        kind="capability",   # the root manifest uses 'capability' as the umbrella for now
        package="lazyslide",
        source="https://github.com/RendeiroLab/LazySlide",
        docs="https://lazyslide.readthedocs.io/en/latest/",
        notebooks=[
            "tutorials/preprocessing.html",
            "tutorials/cell_segmentation.html",
            "tutorials/virtual_staining.html",
        ],
        cost_profile="gpu-required",
    )
    return root


class TestPartitionInit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-partition-"))
        self.root = _make_package_root(self.tmp)

    def test_init_writes_stub_and_instructions(self):
        out = partition.cmd_init(self.root)
        self.assertTrue((self.root / "partition.json").exists())
        self.assertTrue((self.root / "PARTITION_INSTRUCTIONS.md").exists())
        # Stub partition.json must include <FILL:> markers
        data = (self.root / "partition.json").read_text(encoding="utf-8")
        self.assertIn("<FILL:", data)
        # And must include the package name pre-filled from manifest
        self.assertIn("lazyslide", data)


class TestPartitionStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-partition-"))
        self.root = _make_package_root(self.tmp)
        partition.cmd_init(self.root)

    def test_status_reports_fills_remaining(self):
        s = partition.cmd_status(self.root)
        self.assertGreater(s["fills_remaining"], 0)
        self.assertFalse(s["ready_to_emit"])

    def test_status_after_full_fill(self):
        # Drop in a fully-resolved partition.json with 2 capabilities.
        filled = {
            "package": "lazyslide",
            "source": "https://github.com/RendeiroLab/LazySlide",
            "docs": "https://lazyslide.readthedocs.io/en/latest/",
            "reasoning": "Two distinct jobs: morphology pipeline vs cell-level segmentation.",
            "capabilities": [
                {
                    "id": "pathology_lazyslide_he_analysis",
                    "title": "Direct H&E whole-slide morphology pipeline",
                    "tutorials": ["preprocessing.html", "feature_extraction.html",
                                  "zero-shot-learning.html"],
                    "key_modules": ["lazyslide.pp", "lazyslide.tl"],
                    "key_apis": [
                        {"name": "zs.pp.find_tissues", "purpose": "tissue polygons"},
                        {"name": "zs.tl.feature_extraction", "purpose": "FM tile features"},
                    ],
                    "distinct_from": "no cell-level shapes",
                    "domain": "spatial-he",
                    "cost_profile": "gpu-required",
                },
                {
                    "id": "pathology_lazyslide_cell_segmentation",
                    "title": "Cell-level segmentation from H&E",
                    "tutorials": ["cell_segmentation.html"],
                    "key_modules": ["lazyslide.seg"],
                    "key_apis": [
                        {"name": "zs.seg.cells", "purpose": "per-cell polygons"}
                    ],
                    "distinct_from": "cell shapes, not tiles or tissue regions",
                    "domain": "spatial-he",
                    "cost_profile": "gpu-required",
                },
            ],
            "pedagogy_only_tutorials": ["wsi_visualization.html"],
        }
        (self.root / "partition.json").write_text(
            json.dumps(filled, indent=2), encoding="utf-8"
        )
        s = partition.cmd_status(self.root)
        self.assertEqual(s["fills_remaining"], 0)
        self.assertEqual(s["n_capabilities"], 2)
        self.assertTrue(s["ready_to_emit"])


class TestPartitionEmit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-partition-"))
        self.root = _make_package_root(self.tmp)
        partition.cmd_init(self.root)
        # Hand-write a 2-capability partition.json
        filled = {
            "package": "lazyslide",
            "source": "https://github.com/RendeiroLab/LazySlide",
            "docs": "https://lazyslide.readthedocs.io/en/latest/",
            "reasoning": "Two distinct jobs.",
            "capabilities": [
                {
                    "id": "pathology_lazyslide_he_analysis",
                    "title": "Direct H&E pipeline",
                    "tutorials": ["preprocessing.html"],
                    "key_modules": ["lazyslide.pp"],
                    "key_apis": [{"name": "zs.pp.find_tissues", "purpose": "tissue"}],
                    "distinct_from": "tiles only",
                    "domain": "spatial-he",
                    "cost_profile": "gpu-required",
                },
                {
                    "id": "pathology_lazyslide_cell_segmentation",
                    "title": "Cell segmentation",
                    "tutorials": ["cell_segmentation.html"],
                    "key_modules": ["lazyslide.seg"],
                    "key_apis": [{"name": "zs.seg.cells", "purpose": "cells"}],
                    "distinct_from": "cells only",
                    "domain": "spatial-he",
                    "cost_profile": "gpu-required",
                },
            ],
            "pedagogy_only_tutorials": [],
        }
        (self.root / "partition.json").write_text(
            json.dumps(filled, indent=2), encoding="utf-8"
        )

    def test_emit_creates_one_subdir_per_capability(self):
        result = partition.cmd_emit(self.root)
        self.assertEqual(result["n_skills_created"], 2)
        self.assertEqual(result["n_skills_existed"], 0)
        for cap_id in ("pathology_lazyslide_he_analysis",
                       "pathology_lazyslide_cell_segmentation"):
            skill_dir = self.root / "skills" / cap_id
            self.assertTrue(skill_dir.is_dir())
            self.assertTrue((skill_dir / "manifest.yaml").exists())

    def test_emit_is_idempotent(self):
        partition.cmd_emit(self.root)
        result2 = partition.cmd_emit(self.root)
        self.assertEqual(result2["n_skills_created"], 0)
        self.assertEqual(result2["n_skills_existed"], 2)

    def test_emit_refuses_with_fills_remaining(self):
        (self.root / "partition.json").write_text(
            '{"package": "x", "capabilities": [{"id": "<FILL: id>"}]}',
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            partition.cmd_emit(self.root)


if __name__ == "__main__":
    unittest.main()
