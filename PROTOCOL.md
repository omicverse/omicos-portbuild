# omicos-portbuild Protocol — 6 Phases

> Generate a validated OmicOS agent + skill from one third-party package's
> tutorials + source. SkillOpt-style training loop, rebuildr-style per-target
> folder. **OmicOS-targeted by design.**

> **Hard policy**: this kit NEVER writes into `omicos-admin/`. Phase 6
> (publish) produces an admin-shape bundle at `targets/<id>/published/`;
> the operator copies it into admin by hand. The factory does read admin
> for duplicate detection and router synthesis, but only as read-only data.

## The loop in one sentence

Treat skill authoring as: **factory ingests tutorials + source → drafts agent
+ skill bundle → frozen target agent runs rollouts → optimizer reflects on
failures and successes → bounded edits applied → held-out gate accepts or
rejects → publish best to admin**.

Formally, mirroring SkillOpt §3.1: given the target agent `M`, harness `h`,
and capability splits `D_train, D_sel, D_test`, the factory produces
`(agent, skill)` such that

```
agent + skill = argmax_{candidates from rollouts on D_train}
                  selection_score on D_sel
```

then reports the held-out score on `D_test` and publishes the best pair.

---

## Phase 0 — Discovery (avoid duplicate work)

Before any draft. Check whether the capability already exists in OmicOS or
whether the algorithm is already covered by OmicVerse.

```bash
omicos-portbuild build --check <capability_id> --admin-root ../omicos-admin
```

Outputs `targets/<id>/DISCOVERY.md` recording:

- Does `omicos-admin/agents/` or `omicos-admin/skills/` already contain a
  matching capability? If yes → STOP, update the existing one instead.
- Does OmicVerse expose this algorithm? If yes → emit a routing skill
  that delegates to `omicverse_omni` instead of generating a redundant
  wrapper.
- License / weight-gating notes (HuggingFace gated checkpoints, GPL-3.0
  vendored code).

---

## Phase 1 — Capability Partition

Tutorials are split into stable, independently triggerable jobs. The factory
fights the urge to roll everything into one "do the whole notebook" skill.

For LazySlide, the partition is:

- direct H&E WSI analysis + report (capability_id: `pathology_lazyslide_he_analysis`)
- H&E ↔ transcriptomics integration when paired RNA exists
- zero-shot image-text scoring
- cell segmentation
- survival / slide-level prediction
- virtual staining / image generation

Each partition becomes its own `<pkg-root>/<kind>/<id>/`. **One factory
run = one capability.** Outputs: `<pkg-root>/partition.json` (the source
of truth) and per-target `manifest.yaml`.

If the partition changes after `emit` (a new capability, a new key API),
re-sync the affected manifests **non-destructively** with:

```bash
python -m factory.partition update <pkg-root>           # default: warn on removals
python -m factory.partition update <pkg-root> --force   # allow key_apis removals + orphaned subdirs
```

`update` refuses by default to drop a `key_apis` entry that already exists
in a manifest, or to leave orphaned subdirs after a capability disappears
from `partition.json`. This preserves agent-edited skill bodies across
partition iterations.

---

## Phase 2 — Source Grounding

Concrete API and CLI claims are grounded against live source before they
enter the draft. The factory:

1. `git clone` the package source into `targets/<id>/upstream-src/`
2. `webfetch` tutorial pages into `targets/<id>/upstream-docs/`
3. Run `factory/interface_inspector.py` on each user-facing module
   to extract signatures, defaults, docstring parameter docs, and detected
   branch-parameter values (via AST scan)
4. Enumerate model registries (HuggingFace ids, default backbones)
5. Record everything in `targets/<id>/SOURCE_GROUNDING.md`

The draft phase reads `SOURCE_GROUNDING.md`, not the docs directly.

---

## Phase 3 — Draft Generation (v000)

Factory invokes a subagent with:

