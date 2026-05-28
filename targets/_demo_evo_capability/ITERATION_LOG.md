# Iteration Log — `evo_capability`

- Total epochs: 4
- Events recorded: 15
- Latest score_cur: 0.6200
- Latest score_best: 0.6200
- Accepts: 2     Rejects: 1
- Loop finalized at: 2026-05-28T10:01:03Z

> This log is rebuilt after every state transition. Rejected attempts are kept.

## Epoch 1

| step | n_fail | n_succ | n_propose | n_apply | score_cand | decision | candidate |
|---:|---:|---:|---:|---:|---:|:---|:---|
| 0 | 3 | 2 | 2 | 2 | 0.6200 | accept_and_promote_best | `v001` |
| 1 | – | – | – | – | 0.5800 | reject | `–` |

- slow_update applied → candidate `v002` (decision: –)

## Rejected attempts (for the record)

| epoch | step | candidate | score_cand | score_cur_at_decision | score_drop |
|---:|---:|:---|---:|---:|---:|
| 1 | 1 | `v002` | 0.5800 | 0.6200 | 0.0400 |
