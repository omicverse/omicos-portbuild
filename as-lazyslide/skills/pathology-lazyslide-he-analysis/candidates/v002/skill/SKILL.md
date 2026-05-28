---
id: pathology-lazyslide-he-analysis
name: pathology-lazyslide-he-analysis
title: Direct H&E WSI analysis with LazySlide (no transcriptomic prediction)
description: Run LazySlide's canonical pipeline on a single H&E whole-slide image — tissue mask, tiling, tile-prediction QC, pathology FM features, optional zero-shot tile typing — and produce the OmicOS report contract. Use when the input is H&E only AND the user wants morphology-level output, not molecular prediction.
category: spatial_analysis
category_order: 28
tier: pro
summary: LazySlide H&E direct pipeline → tissues / tiles / FM features / zero-shot / REPORT.html
use_when: H&E WSI direct analysis + report, no gene-expression prediction (for prediction, route to spatial-he-to-st-prediction first).
---

# Direct H&E WSI analysis with LazySlide

## Goal

Take one H&E whole-slide image (svs / tiff / ndpi / ome.tif) and produce a
`WSIData` container with tissue polygons, QC-checked tiles, pathology
foundation-model tile features, and (optionally) a per-tile zero-shot class
probability table; render the OmicOS report bundle.

The stable job is **the LazySlide pipeline up to but not past the pixel →
molecular boundary**. Cross that boundary (predict expression, do
deconvolution) by delegating to `he_to_st_predictor` first — this skill
optionally runs before that one as upstream WSI prep.

## Quick Workflow

1. Confirm GPU availability and HF gate access (UNI2 / Virchow are gated;
   PLIP / ResNet50 are public fallbacks).
2. `open_wsi(slide)` from `wsidata` → `WSIData` object (SpatialData subclass).
3. `zs.pp.find_tissues(wsi)` adds `wsi.shapes['tissues']` (tissue polygons +
   `tissue_id`).
4. `zs.tl.tissue_props(wsi)` adds area / perimeter / eccentricity to the
   tissues table.
5. `zs.pp.tile_tissues(wsi, 256, mpp=0.5)` adds `wsi.shapes['tiles']` (256-px
   tiles at 0.5 µm/px). Override `mpp` to match the source slide's native
   resolution.
6. `zs.tl.tile_prediction(wsi, "pathprofilerqc")` writes per-tile QC columns
   (`diagnostic_quality`, `visual_cleanliness`, `focus_issue`,
   `staining_issue`) onto the tiles table.
7. `zs.tl.feature_extraction(wsi, "uni2")` writes
   `wsi.tables['uni2_tiles']` (AnnData, one row per tile). Pick the backbone
   in §Branches below.
8. `zs.tl.feature_aggregation(wsi, "uni2", by="tissue_id")` aggregates per
   tissue for the slide-level summary.
9. Optional zero-shot pass: `zs.tl.feature_extraction(wsi, "virchow")` →
   `zs.tl.feature_aggregation(wsi, feature_key="virchow", encoder="prism", device="cpu")`
   → `zs.tl.zero_shot_score(wsi, classes, feature_key="virchow_tiles")`.
   `classes` is a list of text labels (e.g.
   `["tumor", "stroma", "necrosis", "normal"]`). PRISM REQUIRES virchow
   features upstream — do not substitute.
10. `wsi.write(<out_dir>/wsi.zarr)` persists. If the source is on read-only
    storage, pass an explicit out path; otherwise LazySlide writes a `.zarr`
    next to the slide.
11. Render `REPORT.html`, `summary.json`, `handoff_manifest.json` per the
    OmicOS contract (see Validation).

## Branches: feature_extraction(wsi, model)

### Pool-mode sub-branch

`zs.tl.feature_extraction(wsi, model, pool_mode=...)` accepts `Literal["cls", "cls_patch_mean"]`. CLS-only is faster and matches PRISM's expectation for the slide-encoder step; `cls_patch_mean` is denser and helps when the downstream task needs sub-tile texture (e.g. zero-shot heatmaps). When in doubt, use `pool_mode="cls"` for PRISM-bound pipelines and `pool_mode="cls_patch_mean"` for direct feature inspection.

