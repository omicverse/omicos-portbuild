# Phase 3 — Draft the agent + skill PAIR

The factory has scaffolded `candidates/v000/`. Two files need to be filled in
before the SkillOpt loop starts. Both contain `<FILL: …>` markers; replace
each marker with the indicated content. The publisher refuses to ship any
artifact that still contains a marker.

## Files to fill in

1. `candidates/v000/agent.md` — the OmicOS runtime persona
2. `candidates/v000/skill/SKILL.md` — the execution policy

## How to fill in

Read these inputs in order:

- `manifest.yaml` — capability_id, package, cost_profile
- `PARTITION.md` — which capability this is (vs. the package's other capabilities)
- `SOURCE_GROUNDING.md` — live API signatures, branch parameters, model registry

Then rewrite each `<FILL: …>` marker into final content. The marker's text
explains what to write (e.g. `<FILL: 5-12 word title naming the capability>`).
Do NOT leave any marker behind.

## How to check

```bash
python -m factory.draft check targets/pathology-lazyslide-survival-prediction
```

That command lists every unresolved `<FILL:>` per file. Once it returns OK,
proceed to:

```bash
python -m factory.optimizer init targets/pathology-lazyslide-survival-prediction
```

## What you're producing

A *pair* of OmicOS artifacts:

- The **agent** owns routing and persona — what the user asks, when to ask
  clarification, when to delegate to siblings, what report contract is
  guaranteed.
- The **skill** owns execution — the live API calls, the validation
  postconditions, the SLOW_UPDATE region the optimizer will train against.

Both ship together, always. The `published/` bundle puts the agent in
`agents/<id>.md` and the skill in `skills/<skill_id>/`, matching the
omicos-admin two-tier layout.