- `SOURCE_GROUNDING.md` (facts about the live interface)
- `PARTITION.md` (which capability we're drafting)
- `factory/templates/skill_v000.md.j2` and `agent.admin.md.j2`
- The capability's `manifest.yaml`

The subagent emits `targets/<id>/candidates/v000/`:

```
v000/
├── agent.md                   ← admin-shape frontmatter
└── skill/
    ├── SKILL.md               ← includes empty <!-- SLOW_UPDATE_START --> block
    ├── references/
    │   ├── source-grounding.md
    │   └── source-tutorial-map.md
    └── assets/
        └── acceptance.json    ← required_paths includes REPORT.html, summary.json, handoff_manifest.json
```

`SKILL.md` MUST embed an empty `<!-- SLOW_UPDATE_START --> ... <!-- SLOW_UPDATE_END -->`
block from v000 on, or the SkillOpt loop has nowhere to write the
end-of-epoch longitudinal guidance.

---

## Phase 4 — Eval Harness

Build `eval/` from the partition and source grounding:

```
eval/
├── train.yaml        ← rollouts feed reflection
├── selection.yaml    ← validation gate (Algorithm 1 line 17)
└── test.yaml         ← final held-out score only (line 37)
```

Default split is 2:1:7 per SkillOpt §C; benchmark-specific splits are
allowed in `manifest.yaml`. Each task entry contains:

- input artifacts (slide path, expected modality, optional reference)
- expected outputs (file names, schema, value-range constraints)
- scoring function (rubric for routers; output-validity for terminal skills)

---

## Phase 5 — SkillOpt Loop (agent-driven)

This is the key design choice that mirrors omicverse-rebuildr's
`engine/loop.py`: **the loop is not driven by a Python SDK call to an LLM**.
The Python state machine lives in `factory/iterate.py` and on disk in
`targets/<id>/_state_iterate.json`; the agent (you, in Claude Code or Codex)
is the optimizer model. The agent walks the loop by alternating two things:

  1. `python -m factory.iterate next-step targets/<id>` — the cursor
     prints a JSON instruction block: which prompt contract to read, which
     trajectory/edit files to read, where to write the output, and which
     `record-*` subcommand to call next.
  2. The agent does the work (applies the prompt, writes the JSON), then
     calls the named `record-*` subcommand to advance the cursor.

Concretely, one SkillOpt step looks like this in conversation:

```
$ python -m factory.iterate init targets/<id> --max-iters 4 --target-score 95
{ "phase": "awaiting_initial_score", ... }

# (agent applies RUBRIC.md, writes the JSON)
$ python -m factory.iterate record-score targets/<id> _trajectory/score/iter00_rubric_score.json
{ "phase": "awaiting_analyst_error", "instructions": { ... } }

# (agent applies analyst_error.md to the failure minibatch, writes the JSON)
$ python -m factory.iterate record-proposal analyst_error _pending/iter01/analyst_error.json
{ "phase": "awaiting_analyst_success", "instructions": { ... } }

# ... merge_failure, merge_success, merge_final, ranking ...
$ python -m factory.iterate apply targets/<id>
{ "phase": "applied_awaiting_eval",
  "n_applied": 3, "n_skipped": 1,
  "candidate_dir": "candidates/v004",
  "instructions": { "do": "Apply RUBRIC.md to v004, then record-score", ... } }

# When score ≥ target_score, the cursor flips to ready_to_finalize:
$ python -m factory.iterate finalize targets/<id>
{ "phase": "finalized",
  "promoted": "candidates/v004",
  "republished": true,
  "published_dir": "targets/<id>/published" }
```

Two batch entry points exist alongside the per-step loop:

- `python -m factory.iterate continue targets/<id>` — re-init on the
  current `best/` and carry forward the selection-cache hashes; use when
  you've manually edited `best/` or finished a previous run and want
  another epoch.
- `python -m factory.iterate batch <pkg-root>` — runs `init-or-continue`
  across every `kind/<id>/` under `<pkg-root>`. Skips empty roots.

The Python side does ZERO model calls. It owns:

- `IterateCursor` (s_cur, s_best, C, B, m_meta, `factory_version`) — Algorithm 1 line 1
- the strict `>` validation gate — Algorithm 1 lines 20-24
- hash-based selection cache — Algorithm 1 lines 14-18
- the rejected-edit buffer (B) — Algorithm 1 line 26
- patcher safety (4 ops, SLOW_UPDATE protection, independence) — §C.3
- the evidence-keyword check (any dim ≥ 4 must show its keywords in
  SKILL.md / acceptance.json / agent.md, or `EvidenceCheckFailed`)
- which prompt is allowed in which phase
- `factory_version` round-trip — `RuntimeWarning` if a state file was
  written by a different factory version

The LLM (the agent at the keyboard) owns:

- producing rollouts
- applying the 8 prompt contracts in `factory/prompt_contracts/`
- producing JSON responses in the exact schemas required

Defaults (from SkillOpt §C.3):

| Knob                                            | Default                        |
|-------------------------------------------------|--------------------------------|
| epochs (E)                                      | 4                              |
| rollout batch size (B)                          | 40                             |
| accumulation factor (A)                         | 1                              |
| reflection minibatch (B_m)                      | 8                              |
| edit budget (L_t)                               | 4                              |
| LR schedule                                     | constant (§4 Table 2e best)    |
| LR floor                                        | 2                              |
| slow update                                     | enabled, 20 samples / epoch    |
| meta skill                                      | enabled (optimizer-side only)  |
| validation gate                                 | strict `>`, ties rejected      |
| edit mode                                       | patch                          |
| analyst workers in parallel                     | 16                             |
| merge batch size                                | 8                              |
| teacher refinement rounds per minibatch         | 3                              |

Per-step outputs:

- `candidates/v00n/` (new skill version)
- `candidates/v00n/edit_apply_report.json` (per-edit accept/skip)

Per-epoch outputs:

- `slow_update_v00n.json` (if e ≥ 2)
- `meta_skill_v00n.json` (optimizer-side, NOT published)

After all epochs:

- `best/` is copied from the candidates entry with the highest selection
  score (Algorithm 1 line 37 runs Test on s_best).
- `ITERATION_LOG.md` is materialized from `algorithm1.TrainingRun.to_dict()`.

---

## Phase 6 — Publish (sandbox export, never writes to admin)

```bash
omicos-portbuild publish targets/<id>            # default --out=<target>/published
omicos-portbuild publish targets/<id> --out /some/other/sandbox
```

`publisher.py`:

1. Reads `targets/<id>/best/{agent.md, skill/}`.
2. Passes through `publisher_adapter.py` to:
   - validate the admin-schema frontmatter (`adapt_skill` / `adapt_agent`)
   - check the SLOW_UPDATE block is intact, no machine-specific paths
   - check `key_apis` grounding: ≥40% of declared `name` basenames appear
     as word-boundary tokens in the artifact body, AND no
     ```python``` fence calls an unauthorized `pkg.module.func` (modulo
     the generic-helper allow-list)
3. If `agent.md` advertises a routing fixture
   (`assets/routing_fixture.jsonl`), the file MUST exist, parse as JSONL,
   and every row MUST contain `{user_query | q, expected_skill_id}` —
   enforced by `publisher._check_routing_fixture_if_promised`. Empty
   promised fixtures are rejected.
4. Writes the **admin-shape bundle into a sandbox**, never into admin:
   - `targets/<id>/published/agents/<id>.md`
   - `targets/<id>/published/skills/<skill_id>/`
   - `targets/<id>/published/HOW_TO_INSTALL.md` (a ready-to-paste `cp`
     command for the operator)
5. Refuses any `--out` whose resolved path contains `omicos-admin` —
   enforced as a hard error in `publisher._looks_like_admin_path`.

`iterate finalize` calls `publisher.run` automatically when a target's
selection score meets `--target-score`; the `published/` sandbox is
refreshed in place. Idempotent `cp` from there into the real admin
checkout is the only step the factory will not do for you.

The operator then runs the printed `cp` command to drop the bundle into
the real admin checkout. This is the one step the factory will not do for
you, by policy.

### Router synthesis (independent of publish)

```bash
omicos-portbuild route --domain spatial-he --members auto
```

This is a separate explicit command (no auto-trigger). It reads
`OMICOS_ADMIN_ROOT/agents/*.md` to find sibling agents in the named
domain, drafts a router, runs the SkillOpt loop on routing-accuracy
tasks, and writes its own `targets/_router_<domain>/published/` bundle.
Same hard policy: it never touches admin.

---

## Router-synthesis variant (`omicos-portbuild route`)

Routers are first-class targets, just generated from a different input:

- Input: not tutorials, but the admin registry of sibling agents in the
  domain.
- Draft prompt variant: `factory/draft.py` reads `manifest.yaml.kind` and
  switches templates accordingly.
- Eval data: routing-accuracy tasks
  `(user_query, expected_member_id)` instead of execution traces.
- Scorer: routing fitness (Dimension 10) weighted higher.

Otherwise the same 6 phases.

---

## Hard invariants (factory will refuse to ship if violated)

1. `targets/<id>/best/skill/SKILL.md` must contain a well-formed
   `<!-- SLOW_UPDATE_START --> ... <!-- SLOW_UPDATE_END -->` block.
2. `targets/<id>/best/agent.md` frontmatter must satisfy the admin schema
   (checked by `validate.py`).
3. Cross-modality fabrication block (RUBRIC.md §"Hard Block") must not trip.
4. All hard-gate dimensions (1, 2, 3, 4, 10) score `≥ 3/5`.
5. `assets/acceptance.json.validation_budget` is present and consistent with
   the cost profile in `manifest.yaml`.
6. No machine-specific absolute paths (`/Users/...`, `/home/<user>/...`,
   `/scratch/...`) appear inside `SKILL.md` or `references/` (lint by
   `validate.py`).
7. **No write outside `targets/<id>/`.** `publisher.py` enforces this; any
   `--out` whose path contains `omicos-admin` is rejected at runtime.
8. **Evidence-keyword check.** No rubric dim may land a score ≥ 4 unless
   the artifact body (SKILL.md ++ acceptance.json ++ agent.md) contains
   the dim's evidence keywords (`scorer.RUBRIC_EVIDENCE_KEYWORDS`).
   Raised as `EvidenceCheckFailed` by `scorer.cmd_record_score` (bypass
   with `enforce_evidence=False`, intended for tests only).
9. **`key_apis` grounding ≥ 40%** with no unauthorized
   ``` ```python ``` ``` calls — `publisher_adapter.validate_skill_for_publish`.
10. **Promised routing fixture exists + parses + non-empty** —
    `publisher._check_routing_fixture_if_promised`.
11. **`factory_version` stamping.** `factory/__init__.__version__` is
    written into every manifest and `IterateCursor`; `IterateCursor.load`
    emits `RuntimeWarning` on version mismatch so stale state files
    surface loudly.
