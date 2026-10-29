# Results

Eligible tasks in these runs: **2** | excluded during preparation: **0** (of 3 candidates)

> **No real strict-native task yet**, so there is no headline track. Do not quote any number from this report as a benchmark result.

> **Provisional tasks (adaptations/exceptions not yet human-reviewed): openjd-model, vonage-sdk.** Treat results as development evidence, not benchmark claims.

## Track: compat_exception (1 task: openjd-model)

*Strict native rules plus task-declared, machine-readable exceptions (same for every party). Reported separately.*

| Variant | Verified | 95% CI | Regressions | Collection failed | Median cost | Median tokens | $/success | Median time | Median steps |
|---|---|---|---|---|---|---|---|---|---|
| bump-pydantic | 0/3 (0%) | 0%-56% | 0 attempts (0 tests) | 2/3 | $0.000 | 0 | - | 73s | 0 |
| noop | 0/3 (0%) | 0%-56% | 0 attempts (0 tests) | 0/3 | $0.000 | 0 | - | 0s | 0 |
| agent | 0/1 (0%) | 0%-79% | 0 attempts (0 tests) | 1/1 | $0.000 | 560,268 | - | 341s | 45 |

| Variant | environment | incorrect_patch | regression | budget_exhausted | policy_violation | gave_up |
|---|---|---|---|---|---|---|
| bump-pydantic | 0 | 3 | 0 | 0 | 0 | 0 |
| noop | 0 | 0 | 0 | 0 | 0 | 3 |
| agent | 0 | 1 | 0 | 0 | 0 | 0 |

Paired per-task comparison:

| A | B | tasks | A only | B only | both | neither | exact p |
|---|---|---|---|---|---|---|---|
| bump-pydantic | noop | 1 | 0 | 0 | 0 | 1 | 1.000 |
| agent | bump-pydantic | 1 | 0 | 0 | 0 | 1 | 1.000 |

## Track: lenient (1 task: vonage-sdk)

*Deprecated-but-working v2 APIs allowed (no `pydantic.v1` shim). A different, easier objective; never pool with strict.*

| Variant | Verified | 95% CI | Regressions | Collection failed | Median cost | Median tokens | $/success | Median time | Median steps |
|---|---|---|---|---|---|---|---|---|---|
| bump-pydantic | 0/3 (0%) | 0%-56% | 0 attempts (0 tests) | 2/3 | $0.000 | 0 | - | 22s | 0 |
| noop | 0/3 (0%) | 0%-56% | 0 attempts (0 tests) | 0/3 | $0.000 | 0 | - | 0s | 0 |
| agent | 0/1 (0%) | 0%-79% | 0 attempts (0 tests) | 1/1 | $0.000 | 2,005,567 | - | 723s | 82 |

| Variant | environment | incorrect_patch | regression | budget_exhausted | policy_violation | gave_up |
|---|---|---|---|---|---|---|
| bump-pydantic | 0 | 3 | 0 | 0 | 0 | 0 |
| noop | 0 | 0 | 0 | 0 | 0 | 3 |
| agent | 0 | 0 | 0 | 1 | 0 | 0 |

Paired per-task comparison:

| A | B | tasks | A only | B only | both | neither | exact p |
|---|---|---|---|---|---|---|---|
| bump-pydantic | noop | 1 | 0 | 0 | 0 | 1 | 1.000 |
| agent | bump-pydantic | 1 | 0 | 0 | 0 | 1 | 1.000 |

## Track: synthetic_fixture (1 task: toy-users)

*SMOKE-TEST FIXTURE: built to exercise the pipeline. Not a benchmark result; ignore for any claim.*

| Variant | Verified | 95% CI | Regressions | Collection failed | Median cost | Median tokens | $/success | Median time | Median steps |
|---|---|---|---|---|---|---|---|---|---|
| bump-pydantic | 0/1 (0%) | 0%-79% | 0 attempts (0 tests) | 1/1 | $0.000 | 0 | - | 2s | 0 |
| noop | 0/1 (0%) | 0%-79% | 0 attempts (0 tests) | 0/1 | $0.000 | 0 | - | 0s | 0 |
| agent | 1/2 (50%) | 9%-91% | 0 attempts (0 tests) | 0/2 | $0.000 | 457,802 | $0.000 | 471s | 60 |

| Variant | environment | incorrect_patch | regression | budget_exhausted | policy_violation | gave_up |
|---|---|---|---|---|---|---|
| bump-pydantic | 0 | 1 | 0 | 0 | 0 | 0 |
| noop | 0 | 0 | 0 | 0 | 0 | 1 |
| agent | 0 | 0 | 0 | 1 | 0 | 0 |

Paired per-task comparison:

| A | B | tasks | A only | B only | both | neither | exact p |
|---|---|---|---|---|---|---|---|
| bump-pydantic | noop | 1 | 0 | 0 | 0 | 1 | 1.000 |
| agent | bump-pydantic | 1 | 1 | 0 | 0 | 0 | 1.000 |

Infrastructure errors excluded from the rates above (not agent results): agent: 9

*Cost = model API spend only (failed attempts included); compute excluded. Free/local models cost $0: use tokens. `configs/pricing.json` is a placeholder until verified. "Collection failed" means pytest aborted before running tests, so required tests were never executed (not observed assertion failures). With few tasks, report counts and traces, not just rates.*


## Warnings
- 9 infrastructure-error runs (category `environment`) were excluded from all rates
- multiple code versions contributed to these runs (agent code changed mid-experiment)
- all runs used the local backend (not a security boundary; Docker path unvalidated)
