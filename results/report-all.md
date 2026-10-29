# Results

Eligible tasks in these runs: **3** | excluded during preparation: **0** (of 4 candidates)

> **Provisional tasks (adaptations/exceptions not yet human-reviewed): openjd-model, vonage-sdk.** Treat results as development evidence, not benchmark claims.

## Track: compat_exception (1 task: openjd-model)

*Strict native rules plus task-declared, machine-readable exceptions (same for every party). Reported separately.*

| Variant | Verified | 95% CI | Regressions | Collection failed | Median cost | Median tokens | $/success | Median time | Median steps |
|---|---|---|---|---|---|---|---|---|---|
| bump-pydantic | 0/3 (0%) | 0%-56% | 0 attempts (0 tests) | 2/3 | $0.000 | 0 | - | 73s | 0 |
| noop | 0/3 (0%) | 0%-56% | 0 attempts (0 tests) | 0/3 | $0.000 | 0 | - | 0s | 0 |

| Variant | environment | incorrect_patch | regression | budget_exhausted | policy_violation | gave_up |
|---|---|---|---|---|---|---|
| bump-pydantic | 0 | 3 | 0 | 0 | 0 | 0 |
| noop | 0 | 0 | 0 | 0 | 0 | 3 |

Paired per-task comparison:

| A | B | tasks | A only | B only | both | neither | exact p |
|---|---|---|---|---|---|---|---|
| bump-pydantic | noop | 1 | 0 | 0 | 0 | 1 | 1.000 |

## Track: lenient (1 task: vonage-sdk)

*Deprecated-but-working v2 APIs allowed (no `pydantic.v1` shim). A different, easier objective; never pool with strict.*

| Variant | Verified | 95% CI | Regressions | Collection failed | Median cost | Median tokens | $/success | Median time | Median steps |
|---|---|---|---|---|---|---|---|---|---|
| bump-pydantic | 0/3 (0%) | 0%-56% | 0 attempts (0 tests) | 2/3 | $0.000 | 0 | - | 22s | 0 |
| noop | 0/3 (0%) | 0%-56% | 0 attempts (0 tests) | 0/3 | $0.000 | 0 | - | 0s | 0 |
| agent | 0/1 (0%) | 0%-79% | 0 attempts (0 tests) | 1/1 | $0.308 | 1,025,767 | - | 2401s | 39 |

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

## Track: strict_native (1 task: llm-pydantic-v2)

*No v1 shims, no exceptions; deprecation warnings are errors. The headline track.*

| Variant | Verified | 95% CI | Regressions | Collection failed | Median cost | Median tokens | $/success | Median time | Median steps |
|---|---|---|---|---|---|---|---|---|---|
| bump-pydantic | 0/2 (0%) | 0%-66% | 0 attempts (0 tests) | 0/2 | $0.000 | 0 | - | 29s | 0 |
| noop | 0/1 (0%) | 0%-79% | 0 attempts (0 tests) | 0/1 | $0.000 | 0 | - | 0s | 0 |
| agent | 1/3 (33%) | 6%-79% | 0 attempts (0 tests) | 0/3 | $0.058 | 102,566 | $0.171 | 442s | 40 |
| agent+docs | 3/3 (100%) | 44%-100% | 0 attempts (0 tests) | 0/3 | $0.047 | 71,166 | $0.050 | 388s | 40 |

| Variant | environment | incorrect_patch | regression | budget_exhausted | policy_violation | gave_up |
|---|---|---|---|---|---|---|
| bump-pydantic | 0 | 2 | 0 | 0 | 0 | 0 |
| noop | 0 | 0 | 0 | 0 | 0 | 1 |
| agent | 0 | 0 | 0 | 2 | 0 | 0 |
| agent+docs | 0 | 0 | 0 | 0 | 0 | 0 |

Paired per-task comparison:

| A | B | tasks | A only | B only | both | neither | exact p |
|---|---|---|---|---|---|---|---|
| bump-pydantic | noop | 1 | 0 | 0 | 0 | 1 | 1.000 |
| agent | bump-pydantic | 1 | 1 | 0 | 0 | 0 | 1.000 |
| agent+docs | bump-pydantic | 1 | 1 | 0 | 0 | 0 | 1.000 |

Infrastructure errors excluded from the rates above (not agent results): agent: 3, agent+docs: 3

## Track: synthetic_fixture (1 task: toy-users)

*SMOKE-TEST FIXTURE: built to exercise the pipeline. Not a benchmark result; ignore for any claim.*

| Variant | Verified | 95% CI | Regressions | Collection failed | Median cost | Median tokens | $/success | Median time | Median steps |
|---|---|---|---|---|---|---|---|---|---|
| bump-pydantic | 0/1 (0%) | 0%-79% | 0 attempts (0 tests) | 1/1 | $0.000 | 0 | - | 2s | 0 |
| noop | 0/1 (0%) | 0%-79% | 0 attempts (0 tests) | 0/1 | $0.000 | 0 | - | 0s | 0 |

| Variant | environment | incorrect_patch | regression | budget_exhausted | policy_violation | gave_up |
|---|---|---|---|---|---|---|
| bump-pydantic | 0 | 1 | 0 | 0 | 0 | 0 |
| noop | 0 | 0 | 0 | 0 | 0 | 1 |

Paired per-task comparison:

| A | B | tasks | A only | B only | both | neither | exact p |
|---|---|---|---|---|---|---|---|
| bump-pydantic | noop | 1 | 0 | 0 | 0 | 1 | 1.000 |

*Cost = model API spend only (failed attempts included); compute excluded. Free/local models cost $0: use tokens. `configs/pricing.json` is a placeholder until verified. "Collection failed" means pytest aborted before running tests, so required tests were never executed (not observed assertion failures). With few tasks, report counts and traces, not just rates.*


## Warnings
- 6 infrastructure-error runs (category `environment`) were excluded from all rates
- multiple code versions contributed to these runs (agent code changed mid-experiment)
- results mix execution backends (docker, local); do not pool them
