"""Acceptance harness — replaces upstream run_skill_acceptance.py.

Reads `assets/acceptance.json` from a candidate or best skill folder and
exercises its required_sections / required_paths / smoke_commands subject to
`validation_budget.smoke_mode` and `max_wall_seconds`.

Differences from upstream:
- We support being pointed at `targets/<id>/best/skill/` or `candidates/v00n/skill/`.
- `smoke_mode` is honored verbatim from upstream (full / import_only / skip).
- `max_wall_seconds` is enforced; per-command timeout is `timeout_seconds` (default 60).
- We allow `conda_env` resolution via `find_conda_env_python` from omicdev defaults.

assets/acceptance.json schema:

{
  "version": 1,
  "skill": "<id>",
  "sample_requests": ["..."],
  "required_description_terms": ["..."],
  "required_sections": ["## Goal", ...],
  "required_body_terms": ["..."],
  "required_paths": ["outputs/REPORT.html", ...],
  "smoke_commands": [
    {
      "name": "import_smoke",
      "tier": "import",
      "command": "${PYTHON} -c 'import lazyslide'",
      "conda_env": "omicdev",
      "timeout_seconds": 30,
      "expect_stdout_contains": [],
      "expect_stderr_contains": []
    }
  ],
  "validation_budget": {
    "smoke_mode": "import_only",
    "max_wall_seconds": 120,
    "skip_reason": ""
  }
}
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def run_acceptance(skill_dir: Path) -> list[str]:
    """Return a list of human-readable acceptance failures. Empty == pass."""
    errors: list[str] = []
    acceptance_path = skill_dir / "assets" / "acceptance.json"
    if not acceptance_path.exists():
        return [f"{skill_dir}: missing assets/acceptance.json"]

    case = json.loads(acceptance_path.read_text(encoding="utf-8"))

    skill_md_path = skill_dir / "SKILL.md"
    skill_text = skill_md_path.read_text(encoding="utf-8")

    if case.get("version") != 1:
        errors.append("acceptance version must be 1")

    sample_requests = case.get("sample_requests") or []
    if not isinstance(sample_requests, list) or not sample_requests:
        errors.append("sample_requests must be a non-empty list")

    body_lower = skill_text.lower()
    for term in [t.lower() for t in case.get("required_body_terms") or []]:
        if term not in body_lower:
            errors.append(f"body missing required term: {term!r}")

    for section in case.get("required_sections") or []:
        if section not in skill_text:
            errors.append(f"SKILL.md missing required section: {section!r}")

    for rel in case.get("required_paths") or []:
        target = (skill_dir / rel).resolve()
        if not target.exists():
            errors.append(f"required path does not exist: {rel}")

    errors.extend(_run_smoke(skill_dir, case))
    return errors


def _run_smoke(skill_dir: Path, case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    budget = case.get("validation_budget", {}) or {}
    smoke_mode = budget.get("smoke_mode", "full")
    max_wall = budget.get("max_wall_seconds")

    if smoke_mode == "skip":
        reason = budget.get("skip_reason") or "no reason given"
        print(f"[acceptance] smoke skipped ({reason})")
        return errors

    wall_start = time.monotonic()
    for sm in case.get("smoke_commands") or []:
        if max_wall is not None and (time.monotonic() - wall_start) >= max_wall:
            print(f"[acceptance] wall budget {max_wall}s reached; stopping")
            break

        tier = sm.get("tier", "full")
        if smoke_mode == "import_only" and tier != "import":
            print(f"[acceptance] skip {sm.get('name')!r} (import_only mode)")
            continue

        cmd = sm.get("command")
        if not cmd:
            errors.append(f"smoke {sm.get('name')!r}: missing command")
            continue

        cmd = _expand_python(cmd, sm)
        env_prefix = ""
        for k, v in sorted((sm.get("env") or {}).items()):
            env_prefix += f"{k}={shlex.quote(str(v))} "
        full_cmd = f"{env_prefix}{cmd}".strip()

        per_to = sm.get("timeout_seconds", 60)
        if max_wall is not None:
            remaining = max_wall - (time.monotonic() - wall_start)
            per_to = min(per_to, max(1, int(remaining)))

        try:
            proc = subprocess.run(
                ["/bin/bash", "-lc", full_cmd],
                cwd=skill_dir,
                capture_output=True,
                text=True,
                timeout=per_to,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"smoke {sm.get('name')!r} timed out after {per_to}s")
            continue

        if proc.returncode != 0:
            errors.append(
                f"smoke {sm.get('name')!r} exit={proc.returncode}\n"
                f"stdout: {proc.stdout.strip()[:400]}\n"
                f"stderr: {proc.stderr.strip()[:400]}"
            )
            continue

        for needle in sm.get("expect_stdout_contains") or []:
            if needle not in proc.stdout:
                errors.append(f"smoke {sm.get('name')!r} stdout missing {needle!r}")
        for needle in sm.get("expect_stderr_contains") or []:
            if needle not in proc.stderr:
                errors.append(f"smoke {sm.get('name')!r} stderr missing {needle!r}")

    return errors


def _expand_python(cmd: str, sm: dict[str, Any]) -> str:
    if "${PYTHON}" not in cmd:
        return cmd
    pyp = sm.get("python_path")
    if pyp:
        return cmd.replace("${PYTHON}", shlex.quote(str(pyp)))
    # Resolution order for the conda env:
    #   1. acceptance.json sm["conda_env"]
    #   2. OMICOS_PYTHON_ENV
    #   3. manifest.python_env (callers should pre-substitute if they want this)
    #   4. built-in default `omicdev`
    from factory import env as env_mod
    env_name = sm.get("conda_env") or env_mod.python_env()
    resolved = env_mod.find_conda_env_python(env_name)
    if resolved:
        return cmd.replace("${PYTHON}", shlex.quote(str(resolved)))
    # Last resort: hand it to `conda run`. Slower but works as long as `conda`
    # is on PATH.
    return f"conda run -n {shlex.quote(env_name)} {cmd.replace('${PYTHON}', 'python')}"


def _main() -> int:
    p = argparse.ArgumentParser(prog="factory.acceptance")
    p.add_argument("skill_dir", help="Path to a skill folder (containing SKILL.md + assets/acceptance.json)")
    args = p.parse_args()
    errors = run_acceptance(Path(args.skill_dir).resolve())
    if errors:
        print("acceptance failed:")
        for e in errors:
            print(f"- {e}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
