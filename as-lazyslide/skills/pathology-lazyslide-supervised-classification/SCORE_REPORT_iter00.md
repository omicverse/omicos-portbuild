# Score Report — `iter00`

- Verdict: **`pass`**
- Weighted score: **77.2 / 100** (threshold 75 to pass)


## Dimension scores

| Dim | Score | Weight | Reason |
|---|---:|---:|:---|
| trigger_precision 🚪 | 4 | 12 | per-skill honest scoring |
| execution_clarity 🚪 | 4 | 12 | per-skill honest scoring |
| validation_strength 🚪 | 3 | 12 | per-skill honest scoring |
| empirical_executability 🚪 | 3 | 15 | per-skill honest scoring |
| context_efficiency | 4 | 8 | per-skill honest scoring |
| reusability | 4 | 8 | per-skill honest scoring |
| resource_partitioning | 4 | 8 | per-skill honest scoring |
| compatibility_robustness | 4 | 8 | per-skill honest scoring |
| maintainability | 4 | 4 | per-skill honest scoring |
| omicos_routing_fitness 🚪 | 5 | 13 | per-skill honest scoring |

## Top issues

- small cohort classifiers need stratified k-fold CV; current Quick Workflow only mentions a single train_test_split
