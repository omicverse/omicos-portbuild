"""Pre-flight environment checks.

Mirrors omicverse-rebuildr's `engine.smoke_test` ("[smoke] OK -- 5/5 checks
passed"). Run before any `omicos-portbuild build` to confirm the
environment can host the loop. Failures are reported as `[FAIL]` lines with
actionable remediation hints; exit code is the number of failed checks.

Checks (in order):

  1. Python >= 3.10
  2. PyYAML importable in the factory runtime
  3. OMICOS_PYTHON_ENV conda env is resolvable and has python
  4. OMICOS_R_ENV conda env is resolvable and has Rscript (warn-only — only
     fails when a target's manifest actually requires R)
  5. git on PATH (needed for `ingest.clone_and_fetch`)
  6. OMICOS_ADMIN_ROOT exists with agents/ + skills/ (warn-only)

Run:
    python -m factory.smoke_test
or:
    omicos-portbuild build ... --skip-smoke    # to bypass
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from factory import env as env_mod


@dataclass
class CheckResult:
    name: str
    ok: bool
    note: str = ""
    warn_only: bool = False


def check_python_version() -> CheckResult:
    v = sys.version_info[:2]
    return CheckResult(
        name="python >= 3.10",
        ok=v >= (3, 10),
        note=f"have {v[0]}.{v[1]}",
    )


def check_pyyaml() -> CheckResult:
    try:
        import yaml  # noqa: F401
        return CheckResult(name="PyYAML importable", ok=True, note="yes")
    except ImportError:
        return CheckResult(
            name="PyYAML importable", ok=False,
            note="pip install PyYAML  (or `pip install -e .` from this repo root)",
        )


def check_python_env() -> CheckResult:
    name = env_mod.python_env()
    py = env_mod.find_conda_env_python(name)
    if py is None:
        return CheckResult(
            name=f"OMICOS_PYTHON_ENV ({name}) reachable",
            ok=False,
            note=(
                f"no python found for conda env {name!r}; "
                f"create with `conda create -n {name} python=3.10` "
                f"or `export OMICOS_PYTHON_ENV=<existing_env>`."
            ),
        )
    return CheckResult(
        name=f"OMICOS_PYTHON_ENV ({name}) reachable",
        ok=True,
        note=str(py),
    )


def check_r_env() -> CheckResult:
    name = env_mod.r_env()
    rs = env_mod.find_conda_env_rscript(name)
    if rs is None:
        return CheckResult(
            name=f"OMICOS_R_ENV ({name}) reachable (warn-only)",
            ok=False, warn_only=True,
            note=(
                f"no Rscript found for conda env {name!r}; "
                f"R-side capabilities will fail until you "
                f"`conda create -n {name} r-base r-essentials` "
                f"or `export OMICOS_R_ENV=<existing_env>`."
            ),
        )
    return CheckResult(
        name=f"OMICOS_R_ENV ({name}) reachable (warn-only)",
        ok=True, note=str(rs),
    )


def check_git() -> CheckResult:
    g = shutil.which("git")
    return CheckResult(
        name="git on PATH",
        ok=g is not None,
        note=g or "install git (needed for `ingest.clone_and_fetch`)",
    )


def check_admin_root() -> CheckResult:
    root = env_mod.admin_root()
    agents = root / "agents"
    skills = root / "skills"
    if not root.exists():
        return CheckResult(
            name=f"OMICOS_ADMIN_ROOT ({root}) (warn-only)",
            ok=False, warn_only=True,
            note=(
                "path missing — standalone use is fine; duplicate-detection "
                "(Phase 0) and router synthesis (`route`) will simply see an "
                "empty registry. Set OMICOS_ADMIN_ROOT to an omicos-admin "
                "checkout to enable those features."
            ),
        )
    if not agents.is_dir() or not skills.is_dir():
        return CheckResult(
            name=f"OMICOS_ADMIN_ROOT ({root}) (warn-only)",
            ok=False, warn_only=True,
            note="found, but missing agents/ or skills/ subdirectories.",
        )
    return CheckResult(
        name=f"OMICOS_ADMIN_ROOT ({root}) (warn-only)",
        ok=True,
        note=f"{len(list(agents.glob('*.md')))} agents, "
             f"{sum(1 for d in skills.iterdir() if d.is_dir())} skills",
    )


CHECKS: list[Callable[[], CheckResult]] = [
    check_python_version,
    check_pyyaml,
    check_python_env,
    check_r_env,
    check_git,
    check_admin_root,
]


def run_all() -> tuple[list[CheckResult], int, int]:
    results = [fn() for fn in CHECKS]
    hard_fail = sum(1 for r in results if not r.ok and not r.warn_only)
    warn = sum(1 for r in results if not r.ok and r.warn_only)
    return results, hard_fail, warn


def _format(results: list[CheckResult], hard_fail: int, warn: int) -> str:
    lines: list[str] = []
    for r in results:
        tag = "[ OK ]" if r.ok else ("[WARN]" if r.warn_only else "[FAIL]")
        lines.append(f"{tag} {r.name}")
        if r.note:
            lines.append(f"         {r.note}")
    n = len(results)
    n_pass = sum(1 for r in results if r.ok)
    if hard_fail == 0:
        lines.append(f"[smoke] OK -- {n_pass}/{n} checks passed"
                     + (f" ({warn} warn-only)" if warn else ""))
    else:
        lines.append(f"[smoke] FAIL -- {hard_fail} of {n} required checks failed"
                     + (f", {warn} warn-only" if warn else ""))
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="factory.smoke_test")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    results, hard_fail, warn = run_all()
    if not args.quiet:
        print(_format(results, hard_fail, warn))
    return hard_fail


if __name__ == "__main__":
    sys.exit(_main())
