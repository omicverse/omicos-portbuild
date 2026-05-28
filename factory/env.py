"""Centralized resolution of well-known environment variables.

Mirrors omicverse-rebuildr's `PYTHON_TEST_ENV` / `R_TEST_ENV` contract:
this kit reads a small set of env vars at the very start of every run and
exposes them through a single import. Anything that wants to talk to a
conda env, the admin registry, or the upstream awesome-skill-generate
clone must go through these helpers rather than reading `os.environ` directly.

Resolution order for every value: env var → manifest field → built-in default.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------- #
# Well-known variable names                                                   #
# --------------------------------------------------------------------------- #

OMICOS_PYTHON_ENV = "OMICOS_PYTHON_ENV"
OMICOS_R_ENV = "OMICOS_R_ENV"
OMICOS_ADMIN_ROOT = "OMICOS_ADMIN_ROOT"
OMICOS_AWESOME_SG = "OMICOS_AWESOME_SG"
OMICOS_TARGETS_ROOT = "OMICOS_TARGETS_ROOT"

# Built-in defaults (anchored to the user's omicos_dev convention from the
# CLAUDE.md memory: Python = conda `omicdev`, R = conda `CMAP`).
DEFAULT_PYTHON_ENV = "omicdev"
DEFAULT_R_ENV = "CMAP"


# --------------------------------------------------------------------------- #
# Accessors                                                                   #
# --------------------------------------------------------------------------- #


def python_env(manifest_value: Optional[str] = None) -> str:
    return os.environ.get(OMICOS_PYTHON_ENV) or manifest_value or DEFAULT_PYTHON_ENV


def r_env(manifest_value: Optional[str] = None) -> str:
    return os.environ.get(OMICOS_R_ENV) or manifest_value or DEFAULT_R_ENV


def admin_root() -> Path:
    p = os.environ.get(OMICOS_ADMIN_ROOT)
    if p:
        return Path(p).expanduser().resolve()
    # Default: sibling repo at ../omicos-admin
    return (_repo_root().parent / "omicos-admin").resolve()


def awesome_skill_generate_root() -> Optional[Path]:
    p = os.environ.get(OMICOS_AWESOME_SG)
    if p:
        return Path(p).expanduser().resolve()
    # Default: sibling repo at ../awesome-skill-generate, if present.
    candidate = (_repo_root().parent / "awesome-skill-generate").resolve()
    return candidate if candidate.exists() else None


def targets_root() -> Path:
    p = os.environ.get(OMICOS_TARGETS_ROOT)
    if p:
        return Path(p).expanduser().resolve()
    return _repo_root() / "targets"


# --------------------------------------------------------------------------- #
# Conda env resolution                                                        #
# --------------------------------------------------------------------------- #


def find_conda_env_python(env_name: str) -> Optional[Path]:
    """Locate the python binary for a named conda env.

    Walks `conda info --envs` (matching both name-style and prefix-style envs,
    where prefix-style means `/scratch/.../env/<name>` with no alias), plus
    the common install roots and HPC-style scratch / OAK prefixes that
    sherlock + stanford-style hosts use. Returns None if not found.
    """
    if not env_name:
        return None
    seen: set[Path] = set()
    candidates: list[Path] = []

    # `conda info --envs` is the most reliable source if conda is on PATH.
    # The output has three column patterns:
    #   - named env:   `<name>     <prefix>`
    #   - active env:  `<name>  *  <prefix>`
    #   - prefix env:  `           <prefix>`   (no alias; only the path is shown)
    # We accept either column 0 == env_name OR basename(prefix) == env_name.
    if shutil.which("conda"):
        try:
            out = subprocess.run(
                ["conda", "info", "--envs"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout
            for line in out.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                parts = stripped.split()
                if not parts:
                    continue
                prefix = Path(parts[-1])
                if parts[0] == env_name or prefix.name == env_name:
                    candidates.append(prefix / "bin" / "python")
        except Exception:
            pass

    home = Path.home()
    user = os.environ.get("USER", home.name)

    if os.environ.get("CONDA_PREFIX"):
        base_path = Path(os.environ["CONDA_PREFIX"]).expanduser()
        candidates.append(base_path.parent / env_name / "bin" / "python")

    # Standard miniforge / mamba / miniconda / anaconda layouts.
    for base in (
        home / "miniforge3",
        home / "mambaforge",
        home / "miniconda3",
        home / "anaconda3",
        Path("/opt/conda"),
    ):
        candidates.append(base / "envs" / env_name / "bin" / "python")

    # HPC-style prefix locations (Sherlock, OAK, generic scratch).
    for base in (
        Path(f"/scratch/users/{user}/env"),
        Path(f"/scratch/users/{user}/envs"),
        Path(f"/scratch/users/{user}/conda/envs"),
        Path(f"/scratch/users/{user}/miniforge3/envs"),
        Path(f"/oak/stanford/groups/*/{user}/env"),     # placeholder; group glob handled below
        Path(f"/home/groups/*/{user}/env"),             # placeholder; group glob handled below
    ):
        candidates.append(base / env_name / "bin" / "python")

    # Glob expansion for the wildcard group paths above.
    for pattern in (
        f"/oak/stanford/groups/*/{user}/env/{env_name}/bin/python",
        f"/home/groups/*/{user}/env/{env_name}/bin/python",
    ):
        from glob import glob
        for hit in glob(pattern):
            candidates.append(Path(hit))

    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        if c.exists():
            return c
    return None


def find_conda_env_rscript(env_name: str) -> Optional[Path]:
    """Locate Rscript inside a conda env."""
    if not env_name:
        return None
    py = find_conda_env_python(env_name)
    if py is None:
        return None
    rscript = py.parent / "Rscript"
    return rscript if rscript.exists() else None


# --------------------------------------------------------------------------- #
# Reporting                                                                   #
# --------------------------------------------------------------------------- #


def describe(manifest_python: Optional[str] = None,
             manifest_r: Optional[str] = None) -> dict[str, object]:
    """Snapshot of the current resolved environment, for dumping to logs / smoke."""
    py_env = python_env(manifest_python)
    rn = r_env(manifest_r)
    return {
        "OMICOS_PYTHON_ENV": py_env,
        "OMICOS_R_ENV": rn,
        "OMICOS_ADMIN_ROOT": str(admin_root()),
        "OMICOS_AWESOME_SG": str(awesome_skill_generate_root()) if awesome_skill_generate_root() else None,
        "OMICOS_TARGETS_ROOT": str(targets_root()),
        "python_env_python": str(find_conda_env_python(py_env)) if find_conda_env_python(py_env) else None,
        "r_env_rscript": str(find_conda_env_rscript(rn)) if find_conda_env_rscript(rn) else None,
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]
