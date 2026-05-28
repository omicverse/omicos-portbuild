# Source Grounding — pathology-lazyslide-he-analysis

Verified live against the LazySlide docs index (May 2026 snapshot, lazyslide >= 0.9).

## Entry point (NOT in `lazyslide.*` — it's in `wsidata`)

```python
from wsidata import open_wsi
wsi = open_wsi("path/to/slide.svs")  # returns a WSIData (SpatialData subclass)
```

`WSIData` exposes:

- `wsi.shapes` — dict-like, holds `GeoDataFrame`s (`tissues`, `tiles`, ...)
- `wsi.tables` — dict-like, holds `AnnData`s (`<model>_tiles`, ...)
- `wsi.fetch.features_anndata(model)` — convenience accessor
- `wsi[<key>]` — shortcut for `tables[<key>]`
- `wsi.write(path=None)` — persist to `.zarr`

## Module surface used by this skill

### `lazyslide.pp`

| Function | Side effect |
|---|---|
| `find_tissues(wsi, ...)` | writes `wsi.shapes['tissues']` |
| `tile_tissues(wsi, size, mpp=None, overlap=None, stride_px=None, key_added=None)` | writes `wsi.shapes['tiles']` |

### `lazyslide.tl`

| Function | Side effect |
|---|---|
| `tissue_props(wsi)` | adds columns to `wsi.shapes['tissues']` |
| `tile_prediction(wsi, model, ...)` | adds columns to `wsi.shapes['tiles']` |
| `feature_extraction(wsi, model, amp=False, device=None, ...)` | writes `wsi.tables['<model>_tiles']` |
| `feature_aggregation(wsi, feature_key, by=None, encoder=None, device=None)` | writes aggregated AnnData |
| `text_embedding(terms, model='plip')` | returns embeddings |
| `text_image_similarity(wsi, embeddings, model='plip', softmax=True)` | writes `wsi.tables['<model>_tiles_text_similarity']` |
| `zero_shot_score(wsi, classes, feature_key, device='cpu')` | returns / writes per-class prob table |
| `spatial_domain(wsi, feature_key, resolution=0.2)` | writes a domain table |

### Branch parameter values (audited from docs)

- `feature_extraction(wsi, model=...)` — `model` is one of `'uni2'`, `'uni'`, `'conch'`, `'gigapath'`, `'virchow'`, `'virchow2'`, `'ctranspath'`, `'plip'`, `'resnet50'`, or any timm id.
- `feature_aggregation(..., encoder=...)` — `encoder` is one of `None`, `'prism'`. PRISM REQUIRES virchow features.
- `tile_prediction(wsi, model=...)` — `model` is one of `'pathprofilerqc'`, ... .

### `lazyslide.metrics`

| Function | Purpose |
|---|---|
| `topk_score(...)` | top-K probability scoring for CONCH text-image workflows |

## Cross-modality boundary

This skill writes morphology / class-probability outputs. It does NOT write
gene-expression predictions. If the user wants the latter, the agent layer
routes to `he_to_st_predictor` first.
