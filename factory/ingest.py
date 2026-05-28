"""Phase 2a — ingest. git clone the package source and fetch docs into
targets/<id>/upstream-src and targets/<id>/upstream-docs.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from factory.manifest import read_manifest


def clone_and_fetch(target_dir: Path) -> None:
    manifest = read_manifest(target_dir)
    if manifest.kind != "capability":
        return  # routers don't ingest packages

    src_dir = target_dir / "upstream-src"
    docs_dir = target_dir / "upstream-docs"
    src_dir.mkdir(exist_ok=True)
    docs_dir.mkdir(exist_ok=True)

    if manifest.source and not (src_dir / ".git").exists():
        print(f"[ingest] git clone --depth 1 {manifest.source} → {src_dir}")
        subprocess.run(
            ["git", "clone", "--depth", "1", manifest.source, str(src_dir)],
            check=True,
        )
    else:
        print(f"[ingest] source already present at {src_dir}; skipping clone")

    if manifest.docs:
        index_file = docs_dir / "index.html"
        if not index_file.exists():
            print(f"[ingest] fetching docs index → {index_file}")
            _fetch_url(manifest.docs, index_file)
        else:
            print(f"[ingest] docs index already present; skipping fetch")


def _fetch_url(url: str, dest: Path) -> None:
    try:
        import requests
    except ImportError as exc:
        raise NotImplementedError(
            f"requests not available; cannot fetch {url}. Install requests."
        ) from exc
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    dest.write_text(resp.text, encoding="utf-8")
