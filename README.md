# omicos-portbuild

**Port a third-party scientific Python package into a portable
`agent + skill` bundle. The bundle is the deliverable — `cp` it
into Claude Code, Codex, OpenCode, OmicOS, or any other agent runtime
that reads YAML-frontmatter `agent.md` + `skills/<id>/SKILL.md`.**

`omicos-portbuild` is the agent-side sibling of
[`omicverse-rebuildr`](https://github.com/omicverse/rebuildr):

| Tool | Job | Output |
|---|---|---|
| `omicverse-rebuildr` | Port an R / Bioconductor package to a pure-Python `py-<pkg>` mirror with R-parity gate | a pip-installable wheel |
| **`omicos-portbuild`** | Port a third-party Python package to a runtime-portable `agent + skill` bundle with rubric-parity gate | a `<target>/published/{agents,skills}/` tree, ready to `cp` |

Same kit shape (`README + PROTOCOL + engine + templates`), same agent-driven
loop discipline (rebuildr's two-agent inner loop ↔ portbuild's SkillOpt
self-review), same "never silently ship" gate (rebuildr's parity gate ↔
portbuild's RUBRIC + `key_apis` grounding + frontmatter schema check).

The bundle layout — `agents/<id>.md` + `skills/<id>/SKILL.md` with
optional `references/`, `assets/`, `scripts/` — is the **common shape**
every supported runtime reads. Claude Code, Codex, and OpenCode look for
`name` + `description` in the frontmatter (Claude Code adds `tools`,
`model`); OmicOS adds a stricter schema with `category`, `tier`,
`owns_skills`, etc. portbuild generates the **OmicOS superset** because
emitting to the strictest schema means the same files are also valid for
every runtime that ignores unknown frontmatter keys. The first
validation target is
[LazySlide](https://github.com/RendeiroLab/LazySlide) (direct H&E
whole-slide analysis); the second is squidpy (spatial transcriptomics).

## What portbuild does, and what it doesn't

| portbuild does | the operator does |
|---|---|
| Ingest tutorials + source for one Python package | Decide which capabilities to port |
| Scaffold a v000 agent + skill with `<FILL:>` markers | Fill them in (via `factory.iterate`, in conversation) |
| Run the SkillOpt loop — rubric scoring + evidence-keyword check + edit/apply gate | — |
| Validate frontmatter schema + `key_apis` grounding before publish | — |
| Output `<target>/published/{agents,skills}/` — pure files, no install scripts | `cp` the bundle into wherever the runtime reads (`~/.claude/`, a private OmicOS admin checkout, …) |

portbuild **never writes install scripts, configuration files, or deploy
hooks** into the bundle. The bundle is just `agents/<id>.md` +
`skills/<id>/…`. Deployment is intentionally not portbuild's concern —
no two operators have the same deploy target.

## OmicOS specifics, where they apply

The OmicOS-flavored elements (the strict frontmatter schema, the
`Routing Fitness` rubric dimension, the cross-modality fabrication block,
the `omicdev` / `CMAP` conda-env defaults) are what portbuild *generates*
because **OmicOS is the strictest deploy target it supports**. Generating
to the OmicOS shape means the same files are also valid for every
less-strict runtime that ignores extras.

The single hard rule: `publisher.run()` refuses to write into any path
whose resolved location contains `omicos-admin/`. This protects against
`iterate.cmd_finalize`'s auto-republish accidentally overwriting an
OmicOS-admin deploy checkout. If you don't use OmicOS, the check is just
dead weight; if you do, it's a safety rail.

`OMICOS_ADMIN_ROOT` is **optional** and only affects two read-only
convenience flows:

| Convenience | What it does | Without `OMICOS_ADMIN_ROOT` |
|---|---|---|
| `discovery.py` Phase 0 | "Does this capability already exist in OmicOS admin?" | reports "no admin set; proceeding" |
| `route.py --members auto` | Scan an admin agent registry to populate router membership | returns empty, prints notice, exits cleanly |

Neither blocks the generation pipeline. There is no expectation that
you, the reader, have an `omicos-admin/` checkout — it is a private
server-side repository specific to OmicOS deployments.

## What this is

A controllable, audit-able text-space training pipeline for agent skills.
The reflection / edit / validate loop is the SkillOpt algorithm
([arXiv:2605.23904v2](https://arxiv.org/abs/2605.23904)), implemented
faithfully (Algorithm 1 + the 8 prompt contracts in §C.2). The
notebook → skill generation step is informed by Qiu Lab's
[awesome-skill-generate](https://github.com/aristoteleo/awesome-skill-generate)
but the prompts, validator, and rubric here are rewritten for the
biology / multimodal / OmicOS-superset frontmatter.

The deliverable per capability is a self-contained `<target>/` directory
that mirrors omicverse-rebuildr's per-port folder convention:

```
<pkg-root>/<kind>/<capability_id>/
├── manifest.yaml
├── DISCOVERY.md
├── PARTITION.md
├── SOURCE_GROUNDING.md
├── EVAL_PLAN.md
├── eval/{train,selection,test}.yaml
├── _trajectory/                  ← full LLM-side audit trail
│   ├── timeline.jsonl            ← append-only event log
│   └── eNN/sNN/{rollouts,analyst_*,merge_*,ranking}.json
├── _meta_skill/epoch_NN.md       ← optimizer-side; NEVER copied
├── candidates/v000…v00n/         ← every SkillOpt iteration, kept on disk
│   └── edit_apply_report.json
├── ITERATION_LOG.md              ← rebuildr-style narrative, re-rendered per event
├── EVOLUTION_PLOT.svg            ← rebuildr-style two-panel plot
├── SCORE_REPORT.md
├── best/                         ← internal factory output (agent + skill co-located)
│   ├── agent.md
│   ├── skill/{SKILL.md, references/, assets/, scripts/}
│   └── routing_metadata.yaml
└── published/                    ← portable bundle, ready to cp
    ├── agents/<id>.md
    └── skills/<skill_id>/{SKILL.md, references/, assets/, scripts/}
```

There is **no `HOW_TO_INSTALL.md` or config file** in `published/`. The
bundle is pure artifacts; `cp` the contents of `published/agents/` and
`published/skills/` into wherever your runtime reads from.

## Pipeline at a glance

```
                ┌──────────────────────────────────────┐
                │  INPUT                               │
                │   • a Python package                 │
                │   • source URL, docs URL, notebooks  │
                └──────────────────┬───────────────────┘
                                   ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ Phase 0  discovery  (optional)                              │
   │   factory.discovery  ── reads OMICOS_ADMIN_ROOT for dedup;  │
   │                        warn-only when admin not set         │
   └────────────────────────────┬────────────────────────────────┘
                                ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ Phase 1  partition   one package → N capabilities           │
   │   factory.partition init    → stub <pkg>/partition.json     │
   │                                ↓ agent fills tutorials      │
   │   factory.partition emit    → scaffold per capability:      │
   │                                 <pkg>/skills/<id>/          │
   │                                 <pkg>/agents/<id>/          │
   │   factory.partition update  ← non-destructive re-sync       │
   └────────────────────────────┬────────────────────────────────┘
                                ▼
                    (Phases 2–6 run per <kind>/<id>)
                                │
                                ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ Phase 2  source_grounding                                   │
   │   git clone + AST inspector + docstring parse               │
   │   → SOURCE_GROUNDING.md                                     │
   └────────────────────────────┬────────────────────────────────┘
                                ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ Phase 3  draft v000                                         │
   │   factory.draft init     ── auto-pre-fill Quick Workflow    │
   │                             from SOURCE_GROUNDING; else     │
   │                             leave <FILL:> placeholders      │
   │     ┌── agent fills the <FILL:> markers in conversation ──┐ │
   │   factory.draft check    ── list remaining <FILL:>          │
   │   factory.draft promote  ── candidates/v000 → best/         │
   └────────────────────────────┬────────────────────────────────┘
                                ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ Phase 4–5  SkillOpt iterate loop  (agent-driven, no SDK)    │
   │   factory.iterate init  --max-iters 4 --target-score 95     │
   │                                                             │
   │     ┌────────────────────────────────────────────────┐      │
   │     │ next-step → which prompt to apply next         │ ◀─┐  │
   │     └──────────────────┬─────────────────────────────┘   │  │
   │                        ▼                                 │  │
   │     ┌────────────────────────────────────────────────┐   │  │
   │     │ AGENT AT KEYBOARD ── applies RUBRIC.md or one  │   │  │
   │     │   of 8 prompt_contracts/*; writes JSON         │   │  │
   │     └──────────────────┬─────────────────────────────┘   │  │
   │                        ▼                                 │  │
   │     ┌────────────────────────────────────────────────┐   │  │
   │     │ record-score      ── evidence-keyword check    │   │  │
   │     │ record-proposal   ── (analyst_* / merge_*)     │   │  │
   │     └──────────────────┬─────────────────────────────┘   │  │
   │                        ▼                                 │  │
   │     ┌────────────────────────────────────────────────┐   │  │
   │     │ apply  ── patcher: 4 ops + SLOW_UPDATE         │   │  │
   │     │          protection; strict > validation gate; │   │  │
   │     │          hash-cache; rejected-edit buffer      │   │  │
   │     │          → candidates/v00n                     │   │  │
   │     └──────────────────┬─────────────────────────────┘   │  │
   │                        ▼                                 │  │
   │              score ≥ target_score ?  ── NO ──────────────┘  │
   │                        │ YES                                │
   │                        ▼                                    │
   │     ┌────────────────────────────────────────────────┐      │
   │     │ finalize  ── promote best/ + auto-republish    │      │
   │     └──────────────────┬─────────────────────────────┘      │
   └────────────────────────┼────────────────────────────────────┘
                            │
        ┌───────────────────┴───────────────┐
        │  optional alt for kind=agent      │
        │  with routing fixture:            │
        │  factory.rollout build-eval +     │
        │  next-task + record-result        │
        │  (routing_match auto-judge)       │
        └───────────────────┬───────────────┘
                            ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ Phase 6  publish  (hard gates — fail closed)                │
   │   ✓ frontmatter schema  (publisher_adapter)                 │
   │   ✓ key_apis ≥ 40% word-boundary + no unauthorized          │
   │     `pkg.mod.func` calls in ```python``` fences             │
   │   ✓ routing_fixture promise (when kind=agent)               │
   │   ✓ no machine-specific paths in SKILL.md                   │
   │   ✗ refuses any out-dir whose path contains omicos-admin/   │
   └────────────────────────────┬────────────────────────────────┘
                                ▼
              ┌────────────────────────────────────┐
              │  <target>/published/               │  ← portable bundle
              │   ├── agents/<id>.md               │     (pure files,
              │   └── skills/<skill_id>/{…}        │      no install
              └────────────────┬───────────────────┘      hints)
                               │  operator's cp step ↓
       ┌─────────┬─────────────┼─────────────┬─────────────┐
       ▼         ▼             ▼             ▼             ▼
  ~/.claude/  .claude/    ~/.codex/    ~/.config/    $ADMIN/...
   (global)   (project)                opencode/    (private
                                                     OmicOS)
```

## How to invoke

This is an **agent-driven** loop, like omicverse-rebuildr. The Python pieces
here are CLI helpers + a state-machine cursor; the LLM at the keyboard
(Claude Code / Codex) IS the optimizer model. Nothing in this repo calls an
LLM SDK.

First-time setup (~30 seconds): see [SETUP.md](SETUP.md). The short version:

```bash
export OMICOS_PYTHON_ENV=omicdev         # optional; defaults to 'omicdev'
export OMICOS_R_ENV=CMAP                 # optional; only used for R-side capabilities
# OMICOS_ADMIN_ROOT is optional — set only if you're deploying into an
# OmicOS-admin checkout and want Phase 0 dedup + `route --members auto`.
pip install -e .
python -m factory.smoke_test             # expect [smoke] OK -- N/N checks passed
```

Then open this folder in Claude Code (or Codex) and say:

```
Generate skill for package lazyslide. Follow PROTOCOL.md.
```

The agent runs:

```bash
# Phase 0–1: package-wide partition (one factory tree, many capabilities)
python -m factory.partition init  ../<pkg-root>     # write partition.json stub
# (agent fills partition.json from tutorials)
python -m factory.partition emit   ../<pkg-root>    # scaffold one target subdir per capability
python -m factory.partition status ../<pkg-root>    # show resolved capabilities + remaining FILLs
python -m factory.partition update ../<pkg-root>    # re-sync manifest.extra when partition.json changes

# Phase 2: source grounding (per target)
python -m factory.source_grounding run ../<pkg-root>/<kind>/<id>

# Phase 3: scaffold v000 (auto-pre-fills Quick Workflow from SOURCE_GROUNDING.md
# when present; otherwise FILL placeholders).
python -m factory.draft init    ../<pkg-root>/<kind>/<id>
python -m factory.draft check   ../<pkg-root>/<kind>/<id>   # list remaining <FILL:> markers
python -m factory.draft promote ../<pkg-root>/<kind>/<id>   # latest candidate → best/

# Phase 4–5: SkillOpt iterate loop (state machine + hash cache)
python -m factory.iterate init        ../<pkg-root>/<kind>/<id> --max-iters 4 --target-score 95
# loop:
python -m factory.iterate next-step    ../<pkg-root>/<kind>/<id>
# (agent scores per RUBRIC.md, writes the JSON)
python -m factory.iterate record-score ../<pkg-root>/<kind>/<id> path/to/iterNN_rubric_score.json
# (agent emits patch proposals per the 8 prompt_contracts)
python -m factory.iterate record-proposal <prompt> path/to/<prompt>.json
python -m factory.iterate apply        ../<pkg-root>/<kind>/<id>   # → candidates/v00n
python -m factory.iterate finalize     ../<pkg-root>/<kind>/<id>   # promotes best/ + auto-republishes
# batch helpers:
python -m factory.iterate continue ../<pkg-root>/<kind>/<id>       # resume on best/, carry-forward cache
python -m factory.iterate batch    ../<pkg-root>                    # init-or-continue every target

# Phase 6: render the portable bundle:
omicos-portbuild publish ../<pkg-root>/<kind>/<id>
# → ../<pkg-root>/<kind>/<id>/published/
#     ├── agents/<id>.md
#     └── skills/<skill_id>/

# Optionally: synthesize a router from existing OmicOS-admin registry
omicos-portbuild route --domain spatial-he --members auto
```

> `omicos-portbuild build` is a convenience wrapper that calls
> `partition.init/emit` + `source_grounding.run` + `draft.init` in sequence
> for a single capability. The per-module CLIs above are the canonical
> interface the iterate loop uses; the `build` shortcut is fine for
> one-package experiments.

### Deploying the bundle

`published/` is the deliverable — pure `agents/<id>.md` +
`skills/<skill_id>/…` files. `cp` them wherever your runtime reads from.
A few examples (you decide which one your setup needs):

```bash
PUB=../<pkg-root>/<kind>/<id>/published

# Claude Code, user-global
cp $PUB/agents/<id>.md       ~/.claude/agents/
cp -r $PUB/skills/<skill_id> ~/.claude/skills/

# Claude Code, project-scoped (this repo only)
cp $PUB/agents/<id>.md       .claude/agents/
cp -r $PUB/skills/<skill_id> .claude/skills/

# Codex CLI
cp $PUB/agents/<id>.md       ~/.codex/agents/
cp -r $PUB/skills/<skill_id> ~/.codex/skills/

# OpenCode
cp $PUB/agents/<id>.md       ~/.config/opencode/agent/
cp -r $PUB/skills/<skill_id> ~/.config/opencode/skills/

# OmicOS (private admin checkout, if you happen to run one)
cp $PUB/agents/<id>.md       "$ADMIN/agents/"
cp -r $PUB/skills/<skill_id> "$ADMIN/skills/"
```

Same files, different destinations. portbuild has no opinion on which
runtime, and never writes outside `<target>/published/` itself.

## Design choices and where they came from

| Concept                                                   | Source                                                                     |
|-----------------------------------------------------------|----------------------------------------------------------------------------|
| 4 atomic patch ops (`append/insert_after/replace/delete`) | SkillOpt §C.3, verbatim                                                    |
| `<!-- SLOW_UPDATE_START -->` protected region             | SkillOpt §C.2 step-level prompt invariants, verbatim                       |
| `s_cur, s_best, C, B, m_meta` state machine               | SkillOpt Algorithm 1 (Appendix C.1), verbatim variable names               |
| Strict `>` validation gate, hash-cache, rejected-buffer   | SkillOpt §3.5 + Algorithm 1                                                |
| 8 prompt contracts                                        | SkillOpt §C.2.1–C.2.8 — JSON schemas verbatim, rules rewritten for biology |
| `support_count` + `source_type` + 3-stage merge order     | SkillOpt §C.2.3–C.2.5                                                      |
| Per-step `edit_apply_report.json`                         | SkillOpt §4.2 implementation note                                          |
| `_trajectory/timeline.jsonl` + ITERATION_LOG.md           | omicverse-rebuildr's per-iteration log policy                              |
| `EVOLUTION_PLOT.svg` two-panel                            | omicverse-rebuildr's evolution plot pattern                                |
| Per-target self-contained folder                          | omicverse-rebuildr's `py-<pkg>` layout                                     |
| `targets/<id>/best/` co-locating agent + skill            | This factory's choice                                                      |
| `targets/<id>/published/` as the export sandbox           | This factory's anti-admin-write policy                                     |
| 10-dim quality rubric (incl. OmicOS Routing Fitness)      | upstream awesome-skill-generate 9 dims + one OmicOS dim                    |
| `omicdev` / `CMAP` env defaults                           | OmicOS convention (see CLAUDE.md memory)                                   |

## Module map — which are "OmicOS-specific"?

| Module | Type | Notes |
|---|---|---|
| `factory/patcher.py` | engine | SkillOpt §C.3, no OmicOS coupling |
| `factory/algorithm1.py` | engine | SkillOpt Algorithm 1, no OmicOS coupling |
| `factory/iterate.py` | engine | state-machine cursor (`init/next-step/record-*/apply/finalize/continue/batch`), no LLM SDK; auto-republish on finalize |
| `factory/timeline.py` | engine | rebuildr-style event log |
| `factory/plot_evolution.py` | engine | stdlib SVG plot |
| `factory/prompt_contracts/*.md` | mixed | JSON schemas verbatim; rule lists have OmicOS-flavored constraints |
| `factory/smoke_test.py` | engine | framework is generic; the defaults are OmicOS |
| `factory/env.py` | engine | resolver is generic; defaults `omicdev`/`CMAP`/`omicos-admin` are OmicOS |
| `factory/manifest.py` | engine | dataclass + YAML; stamps `factory_version` |
| `factory/partition.py` | engine | `init/status/emit/update`; non-destructive merge in `update` |
| `factory/source_grounding.py` | engine | clones source, AST-inspects user-facing modules |
| `factory/draft.py` | **OmicOS** | scaffolds OmicOS-superset v000; auto-pre-fills Quick Workflow from SOURCE_GROUNDING |
| `factory/discovery.py` | **OmicOS** | reads admin layout (read-only) |
| `factory/route.py` | **OmicOS** | reads admin frontmatter |
| `factory/publisher.py` | engine + OmicOS rail | writes portable `published/{agents,skills}/` bundle; refuses any out-dir under `omicos-admin/` (OmicOS-specific safety rail); checks routing-fixture promise |
| `factory/publisher_adapter.py` | **OmicOS** | admin schema validator + `key_apis` word-boundary grounding + code-block unauthorized-API scan |
| `factory/scorer.py` | **OmicOS** | rubric dim 10 (Routing Fitness) + cross-modality lint + evidence-keyword check |
| `factory/validate.py` | **OmicOS** | admin schema + SLOW_UPDATE + machine-path lint |
| `factory/acceptance.py` | mixed | smoke harness is generic; honors `OMICOS_PYTHON_ENV` |
| `factory/cli.py` | **OmicOS** | `build / publish / route` wrapper subcommands |
| `factory/__init__.py` | engine | `__version__` (stamped into manifest + cursor) |
| `RUBRIC.md` | **OmicOS** | dim 10 + cross-modality block + evidence keywords per dim |
| `factory/templates/*.j2` | **OmicOS** | admin frontmatter schema |

If anyone ever needs to target a different host, swap the OmicOS-marked
modules; the engine row is reusable as-is.

## Quality gates the factory enforces

These are the contract checks that run automatically; they don't need the
agent to remember them. If any of them trip, the relevant step refuses to
advance — score/promote/publish all fail closed.

| Gate | Where | Behavior |
|---|---|---|
| `<!-- SLOW_UPDATE_START --> ... _END -->` block intact | patcher + validate | refuse to write |
| Evidence-keyword check (dim ≥ 4 needs matching keywords in the artifact body) | `scorer.cmd_record_score` | `EvidenceCheckFailed` if missing |
| Cross-modality fabrication hard block (RUBRIC §"Hard Block") | scorer | force-score 1, hard fail |
| `key_apis` grounding ≥ 40% word-boundary match + no unauthorized API in `​```python``` fences | `publisher_adapter.validate_skill_for_publish` | publish refuses |
| Promised `assets/routing_fixture.jsonl` actually exists + parses as JSONL with `{user_query|q, expected_skill_id}` | `publisher._check_routing_fixture_if_promised` | publish refuses |
| Admin frontmatter schema (id/name/title/description/category/category_order/tier/summary, kind-specific fields) | `publisher_adapter.adapt_skill / adapt_agent` | publish refuses |
| No machine-specific absolute paths in `SKILL.md` / `references/` | `validate.py` | publish refuses |
| Strict `>` validation gate (SkillOpt §3.5), hash-cache, rejected-edit buffer | `iterate.cmd_apply` | rolls back |
| `factory_version` round-trip check (manifest + cursor) | `manifest.write_initial_manifest` + `IterateCursor.load` | `RuntimeWarning` on version mismatch |
| `partition update` non-destructive merge (refuses key_apis removal + orphaned subdirs without `--force`) | `partition.cmd_update` | reports issues |

## Status

Engine + OmicOS modules all in place — **136 unit tests pass**
(`python3 -m unittest discover -s tests`):

- [x] `factory/patcher.py` — 4 ops + SLOW_UPDATE protection + independence
- [x] `factory/algorithm1.py` — state-machine dataclasses
- [x] `factory/iterate.py` — agent-driven SkillOpt loop CLI (`init / next-step /
  record-score / record-proposal / apply / finalize / continue / batch`);
  auto-republishes on finalize
- [x] `factory/timeline.py` — `_trajectory/timeline.jsonl` event log +
  per-step `ITERATION_LOG.md` renderer
- [x] `factory/plot_evolution.py` — stdlib SVG two-panel plot
- [x] `factory/env.py` — env-var resolver (`OMICOS_PYTHON_ENV`,
  `OMICOS_R_ENV`, `OMICOS_ADMIN_ROOT`, `OMICOS_AWESOME_SG`,
  `OMICOS_TARGETS_ROOT`)
- [x] `factory/smoke_test.py` — 6 pre-flight checks (warn-only on
  admin checks when `OMICOS_ADMIN_ROOT` is absent)
- [x] `factory/partition.py` — `init / status / emit / update`; partition.json
  drives kind-dispatched scaffolds; `update` is non-destructive
- [x] `factory/source_grounding.py` — clones source, AST-inspects user-facing
  modules, writes `SOURCE_GROUNDING.md` (consumed by `draft init`)
- [x] `factory/draft.py` — `init / check / promote`; `init` auto-pre-fills
  the Quick Workflow skeleton from `SOURCE_GROUNDING.md` when present
- [x] `factory/scorer.py` — `lint / score-request / record-score`; enforces
  evidence-keyword check before letting any high score land
- [x] `factory/publisher.py` + `publisher_adapter.py` — portable bundle,
  refuses `omicos-admin` paths, `key_apis` grounding, routing-fixture
  promise check
- [x] `factory/validate.py` — admin schema + SLOW_UPDATE + path lint
- [x] `factory/acceptance.py` — smoke harness honoring `OMICOS_PYTHON_ENV`
- [x] `factory/__init__.py` — `__version__ = "0.2.0"`, stamped into every
  manifest + iterate cursor; mismatch on load emits `RuntimeWarning`
- [x] `factory/rollout.py` — rollout-harness adapter
  (schema + `build-eval / next-task / record-result / summarize`). The
  **first real harness is router-fixture replay**:
  `build-eval --from-routing-fixture` turns an agent's
  `best/assets/routing_fixture.jsonl` into `eval/<split>.yaml`; the
  `routing_match` scoring kind auto-judges
  `predicted_skill_id == expected_skill_id` at `record-result` and
  `summarize` time. `iterate.py` (no-rollout, RUBRIC-driven) remains the
  default; the rollout loop is for routers + any future fixture-based
  capability. See [ROLLOUT_HARNESS.md](ROLLOUT_HARNESS.md).
- [x] Jinja2 templates, `RUBRIC.md` (10 dims), `PROTOCOL.md` (6 phases)

## Quick test

```bash
python3 -m unittest discover -s tests -v
```

## License

BSD-2-Clause, matching the omicos-dev convention.
