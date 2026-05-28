"""Phase 2b — source grounding (rebuildr-style auto-ingest + inspect).

OmicOS-targeted module. Two parts:

  1. `ingest <package_root>` — if a top-level package manifest is present and
     `<package_root>/upstream-src/` doesn't exist yet, `git clone --depth 1`
     the package source there.

  2. `inspect <capability_target>` — read the capability's manifest
     (`key_modules` / `key_apis`), call `factory.interface_inspector` on each
     module, and write a SOURCE_GROUNDING-style markdown into
     `candidates/v_latest/skill/references/source-grounding.md` PLUS a
     capability-level `SOURCE_GROUNDING.md`. This replaces the previous
     stub that just listed top-level python packages.

After `source_grounding inspect`, the agent fills agent.md and SKILL.md
with REAL API names + signatures extracted from source — even when the
tutorial pages 404.

CLI:
    python -m factory.source_grounding ingest <package_root>
    python -m factory.source_grounding inspect <capability_target>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from factory.interface_inspector import inspect_module, render_markdown
from factory.manifest import read_manifest

UPSTREAM_DIRNAME = "upstream-src"


# --------------------------------------------------------------------------- #
# ingest (git clone)                                                          #
# --------------------------------------------------------------------------- #


def cmd_ingest(package_root: Path) -> dict[str, Any]:
    """Clone the package's source into <package_root>/upstream-src/, if not done.

    Reads the source URL from the top-level <package_root>/manifest.yaml.
    """
    manifest = read_manifest(package_root)
    if not manifest.source:
        raise ValueError(
            f"no `source:` URL in {package_root / 'manifest.yaml'}; cannot ingest"
        )
    dst = package_root / UPSTREAM_DIRNAME
    if dst.exists() and (dst / ".git").is_dir():
        return {"action": "skipped", "reason": "already cloned",
                "path": str(dst)}
    if dst.exists():
        import shutil
        shutil.rmtree(dst)
    print(f"[ingest] git clone --depth 1 {manifest.source} → {dst}")
    subprocess.run(
        ["git", "clone", "--depth", "1", manifest.source, str(dst)],
        check=True,
    )
    return {"action": "cloned", "source": manifest.source, "path": str(dst)}


# --------------------------------------------------------------------------- #
# inspect (run interface_inspector + write source-grounding.md)               #
# --------------------------------------------------------------------------- #


def cmd_inspect(capability_target: Path, *,
                upstream_src: Optional[Path] = None) -> dict[str, Any]:
    """Inspect the capability's `key_modules` against the cloned source and
    write `references/source-grounding.md` into the latest candidate."""
    manifest = read_manifest(capability_target)
    if not manifest.package:
        raise ValueError(
            f"capability manifest at {capability_target} has no `package:` field"
        )

    # Find upstream-src: walk up looking for the package root.
    # Layouts: <root>/<cap>/upstream-src/ (legacy capability)
    #          <root>/skills/<id>/.. → <root>/upstream-src/ (new layout)
    #          <root>/agents/<id>/..  → <root>/upstream-src/ (new layout)
    if upstream_src is None:
        for ancestor in (capability_target,
                         capability_target.parent,
                         capability_target.parent.parent):
            cand = ancestor / UPSTREAM_DIRNAME
            if cand.is_dir():
                upstream_src = cand
                break
        if upstream_src is None:
            raise FileNotFoundError(
                f"no {UPSTREAM_DIRNAME}/ found near {capability_target}; "
                f"run `factory.source_grounding ingest <package_root>` first"
            )

    # Collect modules from manifest.key_modules (which is stored in extra).
    extra = manifest.extra or {}
    key_modules = extra.get("key_modules") or []
    if not key_modules:
        raise ValueError(
            f"capability manifest at {capability_target} has no `key_modules` "
            f"in extra fields; populate them via the partition step"
        )

    surfaces = []
    for mod in key_modules:
        if not isinstance(mod, str):
            continue
        # Skip non-importable hints like "scikit-survival" / "wsidata".
        if mod.startswith(manifest.package):
            surfaces.append(inspect_module(upstream_src, manifest.package, mod))

    md = render_markdown(surfaces, package=manifest.package)

    # Write to candidate references/ (latest vNNN) AND to the capability root.
    cands = sorted((capability_target / "candidates").glob("v*"))
    if cands:
        refs = cands[-1] / "skill" / "references"
        refs.mkdir(parents=True, exist_ok=True)
        (refs / "source-grounding.md").write_text(md, encoding="utf-8")

    (capability_target / "SOURCE_GROUNDING.md").write_text(md, encoding="utf-8")

    return {
        "capability": manifest.capability_id,
        "package": manifest.package,
        "n_modules_inspected": len(surfaces),
        "n_exports": sum(len(s.exports) for s in surfaces),
        "wrote": [
            str((capability_target / "SOURCE_GROUNDING.md").relative_to(capability_target.parent)),
        ] + ([str((cands[-1] / "skill" / "references" / "source-grounding.md").relative_to(capability_target.parent))] if cands else []),
    }


# --------------------------------------------------------------------------- #
# Backwards-compat shim for cli.cmd_build                                     #
# --------------------------------------------------------------------------- #


def run(target_dir: Path) -> None:
    """Legacy single-capability shim used by `omicos-portbuild build`.

    Tries the new auto-inspect path; falls back to a stub if upstream-src
    can't be located.
    """
    out = target_dir / "SOURCE_GROUNDING.md"
    if out.exists():
        return
    try:
        cmd_inspect(target_dir)
        return
    except (FileNotFoundError, ValueError) as e:
        out.write_text(
            f"# Source Grounding\n\nstub — {e}\n\n"
            "Run `factory.source_grounding ingest <package_root>` then "
            "`factory.source_grounding inspect <this_capability>` to populate.\n",
            encoding="utf-8",
        )


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="factory.source_grounding",
        description="git clone the upstream source + AST-extract per-capability API surface.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="git clone the package source into upstream-src/.")
    pi.add_argument("package_root")

    pn = sub.add_parser("inspect", help="Write source-grounding.md from the cloned source.")
    pn.add_argument("capability_target")
    pn.add_argument("--upstream-src", default=None,
                    help="Override the cloned-source location.")

    args = p.parse_args(argv)
    try:
        if args.cmd == "ingest":
            out = cmd_ingest(Path(args.package_root).resolve())
        elif args.cmd == "inspect":
            out = cmd_inspect(
                Path(args.capability_target).resolve(),
                upstream_src=Path(args.upstream_src).resolve() if args.upstream_src else None,
            )
        else:
            return 2
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as e:
        print(f"[source_grounding] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
