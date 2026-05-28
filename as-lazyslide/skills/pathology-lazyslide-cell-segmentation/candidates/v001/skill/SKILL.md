---
id: pathology-lazyslide-cell-segmentation
name: pathology-lazyslide-cell-segmentation
title: Cell-level H&E segmentation with LazySlide seg.cells / seg.cell_types
description: Run LazySlide's per-cell instance segmentation (`lazyslide.seg.cells`, default model `instanseg`) and optional per-cell typing (`lazyslide.seg.cell_types`, default `nulite`) on one H&E whole-slide image, persist cells to `wsi.shapes['cells']` (and optionally `cell_types`), and produce the OmicOS report contract. Use when the user wants cell-level shapes, not tile-level approximations.
category: spatial_analysis
category_order: 30
tier: pro
summary: H&E → per-cell polygons (InstanSeg) + cell types (NuLite) + overlay report
use_when: H&E WSI direct analysis at CELL granularity (cell polygons in wsi.shapes['cells'], optional cell types); use he_analysis for tile-level approximate segmentation.
---

# Cell-level segmentation of H&E with LazySlide

## Goal

Take one H&E whole-slide image, produce per-cell instance polygons via
`lazyslide.seg.cells` (default backend: InstanSeg) and optional per-cell
type labels via `lazyslide.seg.cell_types` (default: NuLite), persist them
into `wsi.shapes['cells']` (and `wsi.shapes['cell_types']`), and render an
OmicOS report with cell overlays + density panels.

The stable job is **H&E → wsi.shapes['cells'] + optional cell types**.
Distinct from `pathology_lazyslide_he_analysis`'s tile-level approximate
segmentation (cheaper, coarser, uses `tools.tile_prediction`) and from
`seg.semantic` whose output is anatomical-region masks.

## Quick Workflow

1. Verify HF gate access for the chosen models (InstanSeg / NuLite /
   PathProfiler / GrandQC may all be gated).
2. `from wsidata import open_wsi; wsi = open_wsi(slide)`.
3. Tissue mask — pick one of:
   - Classical: `lazyslide.pp.find_tissues(wsi, method='otsu')` (or
     `'entropy'`). Cheaper, no DL.
   - DL: `lazyslide.seg.tissue(wsi, model='pathprofiler')` (or `'grandqc'`,
     `'hest'`). Higher quality, GPU.
4. `lazyslide.pp.tile_tissues(wsi, 256, mpp=0.5)` — tile precondition for
   the cell pipelines (they iterate over `wsi.shapes['tiles']`).
5. `lazyslide.seg.cells(wsi, model='instanseg', batch_size=4)` — writes
   per-cell polygons to `wsi.shapes['cells']` (default `key_added='cells'`).
   For each tile, instance polygons are produced and merged. See
   `extract_features=True` to also attach per-cell feature vectors.
6. (Optional) `lazyslide.seg.cell_types(wsi, model='nulite', batch_size=4)`
   — adds per-cell type labels to `wsi.shapes['cell_types']`.
7. (Optional) `lazyslide.seg.artifact(wsi, model='grandqc', tile_key='tiles')`
   — pre/post-flag artifact regions to exclude.
8. (Optional) `lazyslide.seg.zero_shot(wsi, prompts=['lymphocyte', 'tumor cell'],
   model='sam', ...)` — SAM-style prompt-driven segmentation.
9. `wsi.write(<out_dir>/wsi.zarr)` persists.
10. Render `REPORT.html` (cell overlay + per-tissue density + type
    composition if cell_types ran), `summary.json`, `handoff_manifest.json`.

## Branches: model selection

### Low-level runner: `seg.CellSegmentationRunner`

When the user needs per-cell FEATURES (not just polygons), instantiate
`seg.CellSegmentationRunner` directly with `extract_features=True`. The
runner returns an `AnnData` (one row per cell) alongside the polygons,
ready for downstream supervised heads:

```python
runner = zs.seg.CellSegmentationRunner(
    wsi, model="instanseg", tile_key="tiles",
    extract_features=True, size_filter=True, nucleus_size=(20, 1000),
)
runner.run()                                 # writes wsi.shapes["cells"] + wsi.tables["cells_features"]
```


| Function | `model=` | Notes |
|---|---|---|
| `seg.tissue` | `'grandqc'`, `'pathprofiler'` (default), `'hest'` | Literal-annotated; DL tissue mask |
| `seg.cells` | `'instanseg'` (default) + any `SegmentationModelProtocol` instance | cell instance polygons |
| `seg.cell_types` | `'nulite'` (default) + any `SegmentationModelProtocol` instance | cell type classification |
| `seg.artifact` | `'grandqc'` (default) + any `SegmentationModelProtocol` instance, `mode ∈ {'constant','gaussian'}` | artifact detection |
| `seg.semantic` | any `SegmentationModelProtocol` instance, `mode ∈ {'constant','gaussian'}` | general semantic seg runner |
| `seg.zero_shot` | `'sam'` (default) | SAM-style prompts; `threshold ∈ {'otsu', float, List[float]}` |

For low-level control, instantiate `seg.CellSegmentationRunner` /
`seg.SemanticSegmentationRunner` directly (see source-grounding).

## Resource Map

- Read `references/source-grounding.md` for full inspected signatures (extracted
  from a clone of the upstream lazyslide source).

## Constraints

- Cell models (InstanSeg / NuLite) are typically HF-gated. Verify
  `huggingface_hub.whoami()` before the cell pass; abort cleanly if
  missing access.
- `lazyslide.seg.cells` requires `wsi.shapes['tiles']` upstream — the
  tile_key parameter defaults to `Key.tiles`.
- The output slot is `wsi.shapes['cells']` and `wsi.shapes['cell_types']`,
  with `key_added` overridable. Document any non-default key in the
  handoff manifest.
- GPU is strongly recommended; CPU works but is order-of-magnitude slower
  for cell-level passes.
- This skill MUST NOT produce molecular claims from cell polygons alone
  (cell type labels are morphology proxies, not measured markers). Route
  molecular claims via `he_to_st_predictor`.
- Tested against `lazyslide>=0.9`, `wsidata>=0.4`.

## Validation

- `wsi.shapes['tissues']` exists with at least one row.
- `wsi.shapes['tiles']` exists.
- `wsi.shapes['cells']` exists after `seg.cells` with row count > 0 and
  required columns including the geometry column.
- If `cell_types` ran, `wsi.shapes['cell_types']` exists with the same row
  count as `cells` and a categorical type column.
- `outputs/<run_id>/REPORT.html` exists with cell overlay + density panel.
- `outputs/<run_id>/summary.json` parses and includes
  `{n_tissues, n_cells, cell_instance_model, runtime_s, gpu_used}`.
- `outputs/<run_id>/handoff_manifest.json` lists `wsi_zarr` and
  `shapes_keys` including `cells` (and `cell_types` if run).

<!-- SLOW_UPDATE_START -->
<!-- Epoch-boundary longitudinal guidance is written here by the slow_update
     prompt. Step-level edits never touch this region. -->
<!-- SLOW_UPDATE_END -->
