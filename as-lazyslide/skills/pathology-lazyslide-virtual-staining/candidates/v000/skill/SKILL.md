---
id: pathology-lazyslide-virtual-staining
name: pathology-lazyslide-virtual-staining
title: H&E → virtual multiplex stain channels with LazySlide
description: Generate virtual IHC / IF biomarker channels from H&E using LazySlide's `zs.tl.virtual_stain` (GigaTIME UNet pixel-level OR ROSIE tile-level), persist them to `wsi.images['<model>_prediction']`, and render an overlay report. Use when the user wants generated stain channels from H&E, NOT real molecular quantification.
category: spatial_analysis
category_order: 34
tier: pro
summary: H&E → wsi.images['<model>_prediction'] virtual stain channels + overlay report
use_when: H&E WSI → virtual IHC / IF channel reconstruction (GigaTIME or ROSIE) with explicit "model proxy" caveat in the report.
---

# Virtual multiplex staining from H&E with LazySlide

## Goal

Take one H&E whole-slide image, generate virtual stain channels via
`zs.tl.virtual_stain(wsi, model=...)` (either GigaTIME at pixel level or
ROSIE at tile level), persist the predictions into
`wsi.images['<model>_prediction']`, and render a side-by-side H&E +
predicted-channel overlay report with an explicit caveat that virtual stain
channels are model predictions, not measured IHC.

The stable job is **H&E → wsi.images['<model>_prediction']**. The output
slot is `wsi.images`, NOT `wsi.tables` — this is the key data-contract
difference from feature-extraction skills.

## Quick Workflow

1. Verify HF gate access for the chosen model (GigaTIME / ROSIE are both
   HF-gated). Abort cleanly with a remediation hint if access is missing.
2. `from wsidata import open_wsi; wsi = open_wsi(slide)`.
3. `zs.pp.find_tissues(wsi)` — tissue mask precondition.
4. `zs.pp.tile_tissues(wsi, 256, stride_px=32)` — 256-px tiles with 32-px
   stride per the virtual staining tutorial. Stride is smaller than
   `feature_extraction`'s default to give a denser prediction grid.
5. `zs.tl.virtual_stain(wsi, model="gigatime")` (or `"rosie"`). Writes the
   prediction into `wsi.images['<model>_prediction']`.
6. Inspect available channels: `wsi.images["gigatime_prediction"].c.data`
   (a list of biomarker names, up to ~50 for GigaTIME).
7. `wsi.write()` persists the WSIData (including the new image stack).
8. Render `REPORT.html` with a per-channel overlay grid, `summary.json`,
   `handoff_manifest.json`. The report MUST carry a prominent caveat
   ribbon (see Constraints).

## Branches: virtual_stain(wsi, model=...)

| `model=` | Granularity | Output | Cost | When to pick |
|---|---|---|---|---|
| `'gigatime'` | pixel-level (UNet) | dense channel map, ~50 biomarkers | heavier (GPU) | default for visualization-grade output |
| `'rosie'` | tile-level (mean) | per-tile mean per channel | lighter | when only summary-level expression matters or compute is tight |

Both models are HF-gated; check access before the call.

## Resource Map

- Read `references/source-grounding.md` for inspected signature and output
  slot conventions.

## Constraints

- **Caveat ribbon**: every `REPORT.html` MUST include a prominent visual
  caveat that virtual stain channels are model predictions trained on
  paired H&E + IHC data, NOT measured IHC. Downstream interpretation must
  treat these as proxies. Do NOT emit a report that omits this caveat.
- GigaTIME / ROSIE are HF-gated. Verify
  `huggingface_hub.whoami()` before the `virtual_stain` call.
- GPU strongly recommended for GigaTIME; ROSIE more forgiving.
- Output lives in `wsi.images['<model>_prediction']` (NOT
  `wsi.tables[...]`). Downstream consumers that expect tables must read
  from the images slot via `wsi.images[...].c.data` for channel names and
  `.values` for the array.
- Tested against `lazyslide>=0.9`, `wsidata>=0.4`.

## Validation

- `wsi.shapes['tissues']` exists with at least one row.
- `wsi.shapes['tiles']` exists.
- `wsi.images['<model>_prediction']` exists after `virtual_stain` call.
- `wsi.images['<model>_prediction'].c.data` lists channel names (length
  matches the model's biomarker count; ~50 for GigaTIME).
- `outputs/<run_id>/REPORT.html` exists and contains:
  (a) the H&E thumbnail + tissue overlay, (b) a per-channel heatmap grid,
  (c) the model-proxy caveat ribbon.
- `outputs/<run_id>/summary.json` parses and contains
  `{n_tissues, n_tiles, fm_model, n_channels, channel_names, runtime_s, gpu_used}`.
- `outputs/<run_id>/handoff_manifest.json` lists the WSIData zarr path
  AND `image_predictions: ['<model>_prediction', ...]`.

<!-- SLOW_UPDATE_START -->
<!-- Epoch-boundary longitudinal guidance is written here by the slow_update
     prompt. Step-level edits never touch this region. -->
<!-- SLOW_UPDATE_END -->