| `model=` | Tier | Gated? | When to pick |
|---|---|---|---|
| `'uni2'` | best general FM | yes (HF) | default for high-quality features when you have HF access |
| `'uni'` | older UNI | yes (HF) | UNI2 unavailable |
| `'conch'` | vision-language | yes (HF) | CONCH text-image similarity workflows |
| `'gigapath'` | high quality | yes (HF) | needed by STPath downstream; not by direct H&E |
| `'virchow'` | zero-shot via PRISM | yes (HF) | required if you'll run `zero_shot_score` |
| `'virchow2'` | newer | yes (HF) | drop-in for virchow |
| `'plip'` | CLIP-style | no | text-image similarity without HF gate |
| `'ctranspath'` | smaller FM | no | public fallback |
| `'resnet50'` | baseline | no | smoke / no-FM fallback |

If `feature_aggregation(..., encoder='prism')` is used, the feature_key MUST
be `virchow` or `virchow2` — PRISM's slide encoder rejects other inputs.

## Resource Map

- Read `references/source-grounding.md` for inspected signatures, branch
  parameters, and storage-slot conventions.

## Constraints

- HF-gated backbones (UNI / UNI2 / CONCH / Gigapath / Virchow / Virchow2)
  require `huggingface-cli login` with an account granted access to each
  model. Verify the token before the FM step (call
  `huggingface_hub.whoami()`) and fall back to PLIP / ResNet50 if access is
  missing — do not let the run fail mid-way at model download.
- GPU strongly recommended for feature extraction on a full slide; CPU works
  but at 10x wall-clock.
- This skill MUST NOT produce molecular claims (gene expression, pathway
  scores, transcript abundance) from pixel evidence alone. Zero-shot text-
  class probabilities are tissue / morphology labels only, not molecular
  labels. If the user wants molecular output, the agent layer delegates to
  `he_to_st_predictor` first.
- Tested against `lazyslide>=0.9` and `wsidata>=0.4`. Pin these in the
  runtime env; pre-0.9 lazyslide is out-of-spec.
- Persistence: `wsi.write()` writes alongside the source slide by default.
  If the slide is on read-only storage, pass an explicit out path.

## Validation

## Chaining with he_to_st_predictor

When this skill runs as upstream prep for `he_to_st_predictor`, the
handoff manifest must include the WSIData zarr + `tiles` shape key. The
downstream predictor reads `wsi.shapes["tiles"]` and writes its
prediction AnnData into `wsi.tables["stpath_tiles"]` (or sibling tables
for STFlow / HEST-FM). Do NOT pre-compute `feature_extraction` here if
STPath is the downstream — it requires its own gigapath features.


- `wsi.shapes['tissues']` exists with at least one row.
- `wsi.shapes['tiles']` exists with row count consistent with slide area /
  (256 px) squared at the chosen `mpp`.
- `wsi.tables['<model>_tiles']` exists with `n_obs == len(wsi.shapes['tiles'])`
  and `n_vars` greater than 0.
- If zero-shot ran, `wsi.tables['virchow_tiles_text_similarity']` exists and
  column count matches the requested class count.
- `outputs/<run_id>/REPORT.html` exists and contains slide thumbnail, tissue
  overlay, tile-QC heatmap, and (if run) a zero-shot probability panel per
  class.
- `outputs/<run_id>/summary.json` parses and contains
  `{n_tissues, n_tiles, qc_pass_rate, fm_backbone, runtime_s, gpu_used}`.
- `outputs/<run_id>/handoff_manifest.json` lists `wsi_zarr`,
  `feature_tables`, and any `next_agent_hints`.

## Smoke testing on a synthetic fixture

To verify the canonical pipeline without a real WSI, exercise the small-
fixture path via the public `resnet50` backbone (no HF gate) on a 1-tile
synthetic SVS:

```python
from wsidata import open_wsi
import lazyslide as zs
wsi = open_wsi(synthetic_svs)          # any 256x256+ tile-grade RGB
zs.pp.find_tissues(wsi)
zs.pp.tile_tissues(wsi, 256, mpp=0.5)
zs.tl.feature_extraction(wsi, "resnet50")
assert len(wsi.shapes["tiles"]) > 0
assert wsi.tables["resnet50_tiles"].n_obs == len(wsi.shapes["tiles"])
```

The smoke MUST run on the synthetic fixture before any user-facing run on a
real slide.

<!-- SLOW_UPDATE_START -->
<!-- Epoch-boundary longitudinal guidance is written here by the slow_update
     prompt. Step-level edits never touch this region. -->
<!-- SLOW_UPDATE_END -->
