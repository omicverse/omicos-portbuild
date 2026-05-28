# Phase 1 — Partition `lazyslide` into capabilities

The factory has written a stub `partition.json`. Fill it in by following
`factory/prompt_contracts/partition.md`.

## Inputs

- Package: `lazyslide`
- Source: https://github.com/RendeiroLab/LazySlide.git
- Docs:   https://lazyslide.readthedocs.io/en/latest/
- Tutorials / how-to pages listed in manifest.yaml:
  - `Preprocessing a WSI`
  - `Feature extraction and spatial analysis`
  - `Zero-shot learning`
  - `Approximate segmentation through tile prediction`
  - `Histology tile generation`
  - `Cell segmentation tutorial`
  - `Integration with slide-level labels`
  - `Survival prediction with cancer WSIs`
  - `Integration with transcriptomics`
  - `Virtual Staining`
  - `Welcome`
  - `Visualization`

## What to write

Replace every `<FILL: …>` in `partition.json` per the partition.md prompt
contract. Cluster tutorials into independently-triggerable capabilities; mark
pedagogy-only pages in `pedagogy_only_tutorials`.

Aim for AT MOST 8 capabilities. Prefer fewer, broader ones.

## How to check

```bash
python -m factory.partition status as-lazyslide
```

Lists resolved capabilities + any remaining <FILL:> markers.

## How to emit

Once all `<FILL:>` markers are replaced and the JSON parses cleanly:

```bash
python -m factory.partition emit as-lazyslide
```

This creates one `<capability_id>/manifest.yaml` per entry. From there,
each capability follows the normal `factory.draft init <root>/<id>` →
fill `<FILL:>`s → `optimizer init` → SkillOpt loop flow.
