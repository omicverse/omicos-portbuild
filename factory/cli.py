"""omicos-portbuild command-line entry point.

Three subcommands:

  build    — phase 0–5 for one capability (terminal agent + skill bundle)
  route    — phase 0–5 for a router target synthesized from admin registry
  publish  — phase 6: copy targets/<id>/best/ into omicos-admin/{agents,skills}/

Each subcommand currently wires up the protocol-level orchestration but the
LLM-driven stages (draft, optimizer, scorer) are NotImplementedError stubs in
the corresponding modules; this CLI is wired end-to-end so that the orchestrator
itself is testable before any model call is made.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from factory import env as env_mod  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS_ROOT = env_mod.targets_root()


# --------------------------------------------------------------------------- #
# Subcommand: build                                                           #
# --------------------------------------------------------------------------- #


def cmd_build(args: argparse.Namespace) -> int:
    from factory import discovery, ingest, partition, source_grounding
    from factory import draft, optimizer, scorer, smoke_test
    from factory.manifest import write_initial_manifest

    target_dir = DEFAULT_TARGETS_ROOT / args.capability_id
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"[build] capability_id={args.capability_id}")
    print(f"[build] target dir   ={target_dir}")

    # --- Pre-flight: smoke test (rebuildr-style) ------------------------- #
    if not args.skip_smoke:
        results, hard_fail, warn = smoke_test.run_all()
        print(smoke_test._format(results, hard_fail, warn))
        if hard_fail > 0:
            print(f"[build] smoke had {hard_fail} hard failure(s); "
                  f"fix them or re-run with --skip-smoke")
            return 4

    # --- Phase 0: discovery ---------------------------------------------- #
    print("[build] phase 0: discovery")
    discovery_md = discovery.run(
        capability_id=args.capability_id,
        package=args.package,
        source=args.source,
        docs=args.docs,
        admin_root=args.admin_root,
        target_dir=target_dir,
    )
    if discovery_md.get("stop"):
        print(f"[build] STOP — {discovery_md['stop_reason']}")
        return 0

    # --- Persist manifest ------------------------------------------------ #
    write_initial_manifest(
        target_dir=target_dir,
        capability_id=args.capability_id,
        kind="capability",
        package=args.package,
        source=args.source,
        docs=args.docs,
        notebooks=args.notebooks.split(",") if args.notebooks else [],
        cost_profile=args.cost_profile,
        python_env=args.python_env,
        r_env=args.r_env,
    )

    # --- Phase 1: partition --------------------------------------------- #
    print("[build] phase 1: partition")
    partition.run(target_dir=target_dir)

    # --- Phase 2: source grounding --------------------------------------- #
    print("[build] phase 2: source grounding")
    ingest.clone_and_fetch(target_dir=target_dir)
    source_grounding.run(target_dir=target_dir)

    # --- Phase 3: draft v000 (agent + skill PAIR) ----------------------- #
    print("[build] phase 3: draft v000 (agent.md + skill/SKILL.md)")
    draft.run(target_dir=target_dir)
    fills = draft.find_fill_markers(target_dir)
    n_fills = sum(len(v) for v in fills.values())
    if n_fills:
        print(f"[build] phase 3 left {n_fills} <FILL:> markers across "
              f"{len(fills)} file(s). NEXT STEPS:")
        print(f"  1. Read targets/{args.capability_id}/DRAFT_INSTRUCTIONS.md")
        print(f"  2. Open candidates/v000/agent.md and candidates/v000/skill/SKILL.md")
        print(f"  3. Replace every <FILL: …> marker (read PARTITION.md + "
              f"SOURCE_GROUNDING.md first)")
        print(f"  4. Verify: python -m factory.draft check targets/{args.capability_id}")
        print(f"  5. Start the SkillOpt loop: python -m factory.optimizer init "
              f"targets/{args.capability_id}")
        # We deliberately do NOT fail; the build "completes" Phase 0–3.
        # Phase 4–5 are agent-driven and live downstream of the FILL pass.
        if args.skip_optimize:
            return 0
        print(f"[build] phase 5 deferred until <FILL:>s are resolved.")
        return 0

    # --- Phase 4: eval harness ------------------------------------------ #
    print("[build] phase 4: eval harness")
    # eval_harness scaffolding happens inside partition + draft for now;
    # later commits can split it out.

    # --- Phase 5: SkillOpt loop ----------------------------------------- #
    if args.skip_optimize:
        print("[build] phase 5: skipped (--skip-optimize)")
        return 0
    print("[build] phase 5: SkillOpt loop")
    optimizer.run(target_dir=target_dir)

    # --- Score the best ------------------------------------------------- #
    score_report = scorer.run(target_dir=target_dir)
    print(f"[build] verdict={score_report.get('verdict')} "
          f"score={score_report.get('weighted_score')}/100")
    return 0


# --------------------------------------------------------------------------- #
# Subcommand: route                                                           #
# --------------------------------------------------------------------------- #


def cmd_route(args: argparse.Namespace) -> int:
    from factory import route as route_mod
    from factory.manifest import write_initial_manifest

    capability_id = f"_router_{args.domain.replace('-', '_')}"
    target_dir = DEFAULT_TARGETS_ROOT / capability_id
    target_dir.mkdir(parents=True, exist_ok=True)

    members = route_mod.resolve_members(
        domain=args.domain,
        members_arg=args.members,
        admin_root=args.admin_root,
    )
    if len(members) < 2:
        print(
            f"[route] domain '{args.domain}' has only {len(members)} member(s); "
            f"router requires >= 2. Skipping."
        )
        return 0

    print(f"[route] domain={args.domain}  members={[m['id'] for m in members]}")

    write_initial_manifest(
        target_dir=target_dir,
        capability_id=capability_id,
        kind="router-synthesis",
        package=None,
        source=None,
        docs=None,
        notebooks=[],
        cost_profile="text-only",
        python_env=args.python_env,
        r_env=args.r_env,
        extra={"domain": args.domain, "members": members},
    )
    route_mod.draft_router(target_dir=target_dir, members=members)
    if not args.skip_optimize:
        from factory import optimizer, scorer
        optimizer.run(target_dir=target_dir)
        scorer.run(target_dir=target_dir)
    return 0


# --------------------------------------------------------------------------- #
# Subcommand: publish                                                         #
# --------------------------------------------------------------------------- #


def cmd_publish(args: argparse.Namespace) -> int:
    """Render the target's `best/` into a portable agent + skill bundle.

    The bundle is the deliverable; deploy is the operator's concern.
    Default output is `<target_dir>/published/`; override with `--out`.
    Refuses to write into any path containing `omicos-admin` (anti-write
    policy for OmicOS deploy checkouts).
    """
    from factory import publisher

    target_dir = Path(args.target_dir).resolve()
    if not (target_dir / "best").is_dir():
        print(f"[publish] error: no best/ subdir in {target_dir}", file=sys.stderr)
        return 2

    out_dir = Path(args.out).resolve() if args.out else None
    result = publisher.run(target_dir=target_dir, out_dir=out_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("published") else 1


# --------------------------------------------------------------------------- #
# Argparse                                                                    #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="omicos-portbuild",
        description="SkillOpt-style factory for OmicOS agent + skill bundles.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # build ---------------------------------------------------------------- #
    pb = sub.add_parser("build", help="Generate an agent + skill bundle from a package.")
    pb.add_argument("--capability-id", required=True)
    pb.add_argument("--package", required=True, help="PyPI / conda package name")
    pb.add_argument("--source", required=True, help="GitHub URL of upstream source")
    pb.add_argument("--docs", required=True, help="Top-level docs URL (readthedocs etc.)")
    pb.add_argument("--notebooks", default="", help="Comma-separated relative notebook paths")
    pb.add_argument("--cost-profile",
                    choices=("cheap", "moderate", "gpu-required", "infeasible-local"),
                    default="moderate")
    pb.add_argument("--python-env", default=env_mod.python_env(),
                    help="conda env for rollouts; defaults to $OMICOS_PYTHON_ENV "
                         "or 'omicdev'")
    pb.add_argument("--r-env", default=env_mod.r_env(),
                    help="conda env for R-side capabilities; defaults to "
                         "$OMICOS_R_ENV or 'CMAP'")
    pb.add_argument("--admin-root", default=str(env_mod.admin_root()),
                    help="path to omicos-admin; defaults to $OMICOS_ADMIN_ROOT "
                         "or ../omicos-admin")
    pb.add_argument("--skip-optimize", action="store_true",
                    help="Stop after phase 3 (draft v000); do not run the SkillOpt loop.")
    pb.add_argument("--skip-smoke", action="store_true",
                    help="Bypass the pre-flight smoke checks (factory.smoke_test).")
    pb.add_argument("--check", action="store_true",
                    help="Run discovery only and exit.")
    pb.set_defaults(func=cmd_build)

    # route ---------------------------------------------------------------- #
    pr = sub.add_parser("route", help="Synthesize a router from the admin registry.")
    pr.add_argument("--domain", required=True, help="Domain key (e.g. spatial-he)")
    pr.add_argument("--members", default="auto",
                    help="Comma-separated agent ids OR 'auto' to scan admin registry")
    pr.add_argument("--admin-root", default=str(env_mod.admin_root()))
    pr.add_argument("--python-env", default=env_mod.python_env())
    pr.add_argument("--r-env", default=env_mod.r_env())
    pr.add_argument("--skip-optimize", action="store_true")
    pr.set_defaults(func=cmd_route)

    # publish -------------------------------------------------------------- #
    pp = sub.add_parser(
        "publish",
        help="Render <target_dir>/best/ into a portable agent + skill bundle. "
             "Deploy (cp into your agent runtime) is a separate step.",
    )
    pp.add_argument("target_dir", help="Path to <pkg_root>/<kind>/<id>")
    pp.add_argument("--out", default=None,
                    help="Output folder for the bundle "
                         "(default: <target_dir>/published).")
    pp.set_defaults(func=cmd_publish)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    try:
        return args.func(args)
    except NotImplementedError as e:
        print(f"[cli] not implemented yet: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
