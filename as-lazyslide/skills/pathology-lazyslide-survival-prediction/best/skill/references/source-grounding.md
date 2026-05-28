# Source Grounding — pathology-lazyslide-survival-prediction

Verified live against https://lazyslide.readthedocs.io/en/latest/tutorials/survival_prediction.html
(May 2026 snapshot, lazyslide >= 0.9).

## Pipeline entry point

```python
from wsidata import open_wsi
import lazyslide as zs
wsi = open_wsi(slide)
zs.pp.find_tissues(wsi)
zs.pp.tile_tissues(wsi, 448, mpp=0.5, background_fraction=0.5)   # NOTE: 448px not 256
zs.tl.feature_extraction(wsi, "titan", pbar=False)
zs.tl.feature_aggregation(wsi, "titan", encoder="titan")          # 768-dim slide embed
```

The PRISM-style invariant from the HE-analysis tutorial applies here too: the
TITAN slide encoder REQUIRES TITAN tile features upstream (analogous to how
PRISM requires Virchow). Do not substitute UNI / Virchow features.

## Slide-stacking helper

```python
adata = agg_wsi(metadata, wsi_col="slide_path", feature_key="titan")
# Result: AnnData(obs={status, OS_MONTHS, ...}, X=(n_slides, 768))
# Persists to "TCGA_READ_subset_TITAN.h5ad" in the tutorial
```

## Survival head — `scikit-survival` path (CPU OK)

```python
from sksurv.linear_model import CoxnetSurvivalAnalysis
from sksurv.metrics import concordance_index_censored
from sklearn.model_selection import train_test_split

X_train, X_test, y_train, y_test = train_test_split(
    adata.X, structured_y, test_size=0.2,
    stratify=adata.obs["status"], random_state=10,
)
model = CoxnetSurvivalAnalysis(
    l1_ratio=0.9, alpha_min_ratio=0.01, n_alphas=100, fit_baseline_model=True,
)
model.fit(X_train, y_train)
c_index_test = model.score(X_test, y_test)   # sksurv returns c-index directly
```

Baseline c-index ≈ 0.643 on the tutorial cohort.

## Survival head — `torchsurv` PyTorch path (GPU recommended)

```python
import torch
from torchsurv.loss import cox
from torchsurv.metrics import ConcordanceIndex

class CoxMLP(torch.nn.Module):
    def __init__(self, in_dim=768, hidden_dim=32):
        super().__init__()
        self.net = torch.nn.Sequential(
            torch.nn.Linear(in_dim, hidden_dim), torch.nn.ReLU(),
            torch.nn.Linear(hidden_dim, hidden_dim), torch.nn.ReLU(),
            torch.nn.Linear(hidden_dim, 1),
        )
        for m in self.net:
            if isinstance(m, torch.nn.Linear):
                torch.nn.init.xavier_uniform_(m.weight)
    def forward(self, x): return self.net(x)

estimate = model(X_train)
loss = cox.neg_partial_log_likelihood(estimate, E_train, T_train)
# Test:
cindex = ConcordanceIndex()
c_index_test = cindex(log_hz, E_test, T_test)
```

Tutorial reports CoxMLP c-index ≈ 0.857.

## Kaplan–Meier + log-rank

```python
from sksurv.nonparametric import kaplan_meier_estimator
from sksurv.compare import compare_survival
time_points, surv_prob, conf_int = kaplan_meier_estimator(
    adata.obs["status"][mask], adata.obs["OS_MONTHS"][mask], conf_type="log-log",
)
chi2, p_value = compare_survival(
    adata.obs[["status", "OS_MONTHS"]].to_records(index=False),
    adata.obs["clusters"],
)
```

## Branch parameters

- TILE_SIZE = 448 (NOT 256), `background_fraction=0.5`, `mpp=0.5`
- FEATURE BACKBONE = `"titan"`
- ENCODER = `"titan"` for `feature_aggregation` (mandatory)
- SURVIVAL BACKEND ∈ `{"sksurv.CoxnetSurvivalAnalysis", "torchsurv.CoxMLP"}`

## What this skill does NOT touch

- `lazyslide.seg.*` (cell segmentation)
- `lazyslide.io.*` (annotations)
- Per-tile zero-shot scoring (that's `pathology_lazyslide_he_analysis`)
- Categorical slide-level classification (that's
  `pathology_lazyslide_supervised_classification`)
