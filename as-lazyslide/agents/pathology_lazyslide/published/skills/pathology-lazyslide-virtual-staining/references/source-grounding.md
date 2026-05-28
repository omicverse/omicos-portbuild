# Source Grounding — pathology-lazyslide-virtual-staining

Verified live against https://lazyslide.readthedocs.io/en/latest/tutorials/virtual_staining.html
(May 2026 snapshot, lazyslide >= 0.9).

## Canonical workflow

```python
import lazyslide as zs
from wsidata import open_wsi

wsi = open_wsi(slide)
zs.pp.find_tissues(wsi)
zs.pp.tile_tissues(wsi, 256, stride_px=32)      # 256-px tiles, 32-px stride
zs.tl.virtual_stain(wsi, model="gigatime")      # writes wsi.images['gigatime_prediction']
```

## Branch parameters

- `zs.tl.virtual_stain(wsi, model=...)` — `model` is one of `'gigatime'`,
  `'rosie'`.
  - `'gigatime'` is a UNet that predicts at the **pixel level**, generating
    ~50 biomarker channels stored in `wsi.images['gigatime_prediction']`.
  - `'rosie'` operates at the **tile level**, producing a mean expression
    value per biomarker per tile.

## Output schema

- Output slot is `wsi.images['<model>_prediction']` (NOT `wsi.tables`).
- Channel names are accessible via
  `wsi.images['gigatime_prediction'].c.data`.
- Channel count depends on the model's training data — up to 50 for
  GigaTIME per the tutorial.

## HF gating

Both `gigatime` and `rosie` are HF-gated. Verify
`huggingface_hub.whoami()` returns a valid token before the
`virtual_stain` call; abort cleanly with a remediation hint if missing.

## Compute

GigaTIME is GPU-strongly-recommended; ROSIE is more forgiving but still
benefits from GPU.

## What this skill does NOT do

- Real molecular quantification — virtual staining is a learned proxy
  trained on paired H&E + IHC; every REPORT.html must carry an explicit
  caveat.
- Downstream per-cell analysis on the predicted channels — that's
  `spatial_omics_orchestrator` or a dedicated image analytics agent.
