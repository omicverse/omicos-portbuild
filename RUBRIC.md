# OmicOS Skill Quality Rubric (10 dimensions)

A skill or agent is scored on 10 dimensions, each on a 1–5 scale. The weighted
total is normalized to 100. Hard gates must clear `3/5` for a `pass` verdict.

This rubric is **not** awesome-skill-generate's rubric. It is informed by it
(the first 9 dimensions track upstream's 9 axes with adjusted weights) but
adds OmicOS-specific concerns:

- biology / multimodal workflows: empirical executability matters more,
  fabricated cross-modality claims must be hard-blocked
- OmicOS runtime: skills are routed by an upstream agent, so routing fitness
  is its own axis
- admin schema: skills and agents are deployed as a tied pair, so the rubric
  must score both halves

## Scale

- `1`: seriously inadequate
- `2`: partially present but unreliable
- `3`: usable with notable gaps
- `4`: strong and reliable
- `5`: excellent and low-ambiguity

## Dimensions

| # | Dimension                       | Weight | Hard gate (>=3) |
|---|---------------------------------|-------:|:---------------:|
| 1 | Trigger Precision               |     12 |        ✓        |
| 2 | Execution Clarity               |     12 |        ✓        |
| 3 | Validation Strength             |     12 |        ✓        |
| 4 | Empirical Executability         |     15 |        ✓        |
| 5 | Context Efficiency              |      8 |                 |
| 6 | Reusability                     |      8 |                 |
| 7 | Resource Partitioning           |      8 |                 |
| 8 | Compatibility Robustness        |      8 |                 |
| 9 | Maintainability                 |      4 |                 |
|10 | OmicOS Routing Fitness          |     13 |        ✓        |

Total weight = 100. (Deltas from upstream's awesome-skill-generate rubric: we
lowered Trigger/Exec/Validation from 15 → 12 to make room for Empirical
Executability 10 → 15 and the new Routing Fitness axis at 13. Multimodal
workflows reward reviewer-side execution more than text quality.)

### 1. Trigger Precision (12, hard gate)

Will the agent or skill trigger for the right requests and avoid false
positives?

- ✅ task name, concrete artifacts, user-language synonyms
- ✅ for routers: clarification questions are present and disambiguate
- ❌ vague descriptions ("help with data"), missing trigger situations,
  domain names without task names

### 2. Execution Clarity (12, hard gate)

Can another agent follow the workflow and act without guessing major steps?

- ✅ ordered workflow, minimal execution spine, explicit prerequisites
- ✅ source-grounded interface details when the skill names concrete
  functions, methods, or CLI flags (verified via `interface_inspector.py`)
- ✅ coverage of branch-like parameters (`method`, `backend`, `model`,
  `recipe`, `mode`, `provider`) when they control materially different
  behavior
- ❌ tutorial prose without procedure, parameter descriptions inferred
  from notebook usage without checking live signatures

### 3. Validation Strength (12, hard gate)

Does the skill explain how to tell whether the output is correct?

- ✅ expected keys, files, outputs, postconditions
- ✅ smoke tests / verification steps embedded
- ✅ evidence that interface claims were checked against source / help / -h
- ❌ "works if no error" as the only check, validation hidden in an
  unrelated file, no test for documented branches

### 4. Empirical Executability (15, hard gate)

Can the reviewer use representative data to follow the skill and verify that
a documented core path actually works? This is weighted highest because
multimodal / WSI workflows fail silently far more often than text workflows.

- `1/5`: no empirical execution attempt at all
- `2/5`: import or interface smoke only
- `3/5`: reviewer ran one synthetic or minimal data execution path
- `4/5`: reviewer ran one realistic or notebook-adjacent data path with output
  checks
- `5/5`: reviewer ran multiple meaningful paths or strong fixture coverage
  with output checks

### 5. Context Efficiency (8)

Does the skill minimize context usage while remaining complete?

- ✅ short SKILL.md, details delegated to `references/`, no duplication
- ❌ bloated SKILL.md, copied tutorial text, repeated examples

### 6. Reusability (8)

Is the skill built around a stable job rather than a one-off artifact?

- ✅ reusable task framing, abstraction from source material
- ❌ skill is mostly a transcript of one tutorial, no abstraction

### 7. Resource Partitioning (8)

Are `SKILL.md`, `references/`, `scripts/`, and `assets/` used appropriately?

- ✅ core workflow in `SKILL.md`, long details in `references/`, repeated
  deterministic logic in `scripts/`, templates in `assets/`
- ❌ everything in one file, references required but never mentioned

### 8. Compatibility Robustness (8)

Does the skill identify version drift, dependency constraints, environment
assumptions (omicdev / CMAP / huggingface gating) when they matter?

- ✅ renamed APIs, removed parameters, extra deps, env assumptions called out
- ❌ silent dependency traps, stale tutorial prose as current behavior

### 9. Maintainability (4)

Can the skill be updated without large rewrites when the source changes?

- ✅ good decomposition, source-to-skill mapping in `references/`
- ❌ tightly coupled prose, no indication where content came from

### 10. OmicOS Routing Fitness (13, hard gate)

Does the agent / skill fit cleanly into the OmicOS runtime?

- ✅ admin-schema frontmatter is complete (id, name, title, category, tier,
  summary, use_when, example_prompts, example_inputs, toolsets, skills)
- ✅ produces the OmicOS report contract (`REPORT.html`, `summary.json`,
  `handoff_manifest.json`) where applicable
- ✅ routes molecular claims that cannot be supported by the agent's own
  evidence (e.g. molecular labels from H&E pixels alone) to the appropriate
  sibling agent rather than fabricating them
- ✅ for routers: dispatch table maps every observed input class to exactly
  one downstream agent with no overlap
- ❌ uses local interpreter paths or machine-specific cache locations
- ❌ skill exists in OmicOS twice (factory missed Phase 0 discovery)

## Hard Block: cross-modality fabrication

For any image-modality skill, the rubric applies an automatic `fail` (not
just lowered score) if the skill instructs the agent to produce molecular,
transcriptomic, or pathway claims from pixel evidence WITHOUT routing to a
predictor designed for that crossing (e.g. `he_to_st_predictor` for
H&E → spatial transcriptomics).

This rule is enforced both in the scorer prompt and as a regex / lint check
in `factory/validate.py`.

## Weighted Score

```
score = sum(dim_score_i / 5 * weight_i)   # max 100
```

## Verdict Rules

### `pass`

- All hard-gate dimensions (1, 2, 3, 4, 10) at least `3/5`.
- Cross-modality fabrication block did not trip.
- Weighted score `>= 75`.

### `revise`

- Hard gates pass but weighted score is below `75`, or
- Structure is strong but reviewer-run data-backed execution evidence is too
  weak for a reliable `pass` on a data workflow.

### `fail`

- Any hard-gate dimension is below `3`, or
- Cross-modality fabrication block tripped, or
- The skill is too ambiguous to use reliably.
