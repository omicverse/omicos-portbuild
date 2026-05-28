---
id: pathology-lazyslide-supervised-classification
name: pathology-lazyslide-supervised-classification
title: Slide-level supervised classification with LazySlide features + sklearn / torch head
description: Build a supervised slide-level classifier on a cohort of H&E WSIs. LazySlide handles tile FM + slide aggregation; the supervised head (sklearn or PyTorch MLP) is downstream. Use when the user has known slide-level categorical labels and wants AUROC/AUPRC/F1 evaluation + a saved classifier.
category: spatial_analysis
category_order: 31
tier: pro
summary: WSI cohort + categorical labels → tile FM → slide embed → sklearn / torch head → AUROC report
use_when: WSI cohort with known slide-level categorical labels → trained classifier + AUROC/AUPRC/F1 report. NOT for zero-shot (no labels) or survival (time-to-event).
---

# Slide-level supervised classification with LazySlide

## Goal

Take a cohort of H&E WSIs + known categorical slide-level labels, build
per-tile FM features + per-slide aggregated embeddings via LazySlide,
train a supervised head (sklearn or PyTorch), and report AUROC / AUPRC /
F1 with a confusion matrix and calibration curve on a held-out test split.

The stable job is **cohort + labels → trained slide-level classifier + report**.
LazySlide owns the feature pipeline; the classifier head is sklearn /
torch, NOT a lazyslide native API.

## Quick Workflow

1. Verify HF gate access for the chosen FM backbone via
   `huggingface_hub.whoami()`. Abort cleanly if access is missing.
2. For each slide: `from wsidata import open_wsi; wsi = open_wsi(slide)` →
   `lazyslide.pp.find_tissues(wsi)` → `lazyslide.pp.tile_tissues(wsi, 256, mpp=0.5)`.
3. `lazyslide.tools.feature_extraction(wsi, model='<backbone>')` (e.g.
   `'uni2'`, `'titan'`, `'virchow'`, `'plip'`). The
   `pool_mode: Literal['cls', 'cls_patch_mean']` parameter controls how
   tile-level features are pooled inside the FM call.
4. `lazyslide.tools.feature_aggregation(wsi, feature_key='<backbone>',
   encoder='<mean|prism|titan>')` — slide-level embedding. **Constraints**:
   `encoder='prism'` REQUIRES virchow features upstream; `encoder='titan'`
   REQUIRES titan features upstream.
5. After all slides done, stack per-slide embeddings into a cohort
   AnnData via `wsidata.agg_wsi(metadata, wsi_col='slide_path',
   feature_key='<backbone>')`. The result has `X=(n_slides, embedding_dim)`,
   `obs=<your meta with label column>`.
6. Train/test split with `sklearn.model_selection.train_test_split(...,
   test_size=0.2, stratify=adata.obs['label'], random_state=10)`.
7. Train the head:
   - sklearn: `LogisticRegression(C=1.0).fit(X_train, y_train)` or
     `RandomForestClassifier(n_estimators=300, max_depth=None).fit(...)`.
   - PyTorch: small MLP (e.g. `Linear(in_dim, 64) → ReLU → Linear(64, n_classes)`)
     with `CrossEntropyLoss` + Adam.
8. Compute metrics on test: `roc_auc_score`, `average_precision_score`,
   `f1_score`, `confusion_matrix`, calibration curve via
   `sklearn.calibration.calibration_curve`.
9. Persist: `cohort_features.h5ad`, `classifier.pkl` (sklearn) or
   `head.pt` (torch), `label_encoder.pkl`. Render `REPORT.html`,
   `summary.json`, `handoff_manifest.json` to `outputs/<run_id>/`.

## Branches: aggregator + head

### Stratified k-fold CV for small cohorts

For cohort sizes < 200 slides, prefer `StratifiedKFold(n_splits=5,
shuffle=True, random_state=10)` over a single `train_test_split`. Run
`feature_extraction` + `feature_aggregation` per slide ONCE up-front, then
fold the **per-slide AnnData** (cheap to re-split). Report mean AUROC ±
std across folds + per-fold confusion matrices.


| `feature_aggregation(encoder=...)` | Notes |
|---|---|
| `'mean'` (default) | simple mean over tile features; works with any backbone |
| `'prism'` | REQUIRES virchow / virchow2 features upstream — PRISM slide encoder |
| `'titan'` | REQUIRES titan features upstream — TITAN slide encoder, 768-dim |
| custom Callable | user-supplied aggregator function |

| Head backend | When to pick |
|---|---|
| sklearn LR / RF / GBT (`.fit`, `.predict_proba`, `.score`) | classical baseline, robust on small cohorts (<500), no GPU |
| PyTorch MLP (Linear → ReLU → Linear) | better for large cohorts (≥500); needs GPU; calibration may need temperature scaling |

## Resource Map

- Read `references/source-grounding.md` for inspected `tools.feature_extraction`
  and `tools.feature_aggregation` signatures including the `pool_mode` and
  `encoder` Literals.

## Constraints

- HF gating for `feature_extraction` model — verify before running.
- `feature_aggregation(encoder='prism')` REQUIRES virchow / virchow2 features
  upstream. `encoder='titan'` REQUIRES titan features upstream. Substituting
  silently breaks alignment.
- sklearn head needs `scikit-learn>=1.3`; PyTorch head needs `torch>=2.0`.
- Reproducibility: fix `random_state=10` (or another explicit seed) for
  `train_test_split` and any RF / GBT calls.
- This skill MUST NOT produce molecular interpretation from morphology
  predictions alone. The classifier predicts a label; whether that label
  corresponds to a molecular state is the user's downstream concern.
- Tested against `lazyslide>=0.9`, `wsidata>=0.4`, `scikit-learn>=1.3`,
  optionally `torch>=2.0`.

## Validation

- For each slide: `wsi.tables['<backbone>_tiles']` exists.
- Aggregated slide AnnData has shape `(n_slides, embedding_dim)`.
- Train + test splits sum to the full cohort.
- Test-split AUROC is finite (not NaN), in `[0, 1]`; reported in `summary.json`.
- Test confusion matrix sums to `n_test`.
- `outputs/<run_id>/REPORT.html` contains AUROC, calibration curve, and
  per-class precision / recall.
- `outputs/<run_id>/handoff_manifest.json` lists cohort h5ad and classifier
  artifact path with `.pkl` or `.pt` suffix.

<!-- SLOW_UPDATE_START -->
<!-- Epoch-boundary longitudinal guidance is written here by the slow_update
     prompt. Step-level edits never touch this region. -->
<!-- SLOW_UPDATE_END -->
