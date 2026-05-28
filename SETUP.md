# Setup — 30-second checklist

This kit needs one thing to be reachable: a Python conda env for the smoke
runner. Two further env vars are optional — an R conda env (only if a
capability has an R-side component) and a private OmicOS-admin checkout
(only if you're deploying into one and want dedup + router scans).

## 1. Conda envs

The Python env is the only one most users need:

```bash
conda info --envs | awk '/omicdev/ {print $NF}'  # already there?
conda create -n omicdev python=3.10 -y           # if not
```

The R env is optional and only used when a capability has an R-side
smoke command:

```bash
conda create -n CMAP -c conda-forge r-base r-essentials -y
```

## 2. Export the env vars

```bash
# Required (defaults to 'omicdev' if unset, but pin it explicitly):
export OMICOS_PYTHON_ENV=omicdev

# Optional — only set if you'll port a capability with an R-side smoke step:
export OMICOS_R_ENV=CMAP

# Optional — only set if you're deploying into a private OmicOS-admin
# checkout AND want Phase 0 dedup + `route --members auto` member scans.
# portbuild never writes to this path; it is read-only context.
# export OMICOS_ADMIN_ROOT="$HOME/path/to/omicos-admin"

# Optional — informational only; surfaced in smoke output:
# export OMICOS_AWESOME_SG="$HOME/path/to/awesome-skill-generate"
```

Add the lines to `~/.bashrc` (or `~/.zshrc`) so subsequent shells see them.

## 3. Install the kit's own deps

From this repo's root:

```bash
pip install -e .             # registers `omicos-portbuild` console script
```

## 4. Verify

```bash
python -m factory.smoke_test
```

Expected: `[smoke] OK -- N/N checks passed` (N depends on whether you set up R
and admin_root; both are warn-only).

## 5. First build

```bash
omicos-portbuild build \
  --capability-id pathology_lazyslide_he_analysis \
  --package lazyslide \
  --source https://github.com/RendeiroLab/LazySlide \
  --docs https://lazyslide.readthedocs.io/en/latest/
```

`build` re-runs the smoke checks automatically. Pass `--skip-smoke` to bypass.

## Env-var reference

| Var | Default | Used by |
|---|---|---|
| `OMICOS_PYTHON_ENV` | `omicdev` | `acceptance.py`, `optimizer.run-rollouts` instructions |
| `OMICOS_R_ENV` | `CMAP` | R-side acceptance smoke commands (optional) |
| `OMICOS_ADMIN_ROOT` | unset | **Optional.** `discovery.py` (read-only duplicate check), `route.py` (read-only member scan). Never written to; `publisher.py` refuses any `--out` whose path contains `omicos-admin`. |
| `OMICOS_AWESOME_SG` | `../awesome-skill-generate` (if present) | informational; surfaced in smoke output |
| `OMICOS_TARGETS_ROOT` | `<repo>/targets` | where `targets/<id>/` lives; useful if you keep working dirs on a fast scratch disk |

Each var follows the same resolution order: **env var → manifest field → built-in default**.
A target's `manifest.yaml` can still override the per-target defaults
(e.g. one capability uses a heavier conda env), but the env var wins for the
current shell session.
