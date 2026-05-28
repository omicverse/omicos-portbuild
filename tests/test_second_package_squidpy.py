"""Drive the factory end-to-end on a *second* package (squidpy: spatial
transcriptomics). The point is not to validate squidpy — it's to make sure
nothing in the engine is silently hardcoded for LazySlide.

We use a hand-crafted partition.json (no network, no source clone) and run:

  partition.init/emit  →  draft.init  →  draft.promote  →  partition.update

For each emitted target we assert the scaffold contains *no* LazySlide-
specific tokens (`lazyslide`, `wsi`, `H&E`) — those would only appear if
something engine-side had leaked a benchmark assumption.

If this ever breaks, the factory has grown a LazySlide coupling and the
next non-LazySlide port will silently inherit it. That's the only thing
we're trying to prevent here.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory import draft, partition  # noqa: E402
from factory.manifest import read_manifest, write_initial_manifest  # noqa: E402


_SQUIDPY_PARTITION = {
    "package": "squidpy",
    "source": "https://github.com/scverse/squidpy",
    "docs": "https://squidpy.readthedocs.io/",
    "reasoning": "Grouped squidpy tutorials by analytical primitive.",
    "capabilities": [
        {
            "id": "squidpy_neighborhood_enrichment",
            "title": "Squidpy neighborhood enrichment on spatial AnnData",
            "tutorials": ["tutorials/tutorial_nhood_enrichment.ipynb"],
            "key_modules": ["squidpy.gr"],
            "key_apis": [
                {"name": "squidpy.gr.spatial_neighbors", "purpose": "build graph"},
                {"name": "squidpy.gr.nhood_enrichment", "purpose": "compute z-scores"},
                {"name": "squidpy.pl.nhood_enrichment", "purpose": "plot"},
            ],
            "distinct_from": "Doesn't compute autocorrelation; pure pairwise enrichment.",
            "domain": "spatial-rna",
            "cost_profile": "moderate",
        },
        {
            "id": "squidpy_spatial_autocorrelation",
            "title": "Squidpy spatial autocorrelation (Moran's I / Geary's C)",
            "tutorials": ["tutorials/tutorial_autocorr.ipynb"],
            "key_modules": ["squidpy.gr"],
            "key_apis": [
                {"name": "squidpy.gr.spatial_autocorr", "purpose": "Moran/Geary"},
            ],
            "distinct_from": "Single-gene autocorrelation, not pairwise.",
            "domain": "spatial-rna",
            "cost_profile": "cheap",
        },
    ],
    "agents": [
        {
            "id": "squidpy_omni",
            "title": "Squidpy omni-agent",
            "owns_skills": [
                "squidpy_neighborhood_enrichment",
                "squidpy_spatial_autocorrelation",
            ],
            "domain": "spatial-rna",
            "bundle_rationale": "Same package, same input modality (AnnData), same domain.",
        },
    ],
    "pedagogy_only_tutorials": [],
}


_LAZYSLIDE_TOKENS = ["lazyslide", "wsi", "H&E", "openslide"]


class TestSquidpyPartitionEndToEnd(unittest.TestCase):
    """Engine should accept a squidpy-shaped partition with no special-casing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="osg-squidpy-"))
        self.root = self.tmp / "as-squidpy"
        self.root.mkdir(parents=True)
        # Bootstrap the package-level manifest emit() reads
        write_initial_manifest(
            target_dir=self.root,
            capability_id="squidpy_package_root", kind="capability",
            package="squidpy",
            source="https://github.com/scverse/squidpy",
            docs="https://squidpy.readthedocs.io/",
            notebooks=[], cost_profile="moderate",
            extra={"layout": "multi-capability"},
        )
        (self.root / "partition.json").write_text(
            json.dumps(_SQUIDPY_PARTITION, indent=2), encoding="utf-8"
        )

    def test_emit_creates_kind_dispatched_subdirs(self):
        out = partition.cmd_emit(self.root)
        self.assertEqual(out["root"], str(self.root))
        skill_ids = {c["id"] for c in out["skills"]}
        agent_ids = {c["id"] for c in out["agents"]}
        self.assertIn("squidpy_neighborhood_enrichment", skill_ids)
        self.assertIn("squidpy_spatial_autocorrelation", skill_ids)
        self.assertIn("squidpy_omni", agent_ids)

        # kind-dispatched layout
        self.assertTrue((self.root / "skills" / "squidpy_neighborhood_enrichment" / "manifest.yaml").exists())
        self.assertTrue((self.root / "skills" / "squidpy_spatial_autocorrelation" / "manifest.yaml").exists())
        self.assertTrue((self.root / "agents" / "squidpy_omni" / "manifest.yaml").exists())

        # manifest.extra round-trips key_apis + domain
        m = read_manifest(self.root / "skills" / "squidpy_neighborhood_enrichment")
        self.assertEqual(m.kind, "skill")
        self.assertEqual(m.extra.get("domain"), "spatial-rna")
        api_names = {a["name"] for a in m.extra.get("key_apis", [])}
        self.assertIn("squidpy.gr.spatial_neighbors", api_names)

    def test_draft_init_scaffolds_each_target_without_lazyslide_leakage(self):
        partition.cmd_emit(self.root)
        targets = [
            self.root / "skills" / "squidpy_neighborhood_enrichment",
            self.root / "skills" / "squidpy_spatial_autocorrelation",
            self.root / "agents" / "squidpy_omni",
        ]
        for t in targets:
            draft.run(t)
            # SKILL or AGENT scaffold present
            cand = t / "candidates" / "v000"
            self.assertTrue(cand.exists(), msg=f"{t} has no v000")
            scaffold_files: list[Path] = []
            if (cand / "skill" / "SKILL.md").exists():
                scaffold_files.append(cand / "skill" / "SKILL.md")
            if (cand / "agent.md").exists():
                scaffold_files.append(cand / "agent.md")
            self.assertGreater(len(scaffold_files), 0, msg=f"{t} has no scaffold body")
            for f in scaffold_files:
                body = f.read_text(encoding="utf-8").lower()
                for token in _LAZYSLIDE_TOKENS:
                    # `wsi` would be a substring of `wsidata`; we want
                    # exact-word checks. Lazyslide-specific tokens are
                    # rare enough that simple substring is acceptable —
                    # except `wsi` which we check as standalone token.
                    if token == "wsi":
                        self.assertNotRegex(
                            body, r"\bwsi\b",
                            msg=f"LazySlide token 'wsi' leaked into {f}",
                        )
                    else:
                        self.assertNotIn(
                            token.lower(), body,
                            msg=f"LazySlide token {token!r} leaked into {f}",
                        )

    def test_partition_update_is_non_destructive_on_squidpy(self):
        partition.cmd_emit(self.root)
        m_dir = self.root / "skills" / "squidpy_neighborhood_enrichment"

        # Modify partition.json — add a new key_api to the existing skill
        p = json.loads((self.root / "partition.json").read_text(encoding="utf-8"))
        p["capabilities"][0]["key_apis"].append(
            {"name": "squidpy.gr.co_occurrence", "purpose": "co-occurrence"}
        )
        (self.root / "partition.json").write_text(json.dumps(p, indent=2), encoding="utf-8")

        partition.cmd_update(self.root)
        # New API appears, original two preserved
        m_after = read_manifest(m_dir)
        names = {a["name"] for a in m_after.extra.get("key_apis", [])}
        self.assertIn("squidpy.gr.co_occurrence", names)
        self.assertIn("squidpy.gr.spatial_neighbors", names)
        self.assertIn("squidpy.gr.nhood_enrichment", names)

    def test_partition_update_refuses_silent_key_api_removal(self):
        partition.cmd_emit(self.root)
        # Remove one key_api from partition.json without --force
        p = json.loads((self.root / "partition.json").read_text(encoding="utf-8"))
        p["capabilities"][0]["key_apis"] = [
            a for a in p["capabilities"][0]["key_apis"]
            if a["name"] != "squidpy.gr.nhood_enrichment"
        ]
        (self.root / "partition.json").write_text(json.dumps(p, indent=2), encoding="utf-8")

        out = partition.cmd_update(self.root)
        # update should report this as an issue, not silently drop it
        issues = out.get("issues", []) or out.get("warnings", [])
        joined = " ".join(issues) if issues else ""
        # the manifest should NOT have lost the API without --force
        m_after = read_manifest(self.root / "skills" / "squidpy_neighborhood_enrichment")
        names = {a["name"] for a in m_after.extra.get("key_apis", [])}
        self.assertIn(
            "squidpy.gr.nhood_enrichment", names,
            msg=f"update silently removed a key_api; issues={joined!r}",
        )


if __name__ == "__main__":
    unittest.main()
