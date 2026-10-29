# Methodology

## What counts as a solved task

A task is **verified** only if all of the following hold, checked in a *fresh* environment that the
agent never touched:

1. **Tests pass**: every *required* test passes under the pinned Pydantic v2 version.
   `required = (tests green on the pre-upgrade commit under v1) ∩ (tests green when the human
   upgrade's source-only changes are applied under v2)`. Tests missing from the run (collection
   errors) count as failing.
2. **Native v2** (policy `native_v2`): the same tests also pass with
   `PydanticDeprecatedSince20` promoted to an error. v1-style calls that still "work" in v2
   (`.dict()`, `parse_obj`, `@validator`, `class Config`) therefore fail the task.
3. **Policy-clean patch**: no `pydantic.v1` / `pydantic.deprecated` imports, no downgrade or
   `pydantic<2` pin, no skips/xfails, no warning suppression, no edits to tests / conftest / CI /
   pytest config.
4. **Isolation of evaluation**: verification rebuilds the workspace from `base_sha`, applies *only
   the agent's diff*, builds a new venv whose pins come from the task spec via `uv --override`
   (so editing `pyproject.toml` cannot change the verification environment), and runs tests with the
   network disabled.

`pytest is green` alone is **not** success.

## Task construction (three states)

| state | checkout | pydantic | must be |
|---|---|---|---|
| `v1_pass` | `base_sha` | v1 pin | green, identical passing set across 2 runs (no flakiness) |
| `reference_pass` | `base_sha` + (+ evaluator test adaptation) + reference commit's **non-test** diff | v2 pin | green on every required test, and clean under strict mode (native tasks) |
| `v2_fail` | `base_sha` (+ adaptation) | v2 pin | at least one required test failing (otherwise there is nothing to fix) |

`reference_pass` proves the task is *solvable by editing source only*, which is all the agent may do.
The human patch is never shown to the agent: the workspace is re-initialised as a single commit, so
`git log` / `git show` cannot reach it. It is a reference implementation, **not** an exact-diff answer key.

Tasks failing any state are kept as `excluded` with a reason and counted in the report, so the
denominator is honest ("N eligible of M candidates").

### Evaluator-owned test adaptation

Some real upgrades must change tests (error message formats, model equality, Optional semantics).
The agent can never edit tests. When a task needs test changes, the *evaluator* supplies them as a
patch (`test_adaptation_patch`), applied to the base before the agent starts and re-applied in
verification. `--derive-adaptation` builds that patch from the reference commit's test diff; the task
`notes` record it, and it **must be reviewed by a human**: it leaks the intended post-migration test
behaviour, which is the specification, but should never contain unrelated changes.

## Tracks, exceptions and provisional status

Results are reported per **track** and never pooled:

| track | rule |
|---|---|
| `strict_native` | no `pydantic.v1`/`deprecated` use, deprecation warnings are errors. Headline track |
| `compat_exception` | strict rules plus task-declared `policy_exceptions` (code + exact substring + rationale), applied to the agent, the baselines and the human reference alike |
| `lenient` | deprecated-but-working v2 APIs allowed (still no `pydantic.v1` shim): an easier, different objective |

The human reference must pass the same policy as everyone else; a reference that violates it excludes the task unless the
violation is covered by a declared exception. A task is `provisional` until its adaptation audit
(`pmigrate audit-adaptation`) has a human rationale for every flagged change.

## Failure accounting

`required_failing = required_not_run + required_failed`. *Not run* means pytest never executed the test (collection or import
failure, missing test); *failed* means it ran and failed. Reports show how many attempts hit collection failure. Each run
row stores the backend that actually executed it (`backend_used`) and the report warns when backends are mixed.

## Metrics

Primary: **verified rate** (with Wilson 95% CI), **regressions** (required tests that passed before the
agent started and fail after), **cost per attempt** incl. failed attempts, **$ per verified migration**
(total spend / successes), median and p90 wall time, steps per attempt, and a **failure category** for every
failed attempt: `environment`, `incorrect_patch`, `regression`, `budget_exhausted`, `policy_violation`,
`gave_up`. Pairwise variant comparisons use per-task discordant counts and an exact McNemar test; with
10-30 tasks, report counts, not just p-values.

Cost = model API spend only. Sandbox compute is not included. `configs/pricing.json` contains
**placeholder** rates: verify against current published pricing before quoting any dollar figure
(token counts are always logged, so cost can be recomputed).

## Splits and tuning discipline

Tasks are tagged `dev` or `heldout`. Iterate prompts/tools on `dev`. Heldout runs require
`--confirm-frozen`, and the report warns if the prompt hash changed between heldout runs of the same
variant (a sign of tuning on the test set). Every run row stores `prompt_hash` and `code_version`.

## Variants

| variant | what it is |
|---|---|
| `noop` | no changes: sanity-checks that tasks really fail at the start |
| `bump-pydantic` | Pydantic's own deterministic codemod (edits to protected paths reverted) |
| `agent` | plain-Python loop, tools: list/read/search/edit/write/run_tests |
| `agent+docs` | + `lookup_migration_rule` over the version-pinned migration guide + changelog |
| `agent+docs+plan` | + a tool-less planning call fed by a static inventory of v1 usages |
| `agent+...+revert` | + snapshot the best state; if a test run is worse, restore it |

All agent variants share model, budget, snapshots and verifier; each toggle is evaluated against the
same baseline so improvements are attributable. A hybrid (`bump-pydantic`, then agent repairs the rest) is a
natural next experiment, not implemented here.
