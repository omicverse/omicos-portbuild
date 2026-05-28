"""Inspect a cloned package's source code to extract per-module API surface.

Pure AST — does NOT require the target package to be installed. Given a
source root + a module dotted path (e.g. `lazyslide.tools`), walks the
module's `__init__.py` `__all__` (or every top-level callable if no
`__all__`), follows `from ._X import Y` re-exports to find the actual
definitions, and returns a structured table:

  {
    "module": "lazyslide.tools",
    "source_file": "src/lazyslide/tools/__init__.py",
    "exports": [
      {
        "name": "feature_extraction",
        "kind": "function",
        "defined_in": "src/lazyslide/tools/_features.py",
        "signature": "(wsi, model_or_path, ...)",
        "docstring_summary": "Extract features from each tile.",
        "branch_params": [
          {"param": "device", "literals": ["cpu", "cuda", "mps"]},
        ],
      },
      ...
    ],
  }

This is the deterministic substrate that `factory.source_grounding inspect`
hands to the agent so that even when tutorial pages 404, the agent has
ground-truth API names + signatures to fill the FILL markers from.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

BRANCH_PARAM_NAMES = frozenset({
    "method", "recipe", "backend", "mode", "source", "task", "provider",
    "model", "encoder", "device", "kind",
})

BRANCH_PARAM_SUFFIXES = ("_method", "_backend", "_mode", "_model", "_provider")


@dataclass
class BranchParamHit:
    param: str
    literals: list[str] = field(default_factory=list)


@dataclass
class Export:
    name: str
    kind: str  # "function" | "class" | "unknown"
    defined_in: Optional[str] = None
    signature: Optional[str] = None
    docstring_summary: Optional[str] = None
    branch_params: list[BranchParamHit] = field(default_factory=list)


@dataclass
class ModuleSurface:
    module: str
    source_file: Optional[str]
    exports: list[Export] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Source-root locators                                                        #
# --------------------------------------------------------------------------- #


def find_package_root(source_root: Path, package_name: str) -> Optional[Path]:
    """Given a cloned repo path, find the package's top-level package dir.

    Tries: <source_root>/<package>/__init__.py and
           <source_root>/src/<package>/__init__.py.
    """
    candidates = [
        source_root / package_name,
        source_root / "src" / package_name,
    ]
    for c in candidates:
        if (c / "__init__.py").exists():
            return c
    return None


def module_to_path(package_root: Path, module: str) -> Optional[Path]:
    """`lazyslide.tools` -> <package_root>/tools/__init__.py."""
    parts = module.split(".")
    if not parts:
        return None
    pkg_name = parts[0]
    rel_parts = parts[1:]
    p = package_root if package_root.name == pkg_name else package_root / pkg_name
    for sub in rel_parts:
        p = p / sub
    init_py = p / "__init__.py"
    if init_py.exists():
        return init_py
    # Single-file module (e.g. lazyslide.metrics if it's a .py not a package)
    p_py = p.with_suffix(".py")
    if p_py.exists():
        return p_py
    return None


# --------------------------------------------------------------------------- #
# AST walker                                                                  #
# --------------------------------------------------------------------------- #


def _parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return None


def _module_all(tree: ast.Module) -> Optional[list[str]]:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                        out: list[str] = []
                        for el in node.value.elts:
                            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                out.append(el.value)
                        return out
    return None


def _module_reexports(tree: ast.Module) -> dict[str, str]:
    """Return {imported_name: relative_module} from `from ._X import Y, Z`."""
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.level >= 1 and node.module:
                rel = "." * node.level + node.module
                for alias in node.names:
                    out[alias.asname or alias.name] = rel
    return out


def _top_level_defs(tree: ast.Module) -> dict[str, ast.stmt]:
    """`def name` / `class name` at module top level."""
    out: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = node
    return out


def _format_arg(arg: ast.arg, default: Optional[ast.AST]) -> str:
    s = arg.arg
    if arg.annotation is not None:
        try:
            s += ": " + ast.unparse(arg.annotation)
        except Exception:
            pass
    if default is not None:
        try:
            s += "=" + ast.unparse(default)
        except Exception:
            s += "=..."
    return s


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = fn.args
    parts: list[str] = []
    # positional-only + positional-or-keyword
    posonly = list(args.posonlyargs)
    positional = list(args.args)
    n_defaults = len(args.defaults)
    total_pos = len(posonly) + len(positional)
    defaults_pos = [None] * (total_pos - n_defaults) + list(args.defaults)
    for i, a in enumerate(posonly):
        parts.append(_format_arg(a, defaults_pos[i]))
    if posonly:
        parts.append("/")
    for i, a in enumerate(positional):
        parts.append(_format_arg(a, defaults_pos[len(posonly) + i]))
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        parts.append("*")
    for i, a in enumerate(args.kwonlyargs):
        d = args.kw_defaults[i] if i < len(args.kw_defaults) else None
        parts.append(_format_arg(a, d))
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def _docstring_summary(node: ast.stmt) -> Optional[str]:
    ds = ast.get_docstring(node)  # type: ignore[arg-type]
    if not ds:
        return None
    # First non-blank paragraph, condensed.
    paras = [p.strip() for p in ds.strip().split("\n\n") if p.strip()]
    if not paras:
        return None
    first = paras[0]
    # Drop the literal "Parameters" / NumPy doc block headers.
    if first.startswith("Parameters") and "----" in paras[0]:
        return None
    # Squash whitespace.
    return " ".join(first.split())


def _branch_param_name(name: str) -> bool:
    lower = name.lower()
    if lower in BRANCH_PARAM_NAMES:
        return True
    return any(lower.endswith(sfx) for sfx in BRANCH_PARAM_SUFFIXES)


def _branch_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[BranchParamHit]:
    """For each branch-ish param, try to extract `Literal[...]` annotation literals
    or default-value literal."""
    out: list[BranchParamHit] = []
    args = fn.args
    all_params = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    for a in all_params:
        if not _branch_param_name(a.arg):
            continue
        literals: list[str] = []
        if a.annotation is not None:
            literals.extend(_literal_args_from_annotation(a.annotation))
        if not literals:
            # Also consider Optional[Literal[...]] / Union[Literal[...], ...]
            pass
        out.append(BranchParamHit(param=a.arg, literals=literals))
    return out


def _literal_args_from_annotation(node: ast.AST) -> list[str]:
    """Extract string literals from `Literal['a', 'b', 'c']` or nested generics."""
    out: list[str] = []
    if isinstance(node, ast.Subscript):
        # Could be Literal[...] directly, or Optional[Literal[...]], etc.
        sub_name = _attr_or_name(node.value)
        if sub_name in ("Literal", "typing.Literal"):
            slice_node = node.slice
            if isinstance(slice_node, ast.Tuple):
                for el in slice_node.elts:
                    if isinstance(el, ast.Constant) and isinstance(el.value, str):
                        out.append(el.value)
            elif isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                out.append(slice_node.value)
        else:
            # Recurse into the slice for Optional[Literal[...]]
            out.extend(_literal_args_from_annotation(node.slice))
    elif isinstance(node, ast.Tuple):
        for el in node.elts:
            out.extend(_literal_args_from_annotation(el))
    elif isinstance(node, ast.BinOp):
        out.extend(_literal_args_from_annotation(node.left))
        out.extend(_literal_args_from_annotation(node.right))
    return out


def _attr_or_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return ast.unparse(node)
    return None


# --------------------------------------------------------------------------- #
# Top-level inspect()                                                         #
# --------------------------------------------------------------------------- #


def inspect_module(source_root: Path, package_name: str, module: str) -> ModuleSurface:
    """Inspect one dotted module under a cloned package source."""
    pkg_root = find_package_root(source_root, package_name)
    if pkg_root is None:
        return ModuleSurface(module=module, source_file=None,
                             notes=[f"package {package_name!r} not found under {source_root}"])
    init_path = module_to_path(pkg_root.parent, module) if module.startswith(package_name) \
                else module_to_path(pkg_root, f"{package_name}.{module}")
    if init_path is None:
        return ModuleSurface(module=module, source_file=None,
                             notes=[f"module {module!r} not found under {pkg_root}"])

    init_tree = _parse(init_path)
    if init_tree is None:
        return ModuleSurface(module=module, source_file=str(init_path),
                             notes=["failed to parse __init__.py"])

    surface = ModuleSurface(module=module, source_file=str(init_path))

    names = _module_all(init_tree) or list(_top_level_defs(init_tree).keys())
    reexports = _module_reexports(init_tree)
    top_defs = _top_level_defs(init_tree)

    for name in names:
        export = Export(name=name, kind="unknown")
        # If defined right here at top-level, easy.
        node = top_defs.get(name)
        defining_path = init_path
        if node is None and name in reexports:
            # Follow the re-export: relative module like '._features'
            rel = reexports[name]
            # ".X" → siblings of init_path; "..Y" → up one
            up = len(rel) - len(rel.lstrip("."))
            rel_mod = rel.lstrip(".")
            base = init_path.parent
            for _ in range(up - 1):
                base = base.parent
            target = base / (rel_mod + ".py")
            if not target.exists():
                target = base / rel_mod / "__init__.py"
            if target.exists():
                defining_path = target
                sub_tree = _parse(target)
                if sub_tree:
                    sub_defs = _top_level_defs(sub_tree)
                    node = sub_defs.get(name)
        if node is None:
            surface.exports.append(export)
            continue
        if isinstance(node, ast.ClassDef):
            export.kind = "class"
            export.signature = _class_signature(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            export.kind = "function"
            export.signature = _signature(node)
            export.branch_params = _branch_params(node)
        export.docstring_summary = _docstring_summary(node)
        export.defined_in = str(defining_path)
        surface.exports.append(export)

    return surface


def _class_signature(cls: ast.ClassDef) -> str:
    # If there's an __init__, use its signature.
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
            return _signature(node).replace("(self, ", "(", 1).replace("(self)", "()")
    return "()"


# --------------------------------------------------------------------------- #
# Markdown rendering                                                          #
# --------------------------------------------------------------------------- #


def render_markdown(surfaces: list[ModuleSurface], *, package: str) -> str:
    lines: list[str] = []
    lines.append(f"# Source Grounding — auto-inspected from `{package}` source")
    lines.append("")
    lines.append("Generated by `factory.interface_inspector` from a git clone of the upstream "
                 "package. The signatures below are AST-extracted from `__init__.py` and the "
                 "files it re-exports — they are facts about the cloned commit, not memory.")
    lines.append("")
    for surface in surfaces:
        lines.append(f"## `{surface.module}`")
        lines.append("")
        if surface.source_file:
            try:
                rel = Path(surface.source_file).resolve()
                lines.append(f"- Source file: `{rel.name}` (under "
                             f"`{rel.parent.name}/`)")
            except Exception:
                lines.append(f"- Source file: `{surface.source_file}`")
        for note in surface.notes:
            lines.append(f"- Note: {note}")
        if not surface.exports:
            lines.append("- (no exports detected)")
            lines.append("")
            continue
        lines.append("")
        lines.append("| Name | Kind | Signature | Summary |")
        lines.append("|---|---|---|---|")
        for ex in surface.exports:
            sig = (ex.signature or "?").replace("|", "/")
            summ = (ex.docstring_summary or "").replace("|", "/")
            lines.append(f"| `{ex.name}` | {ex.kind} | `{sig}` | {summ[:120]} |")
        # Branch params block
        branch_lines = []
        for ex in surface.exports:
            for bp in ex.branch_params:
                if bp.literals:
                    branch_lines.append(
                        f"- `{ex.name}({bp.param}=...)` → `{bp.literals}`"
                    )
                else:
                    branch_lines.append(
                        f"- `{ex.name}({bp.param}=...)` → branch param "
                        f"(no `Literal[...]` annotation; check source)"
                    )
        if branch_lines:
            lines.append("")
            lines.append("**Branch parameters:**")
            lines.extend(branch_lines)
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="factory.interface_inspector",
                                description="AST-inspect a cloned package's modules.")
    p.add_argument("source_root", help="Path to the cloned package repo.")
    p.add_argument("package", help="Top-level package name (e.g. lazyslide).")
    p.add_argument("modules", nargs="+",
                   help="Dotted module paths (e.g. lazyslide.tools).")
    p.add_argument("--format", choices=("md", "json"), default="md")
    args = p.parse_args(argv)
    src = Path(args.source_root).resolve()
    surfaces = [inspect_module(src, args.package, m) for m in args.modules]
    if args.format == "md":
        print(render_markdown(surfaces, package=args.package))
    else:
        print(json.dumps([asdict(s) for s in surfaces], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
