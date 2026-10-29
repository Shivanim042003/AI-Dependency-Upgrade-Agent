# Resume bullets and blog outline

> Fill the `<...>` slots from `pmigrate report --split heldout`. Do not publish a number you cannot
> reproduce from `results/runs.sqlite`. Keep the exclusions and cost definition next to the headline.

## Resume bullets (pick 3)

- Built an evaluated Pydantic v1→v2 migration agent (plain-Python tool loop, version-pinned docs retrieval,
  optional planning and revert-on-regression); verified **<X>/<N>** held-out historical migrations vs
  **<Y>/<N>** for the deterministic `bump-pydantic` codemod, at a median **$<Z>** model cost per attempt.
- Designed a "hard to fake" benchmark: tasks reconstructed from real upgrade commits in pinned sandboxes, clean-room
  verification, native-v2 enforcement (deprecation warnings as errors), protected tests, and policy checks that
  reject `pydantic.v1` shims, downgrades, skips and test edits.
- Ran retrieval and planning ablations against a shared baseline with paired per-task comparisons, Wilson CIs and
  failure taxonomy (`<k>` regressions, `<k>` policy violations), finding that `<docs/plan>` `<did/didn't>` help.
- Shipped an allowlisted FastAPI demo (SSE progress, diff, verification, retrieved docs) with disposable-container
  execution, spend caps and per-run budgets.

## Blog outline (≈1,200 words)

1. **Hook**: "Tests are green" is a bad success metric for a migration agent — here is how an agent can pass
   by importing `pydantic.v1`.
2. **The task and the baseline**: why `bump-pydantic` is the thing to beat; what it cannot do (Optional-default
   semantics, `BaseSettings` move, behaviour changes).
3. **Building the benchmark**: three states per task, `reference_pass` via source-only diff, evaluator-owned test
   adaptation, exclusions as a first-class result (`<M>` candidates → `<N>` eligible, and why).
4. **Making success hard to fake**: protected paths, clean-room verification, strict deprecation run, `--override`
   pins. One concrete cheating attempt the harness caught.
5. **Results**: table (verified rate + CI, regressions, $/success, time), the paired comparison, and honesty about
   sample size.
6. **Failure analysis**: three representative failures by category, with the diff and what the agent misunderstood.
7. **What retrieval / planning actually did** (including "nothing", if that is the result).
8. **Limits and next steps**: hybrid (`bump-pydantic` then agent), more tasks, behavioural-change subset, human review
   of sampled successes.
