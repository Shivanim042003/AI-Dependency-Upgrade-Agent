# Results - cmp8

Eligible tasks in these runs: **1** | excluded during preparation: **0** (of 4 candidates)

## Track: strict_native (1 task: llm-pydantic-v2)

*No v1 shims, no exceptions; deprecation warnings are errors. The headline track.*

| Variant | Verified | 95% CI | Regressions | Collection failed | Median cost | Median tokens | $/success | Median time | Median steps |
|---|---|---|---|---|---|---|---|---|---|
| agent | 2/3 (67%) | 21%-94% | 1 attempts (43 tests) | 0/3 | $0.021 | 36,115 | $0.032 | 240s | 40 |
| agent+docs | 2/3 (67%) | 21%-94% | 1 attempts (43 tests) | 0/3 | $0.019 | 32,842 | $0.031 | 228s | 40 |

| Variant | environment | incorrect_patch | regression | budget_exhausted | policy_violation | gave_up |
|---|---|---|---|---|---|---|
| agent | 0 | 0 | 0 | 1 | 0 | 0 |
| agent+docs | 0 | 0 | 0 | 1 | 0 | 0 |

Paired per-task comparison:

| A | B | tasks | A only | B only | both | neither | exact p |
|---|---|---|---|---|---|---|---|
| agent | agent+docs | 1 | 0 | 0 | 1 | 0 | 1.000 |

Infrastructure errors excluded from the rates above (not agent results): agent: 3, agent+docs: 3

*Cost = model API spend only (failed attempts included); compute excluded. Free/local models cost $0: use tokens. `configs/pricing.json` is a placeholder until verified. "Collection failed" means pytest aborted before running tests, so required tests were never executed (not observed assertion failures). With few tasks, report counts and traces, not just rates.*


## Warnings
- 6 infrastructure-error runs (category `environment`) were excluded from all rates

