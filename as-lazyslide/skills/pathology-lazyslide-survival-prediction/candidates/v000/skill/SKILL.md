---
id: pathology-lazyslide-survival-prediction
name: pathology-lazyslide-survival-prediction
title: Cancer-WSI survival prediction with LazySlide TITAN + Cox head
description: Train a Cox survival head on TITAN slide-level embeddings of a cohort of H&E WSIs and report c-index + Kaplan–Meier. Use when the user has a cohort of H&E slides with paired right-censored survival metadata and wants a hazard predictor.
category: spatial_analysis
category_order: 32
tier: pro
summary: Cohort H&E → TITAN slide embedding → Cox / CoxMLP → c-index + KM
use_when: H&E WSI cohort + survival metadata (status, time-to-event) → trained survival predictor + c-index + Kaplan-Meier report.
---

# Cancer WSI survival prediction with LazySlide

## Goal

Take a cohort of H&E whole-slide images plus right-censored survival
metadata (status + time-to-event), embed each slide via the TITAN encoder,
fit a Cox survival head (either `sksurv.CoxnetSurvivalAnalysis` or a
PyTorch `CoxMLP` from torchsurv), and report c-index on a held-out split
with Kaplan–Meier curves stratified by predicted risk.

The stable job is **cohort + TITAN + Cox → c-index report**. The pipeline
uses TITAN specifically (not UNI / Virchow) because the TITAN slide encoder
is the path documented in the LazySlide survival tutorial.

## Quick Workflow

1. Verify HF gate access for `mahmoodlab/TITAN` via
   `huggingface_hub.whoami()`. TITAN is gated; abort cleanly if access is
   missing.
2. For each slide in the cohort: `open_wsi(slide)` → `zs.pp.find_tissues(wsi)`
   → `zs.pp.tile_tissues(wsi, 448, mpp=0.5, background_fraction=0.5)` (tile
   size 448 per the TITAN tutorial; do not override to 256).
3. `zs.tl.feature_extraction(wsi, "titan", pbar=False)` writes
   `wsi.tables['titan_tiles']`.
4. `zs.tl.feature_aggregation(wsi, "titan", encoder="titan")` produces a
   768-dim slide embedding (TITAN's slide-level encoder).
5. After all slides done, `agg_wsi(metadata, wsi_col="slide_path",
   feature_key="titan")` stacks slide embeddings into one survival
   `AnnData` (`obs={status, OS_MONTHS, ...}`, `X=(n_slides, 768)`).
6. Train/test split via `train_test_split(X, y, test_size=0.2,
   stratify=adata.obs["status"], random_state=10)` (or a pre-supplied
   split).
7. Fit a Cox head (pick the backend in §Branches below).
8. Compute c-index on the test set; render KM curves via
   `sksurv.nonparametric.kaplan_meier_estimator` stratified by predicted-
   risk tercile and `sksurv.compare_survival` for the log-rank p-value.
9. Write `survival_anndata.h5ad`, `cox_model.pkl` or `coxmlp.pt`,
   `REPORT.html`, `summary.json`, `handoff_manifest.json` to
   `outputs/<run_id>/`.

## Branches: Cox head backend

| Backend | When to pick | Loss / metric |
|---|---|---|
| `sksurv.linear_model.CoxnetSurvivalAnalysis(l1_ratio=0.9, alpha_min_ratio=0.01, n_alphas=100, fit_baseline_model=True)` | classical baseline; no GPU needed; auto-regularizes | `.score(X_test, y_test)` returns c-index |
| torchsurv `CoxMLP` (two hidden layers, hidden_dim=32, Xavier init) | neural baseline; GPU; typically larger c-index gains; needs `torchsurv` | training: `torchsurv.loss.cox.neg_partial_log_likelihood(estimate, E_train, T_train)`; eval: `torchsurv.metrics.ConcordanceIndex()(log_hz, E_test, T_test)` |

The tutorial reports sksurv baseline c-index ≈ 0.643 and torchsurv CoxMLP
c-index ≈ 0.857 on the bundled cohort. "Reasonable model performance" per
the tutorial is **c-index > 0.7**.

## Resource Map

- Read `references/source-grounding.md` for inspected TITAN / sksurv /
  torchsurv signatures.

## Constraints

- TITAN is HF-gated (`mahmoodlab/TITAN`). Verify access before any feature
  extraction; abort cleanly if missing.
- TITAN expects **448-px tiles at 0.5 mpp with `background_fraction=0.5`**
  per the tutorial. Do NOT substitute UNI / Virchow features — the TITAN
  slide encoder requires TITAN tile features upstream (analogous to PRISM
  requiring Virchow).
- `scikit-survival` (`sksurv`) AND/OR `torchsurv` must be importable;
  CoxMLP additionally needs `torch` with CUDA for reasonable wall-clock.
- Right-censored survival metadata must follow the convention
  `(status: bool, time_to_event: float)`. `agg_wsi(...)` requires a
  `wsi_col` pointing at the slide path column.
- Tested against `lazyslide>=0.9`, `wsidata>=0.4`, `scikit-survival>=0.22`,
  `torchsurv>=0.1`.

## Validation

- `wsi.tables['titan_tiles']` exists per slide with
  `n_obs == len(wsi.shapes['tiles'])`, `n_vars == 1536` (TITAN tile dim).
- Aggregated slide AnnData has shape `(n_slides, 768)`.
- Test-split c-index is reported in `summary.json` and is finite (not NaN).
- Log-rank p-value is reported.
- For the torchsurv path: the training loss is monotonically non-increasing
  over the documented warm-up window, and `ConcordanceIndex` returns a
  value in `[0, 1]`.
- `outputs/<run_id>/REPORT.html` exists with the KM panel and the c-index
  comparison.
- `outputs/<run_id>/handoff_manifest.json` lists the survival AnnData and
  the model artifact path (with extension `.pkl` for sksurv or `.pt` for
  torchsurv so the next agent can route on suffix).

<!-- SLOW_UPDATE_START -->
<!-- Epoch-boundary longitudinal guidance is written here by the slow_update
     prompt. Step-level edits never touch this region. -->
<!-- SLOW_UPDATE_END -->
