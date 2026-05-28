# Rollout Harness — design + wiring contract

> Status: **first real harness wired** — router-fixture replay (auto-judged
> via `routing_match`). The schema + CLI are unit-tested against a real
> agent target; the remaining harnesses (skill smoke runner, LLM-judged
> execution) follow the same shape and are still pending.

The two factory loops today:

| Loop | Driver | When to use |
|---|---|---|
| `factory.iterate` | self-review against `RUBRIC.md` | no rollout harness yet — judge the artifact itself |
| `factory.optimizer` | SkillOpt with real rollouts (8 prompt contracts) | a harness can score eval tasks |

`iterate` is the working path for most capabilities right now —
RUBRIC-driven self-review converges quickly and needs no eval infra. But
when a capability has a fixture (e.g. router routing-accuracy, smoke
test on a tiny held-out dataset), the `optimizer` loop is stricter
because it scores against ground truth, not against rubric inference.

`factory/rollout.py` is the **adapter** between the two. It does not
execute rollouts — that's the harness's job — but it does specify the
schema, the on-disk layout, and the agent-facing CLI that the harness
plugs into.

---

## The protocol in five calls (router-fixture replay shown)

```bash
# 0. Build eval/<split>.yaml from the agent's promised routing fixture.
python -m factory.rollout build-eval <agent_target> \
    --from-routing-fixture --split train
# → reads <agent_target>/best/assets/routing_fixture.jsonl
# → writes <agent_target>/eval/train.yaml
#   with scoring.kind=routing_match on every task

# 1. Ask what task to roll out next.
python -m factory.rollout next-task <agent_target> \
    --split train --epoch 0 --step 0 --candidate v000
# → prints the EvalTask + the JSON path to write into

# 2. Do the work (read candidate's agent.md, pick predicted_skill_id),
#    write {task_id, candidate, outputs.predicted_skill_id} to the path
#    next-task named. pass/score may be omitted — the auto-judge fills
#    them at record-result time when --split is set.

# 3. Validate + auto-judge one result.
python -m factory.rollout record-result <agent_target> path/to/<task_id>.json \
    --split train
# → if pass/score are unset and scoring.kind has a registered judge
#   (routing_match etc.), they are computed and persisted back to disk.

# 4. After the whole batch is done, aggregate into the JSONL shape
#    `factory.optimizer record-rollouts` consumes. Summarize re-runs the
#    auto-judge for any rows still missing pass/score.
python -m factory.rollout summarize <agent_target> \
    --split train --epoch 0 --step 0 --candidate v000
# → emits _trajectory/rollouts/e00/s00/v000.rollouts.jsonl

python -m factory.optimizer record-rollouts <agent_target> \
    _trajectory/rollouts/e00/s00/v000.rollouts.jsonl
```

That last call is what wires the harness back into the optimizer state
machine; from there the optimizer drives the 8 prompt contracts as
before.

The auto-judge is opt-in per scoring kind. The harness can always submit
pre-judged pass/score and the judge will respect that; the auto-judge
only fills in *missing* fields, so a harness that wants to override the
routing-match heuristic can do so by passing `pass`/`score` explicitly.

---

## On-disk layout

```
<target>/
├── eval/
│   ├── train.yaml         ← list of {task_id, inputs, expected_outputs, scoring}
│   ├── selection.yaml     ← held-out validation
│   └── test.yaml          ← held-out test (touched once, at finalize)
└── _trajectory/
    └── rollouts/
        └── e00/
            └── s00/
                ├── v000/
                │   ├── <task_id_1>.json     ← RolloutResult per task
                │   ├── <task_id_2>.json
                │   └── …
                └── v000.rollouts.jsonl      ← aggregated, optimizer-shaped
```

`v000.rollouts.jsonl` is what `optimizer.record-rollouts` reads.

---

## Schemas

`EvalTask` (one entry in `eval/<split>.yaml`):

```yaml
tasks:
  - task_id: route_anndata_sample_001
    inputs:
      user_query: "Run Moran's I on visium_sample.h5ad"
    expected_outputs:
      expected_skill_id: squidpy_spatial_autocorrelation
    scoring:
      kind: exact_match
      key: expected_skill_id
```

`RolloutResult` (one JSON the harness writes per task):

```json
{
  "task_id": "route_anndata_sample_001",
  "candidate": "v000",
  "pass": true,
  "score": 1.0,
  "outputs": {
    "predicted_skill_id": "squidpy_spatial_autocorrelation"
  },
  "evidence": [
    "candidates/v000/skill/SKILL.md#trigger-line-12"
  ],
  "error": null
}
```

`pass` and `score` are independent. A harness can fill only `pass` (when
the scoring is binary) or only `score` (when it's continuous);
`summarize` aggregates whichever is present.

---

## Three concrete harnesses

| Harness | Status | What it does |
|---|---|---|
| **router fixture replay** | ✓ **wired** | for routers: `build-eval --from-routing-fixture` turns `best/assets/routing_fixture.jsonl` into `eval/<split>.yaml`; `scoring.kind=routing_match` auto-judges `predicted_skill_id == expected_skill_id` |
| **skill smoke runner** | pending | invoke the skill's `acceptance.json.smoke_commands` in the `omicdev` conda env, judge by `required_paths`/`required_body_terms` (reuses `factory.acceptance`) |
| **LLM-judged execution** | pending | replay a tiny held-out batch through the candidate skill in conversation, judge by rubric or by structural output check |

The harness's only API obligation is: **emit one `RolloutResult` per
task into the path `next-task` named**, then call `summarize` once. The
rest of the optimizer loop stays unchanged.

---

## What's still pending

- **`skipped_infeasible_local` semantics** — `next-task` doesn't yet have
  a way for the harness to mark a task as "I can't run this on this
  machine" so it gets retried later. Lands when a harness needs it.
- **Aggregation is naive** — mean of scores, count of pass, no
  weighting, no per-split percentile. Adequate for routing-accuracy;
  revisit when a continuous-score harness lands.
- **Skill smoke runner + LLM-judged execution** harnesses (see table
  above) follow the same shape as router-fixture replay but aren't
  wired yet. Each will add one `kind:` discriminator + one
  `_judge_<kind>` and reuse the rest of the protocol.

## How a new harness plugs in

1. Pick a `scoring.kind` name (e.g. `smoke_passes`, `rubric_dim_avg`).
2. Add `_judge_<kind>(task, outputs) -> {pass, score, judge_notes}` and
   register it in `_JUDGES`.
3. Add a `build-eval --from-<source>` flag that emits
   `eval/<split>.yaml` with `scoring.kind=<kind>` per task.
4. Done. `next-task`, `record-result --split`, and `summarize` carry the
   new kind without further changes; tests follow the pattern in
   `tests/test_rollout_router_harness.py`.
