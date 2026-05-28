---
id: pathology-lazyslide-he-transcriptomics-integration
name: pathology-lazyslide-he-transcriptomics-integration
title: Paired H&E + RNA-seq integration with LazySlide RNALinker
description: Link aggregated WSI features to a paired RNA-seq AnnData using `lazyslide.tools.RNALinker`, producing association scores per slide / group. Use when the user has paired H&E + bulk / pseudo-bulk RNA-seq cohort (one row per slide aligned) and wants image-expression integration.
category: spatial_analysis
category_order: 33
tier: pro
summary: 配对 (H&E, RNA) → tile FM + 聚合 → RNALinker association → 报告
use_when: Paired H&E + RNA-seq AnnData (n_obs matches) → image-expression association via RNALinker. NOT for expression prediction (he_to_st_predictor) or spatial-omics downstream (spatial_omics_orchestrator).
---

# Paired H&E + transcriptomics integration with LazySlide

## Goal

Take a cohort of H&E WSIs + a paired RNA-seq `AnnData` (with the same
`n_obs`, one row per slide), build per-slide aggregated tile-FM features
via LazySlide, and link them to the RNA AnnData using
`lazyslide.tools.RNALinker(agg_features, others, gene_name=None).score(...)`
to produce per-slide / per-group association scores plus a ranked list of
the top image features for the chosen contrast.

The stable job is **paired (H&E, RNA) cohort → RNALinker association
report**. LazySlide owns the image-side feature pipeline + the
`RNALinker` API; the user provides the RNA AnnData.

## Quick Workflow

1. Verify HF gate access for the chosen FM backbone via
   `huggingface_hub.whoami()`.
2. Confirm alignment: the cohort metadata must let us pair each WSI with
   exactly one row in the RNA `AnnData`. After the WSI pipeline,
   `agg_features.n_obs == rna.n_obs` and the obs index orderings must
   match (or be reconcilable via a shared `sample_id` join).
3. For each slide: `from wsidata import open_wsi; wsi = open_wsi(slide)` →
   `lazyslide.pp.find_tissues(wsi)` →
   `lazyslide.pp.tile_tissues(wsi, 256, mpp=0.5)` →
   `lazyslide.tools.feature_extraction(wsi, model='<backbone>')` →
   `lazyslide.tools.feature_aggregation(wsi, feature_key='<backbone>', encoder='mean')`.
4. After all slides: `from wsidata import agg_wsi; agg_features =
   agg_wsi(metadata, wsi_col='slide_path', feature_key='<backbone>')`.
   Result: `AnnData(X=(n_slides, embedding_dim), obs=metadata)`.
5. Load the paired RNA AnnData. Confirm `agg_features.n_obs == rna.n_obs`
   AND align indices (e.g. via `agg_features.obs['sample_id'] ==
   rna.obs['sample_id']`).
6. `linker = lazyslide.tools.RNALinker(agg_features=agg_features,
   others=rna, gene_name=None)`. (`gene_name=None` is acceptable; pass a
   string if you want to label the others-side; see source-grounding.)
7. `linker.score(groupby='<col>', score_group='<value>', scale=True,
   test_method='t-test', n_features=100, key_added='association_score')`
   computes per-slide association scores for the foreground group.
8. Persist: the linker's outputs (typically an AnnData with
   `obs['association_score']` and an `uns` features table); render
   `REPORT.html` (score distribution per group, top-k features panel,
   tile-thumbnail grid per high-scoring feature), `summary.json`,
   `handoff_manifest.json`.

## Branches: RNALinker.score parameters

### Worked invocation

Given `agg_features = agg_wsi(meta, wsi_col="slide_path", feature_key="uni2")`
aligned with a paired `rna` AnnData by `sample_id`:

```python
from lazyslide.tools import RNALinker
linker = RNALinker(agg_features=agg_features, others=rna, gene_name=None)
linker.score(
    groupby="ESR1_high",          # bool column in adata.obs
    score_group=True,
    scale=True,
    test_method="wilcoxon",
    n_features=100,
    key_added="association_score",
)
# Result: agg_features.obs["association_score"] (per-slide)
#         + features ranked in agg_features.uns
```


| Param | Values | Notes |
|---|---|---|
| `groupby` | column name in `agg_features.obs` | defines the contrast (e.g. subtype, treatment) |
| `score_group` | category value within `groupby` | the foreground class |
| `scale` | bool (default `True`) | z-score features before scoring |
| `test_method` | `'t-test'` (default), `'wilcoxon'`, `'logreg'` | statistical test for ranking features |
| `n_features` | int (default 100) or `'all'` | top-k features to keep |
| `key_added` | str (default `'association_score'`) | result column name on `agg_features.obs` |

## Resource Map

- Read `references/source-grounding.md` for the inspected `RNALinker`
  constructor + `.score()` signature.

## Constraints

- **Alignment is mandatory**: `agg_features.n_obs == others.n_obs`, with
  matched obs ordering. If they don't match, abort and ask the user how
  to reconcile (do NOT silently truncate / reorder).
- **`scanpy` is a required dependency** — RNALinker checks for it at
  construction and raises `ModuleNotFoundError` if missing.
- HF gating for the FM backbone — verify before running.
- `feature_aggregation(encoder='prism')` REQUIRES virchow features upstream;
  `encoder='titan'` REQUIRES titan features upstream.
- This skill does NOT predict expression from images — for that, route to
  `he_to_st_predictor`. The "association" here is a correlation between
  pre-computed image features and observed expression, NOT a prediction.
- Tested against `lazyslide>=0.9`, `wsidata>=0.4`, `scanpy>=1.10`,
  `anndata>=0.10`.

## Validation

- For each slide: `wsi.tables['<backbone>_tiles']` exists; per-slide
  aggregated feature exists.
- `agg_features.n_obs == rna.n_obs` AND the obs indices align under the
  declared join key.
- After `linker.score(...)`: `agg_features.obs['association_score']` (or
  the user-supplied `key_added`) exists and is finite.
- Top-k features (length matches `n_features`) are reported in
  `summary.json`.
- `outputs/<run_id>/REPORT.html` contains the score distribution per group
  + top-k features panel + caveat that this is association, not prediction.
- `outputs/<run_id>/handoff_manifest.json` lists `linker_scored.h5ad`,
  `cohort_features.h5ad`, `paired_rna.h5ad`.

<!-- SLOW_UPDATE_START -->
<!-- Epoch-boundary longitudinal guidance is written here by the slow_update
     prompt. Step-level edits never touch this region. -->
<!-- SLOW_UPDATE_END -->
