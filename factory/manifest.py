"""manifest.yaml read/write for one target.

Manifest is the immutable contract written before the optimizer loop starts.
Splits, thresholds, package metadata, and cost profile all freeze here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


MANIFEST_FILENAME = "manifest.yaml"


@dataclass
class ManifestSplits:
    train_ratio: float = 0.2
    selection_ratio: float = 0.1
    test_ratio: float = 0.7


@dataclass
class ManifestOptimizer:
    epochs: int = 4
    rollout_batch_size_B: int = 40
    minibatch_size_Bm: int = 8
    edit_budget_Lt: int = 4
    lr_schedule: str = "constant"
    lr_floor: int = 2
    slow_update_samples: int = 20
    meta_skill_enabled: bool = True


@dataclass
class ManifestQuality:
    pass_threshold: int = 75
    hard_gate_min: int = 3


@dataclass
class Manifest:
    capability_id: str
    kind: str = "capability"  # "capability" | "skill" | "agent" | "router-synthesis"
    package: Optional[str] = None
    source: Optional[str] = None
    docs: Optional[str] = None
    notebooks: list[str] = field(default_factory=list)
    cost_profile: str = "moderate"
    python_env: str = "omicdev"
    r_env: str = "CMAP"
    splits: ManifestSplits = field(default_factory=ManifestSplits)
    optimizer: ManifestOptimizer = field(default_factory=ManifestOptimizer)
    quality: ManifestQuality = field(default_factory=ManifestQuality)
    factory_version: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(d.pop("extra"))  # flatten extra to top level for readability
        return d


def write_initial_manifest(
    target_dir: Path,
    capability_id: str,
    kind: str,
    package: Optional[str],
    source: Optional[str],
    docs: Optional[str],
    notebooks: list[str],
    cost_profile: str,
    python_env: str = "omicdev",
    r_env: str = "CMAP",
    extra: Optional[dict[str, Any]] = None,
) -> Path:
    """Write manifest.yaml. Refuses to overwrite an existing manifest."""
    from factory import __version__ as _fv
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        capability_id=capability_id,
        kind=kind,
        package=package,
        source=source,
        docs=docs,
        notebooks=[n for n in notebooks if n],
        cost_profile=cost_profile,
        python_env=python_env,
        r_env=r_env,
        factory_version=_fv,
        extra=extra or {},
    )
    path = target_dir / MANIFEST_FILENAME
    if path.exists():
        raise FileExistsError(
            f"manifest already exists at {path}; refusing to overwrite. "
            f"Delete it manually to re-init."
        )
    return _write(manifest, path)


def read_manifest(target_dir: Path | str) -> Manifest:
    if yaml is None:  # pragma: no cover
        raise RuntimeError("PyYAML not available; cannot read manifest")
    path = Path(target_dir) / MANIFEST_FILENAME
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _from_dict(data)


def _write(manifest: Manifest, path: Path) -> Path:
    if yaml is None:  # pragma: no cover
        # Fallback: pseudo-YAML formatter for environments without PyYAML.
        path.write_text(_naive_yaml(manifest.to_dict()), encoding="utf-8")
        return path
    path.write_text(
        yaml.safe_dump(manifest.to_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _from_dict(data: dict[str, Any]) -> Manifest:
    splits = data.get("splits", {}) or {}
    optimizer = data.get("optimizer", {}) or {}
    quality = data.get("quality", {}) or {}
    known = {
        "capability_id", "kind", "package", "source", "docs", "notebooks",
        "cost_profile", "python_env", "r_env",
        "splits", "optimizer", "quality", "factory_version",
    }
    extra = {k: v for k, v in data.items() if k not in known}
    return Manifest(
        capability_id=data["capability_id"],
        kind=data.get("kind", "capability"),
        package=data.get("package"),
        source=data.get("source"),
        docs=data.get("docs"),
        notebooks=list(data.get("notebooks") or []),
        cost_profile=data.get("cost_profile", "moderate"),
        python_env=data.get("python_env", "omicdev"),
        r_env=data.get("r_env", "CMAP"),
        splits=ManifestSplits(**splits) if splits else ManifestSplits(),
        optimizer=ManifestOptimizer(**optimizer) if optimizer else ManifestOptimizer(),
        quality=ManifestQuality(**quality) if quality else ManifestQuality(),
        factory_version=data.get("factory_version", ""),
        extra=extra,
    )


def _naive_yaml(data: Any, indent: int = 0) -> str:
    """Minimal YAML emitter for the fallback path. Not for production."""
    pad = "  " * indent
    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines.append(_naive_yaml(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {v!r}")
        return "\n".join(lines)
    if isinstance(data, list):
        if not data:
            return f"{pad}[]"
        return "\n".join(f"{pad}- {item!r}" for item in data)
    return f"{pad}{data!r}"
