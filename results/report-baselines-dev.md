# Results - baselines-dev

Eligible tasks in this split: **3** | excluded during preparation: **0** (of 3 candidates)

| Variant | Success | 95% CI | Regressions | Median cost | $/success | Median time | p90 time | Median steps |
|---|---|---|---|---|---|---|---|---|
| bump-pydantic | 0/2 (0%) | 0%-66% | 0 attempts (0 tests) | $0.000 | - | 42s | 58s | 0 |
| noop | 0/2 (0%) | 0%-66% | 0 attempts (0 tests) | $0.000 | - | 0s | 0s | 0 |

*Cost = model API spend only (failed attempts included); compute is not included. Verify `configs/pricing.json` against current published prices before quoting dollars.*

## Failure categories

| Variant | environment | incorrect_patch | regression | budget_exhausted | policy_violation | gave_up |
|---|---|---|---|---|---|---|
| bump-pydantic | 0 | 2 | 0 | 0 | 0 | 0 |
| noop | 0 | 0 | 0 | 0 | 0 | 2 |

## Paired comparisons (per task)

| A | B | tasks | A only | B only | both | neither | exact p |
|---|---|---|---|---|---|---|---|
| bump-pydantic | noop | 2 | 0 | 0 | 0 | 2 | 1.000 |

*With few tasks, most differences are not statistically significant; report the counts, not just the rates.*

