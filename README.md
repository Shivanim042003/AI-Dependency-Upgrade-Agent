# pmigrate: an evaluated Pydantic v1 → v2 migration agent

A small, plain-Python agent that migrates real repositories from Pydantic v1 to v2, plus the part that
matters more: a benchmark and harness that make "it worked" hard to fake and easy to measure against
Pydantic's own deterministic codemod, `bump-pydantic`.

> **Honest status (read this first)**
>
> This is currently an **implemented evaluation system, not a validated migration agent.**
>
> | piece | state |
> |---|---|
> | benchmark pipeline (3-state task prep, clean-room verifier, policy checks, tracks) | built and unit/integration tested; run on 2 real repos |
> | real tasks | **2, both provisional**: `openjd-model` (track `compat_exception`) and `vonage-sdk` (track `lenient`). Target was 5, and there is **no clean strict-native task yet** |
> | baselines | `noop` and `bump-pydantic` run on both: **0/2** verified (details below) |
> | agent loop, docs retrieval, planning, revert | control flow validated with **scripted** transcripts and a mock HTTP provider. **Never run against a live model.** Live behaviour (malformed tool calls, retries, usage accounting, budget stops) is unvalidated |
> | Docker backend | implemented, **never executed** (no daemon where this was built). `scripts/validate_backend.py` is the acceptance test to run first |
> | pricing | `configs/pricing.json` is a **placeholder**; no dollar figure here is verified |
> | LLM providers | Anthropic or any OpenAI-compatible endpoint (see `docs/NO_ANTHROPIC_KEY.md`) |
> | demo (FastAPI + UI) | verified end to end in `replay` mode only |
>
> **Claims this supports today:** a Pydantic migration-agent framework with clean-room verification, policy enforcement,
> bounded execution, SQLite reporting and a streaming demo; control flow validated with scripted transcripts.
> **Claims it does not support:** autonomous migrations, improvement over `bump-pydantic`, a measured migration cost,
> a validated Docker sandbox, or generalisation across repositories.

## Why this is built the way it is

"Tests are green" is a bad success metric for a migration agent. Pydantic v2 still ships the v1 API
(`pydantic.v1`) and keeps deprecated methods callable, so an agent can go green by importing `pydantic.v1` or
by weakening a test. Here a task counts as solved only if all of this holds in a **fresh environment the agent
never touched** (details in [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md)):

- every required test passes under the pinned v2 version, **and** (native tasks) also with
  `PydanticDeprecatedSince20` promoted to an error, so `.dict()` / `parse_obj` / `@validator` fail the task;
- no `pydantic.v1` shim, no downgrade, no skips/xfails/warning suppression, no edits to tests/conftest/CI/pytest config;
- the human upgrade commit is never visible to the agent (workspace history is rewritten to one commit) and is a
  *reference*, not an exact-diff key.

## Architecture

```
 candidate repo (base_sha, reference_sha)                         ┌───────────────────────────┐
        │  pmigrate prepare-task                                  │ results/runs.sqlite       │
        ▼                                                         │  runs · events · transcripts
 ┌─────────────────────────┐   v1_pass / reference_pass / v2_fail └────────────▲──────────────┘
 │ task spec + baseline.json│  (3 states, 2x flake check,                      │ log everything
 └────────────┬────────────┘   reference passes the SAME policy)               │
              ▼                                                                │
   fresh workspace (single commit, no upstream history)                        │
              │                                                                │
   ┌──────────┼───────────────────────────┐                                    │
   ▼          ▼                           ▼                                    │
 noop   bump-pydantic          agent loop (plain Python)                       │
                               run_tests → read errors → search → edit → rerun │
                               tools: list/read/search/edit/write/run_tests    │
                               toggles: +docs  +plan  +revert                  │
                               hard limits: steps · $ · wall · test runs       │
              └──────────┬────────────────┘                                    │
                         ▼  diff only                                          │
        ┌───────────────────────────────────────────────┐                      │
        │ clean-room verifier                           │                      │
        │  rebuild base → apply diff → NEW venv (pins   │──────────────────────┘
        │  from spec via uv --override) → tests offline │   success · category · regressions
        │  + strict-deprecation run + policy checks     │
        └───────────────────────────────────────────────┘
```

Backends: `docker` (disposable, no network during tests, caps dropped) or `local` (dev only). See
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## Quickstart

```bash
pip install -e ".[dev]"            # also needs `uv` and `git` on PATH
pmigrate check-env                 # what's available: uv, docker, API key
make test                          # unit tests (no network)        -> see `make test-all` for integration

pmigrate init-toy                  # synthetic smoke-test task (not part of reported results)
make serve                         # demo in replay mode (no API key, no spend): http://127.0.0.1:8000

# validate an execution backend BEFORE trusting its results (Docker path is unvalidated)
docker build -t pmigrate-runner docker/ && python scripts/validate_backend.py --backend docker --task toy-users

# real tasks
pmigrate discover https://github.com/<org>/<repo>          # find candidate upgrade commits
pmigrate prepare-task --id my-task --repo <url> --base <sha> --ref <sha> \
    --test-cmd "pytest -q tests" --test-deps pytest --backend docker
pmigrate fetch-docs --tag v2.10.0                          # pinned migration guide + changelog

# evaluation: pick a provider (no Anthropic key needed)
#   export PMIGRATE_PROVIDER=openai PMIGRATE_BASE_URL=<endpoint>/v1 PMIGRATE_API_KEY=<key>   # or none for local Ollama
#   export PMIGRATE_MODEL=<model id that supports tool/function calling>
pmigrate eval dev-1 --split dev --dry-run                  # job count and worst-case spend first
pmigrate eval dev-1 --split dev --variants noop bump-pydantic agent agent+docs agent+docs+plan \
    --attempts 3 --max-total-cost 25 --backend docker
pmigrate report --experiment dev-1                         # tables + failure analysis -> results/
```

Variants: `noop`, `bump-pydantic`, `agent`, `agent+docs`, `agent+docs+plan`, `…+revert`. Each toggle is compared
against the same baseline (same model, budgets, snapshots, tests) so a difference can be attributed.
Heldout tasks require `--confirm-frozen`; the report warns if the prompt changed between heldout runs.

## What has actually been measured so far

Two real tasks, no LLM involved (deterministic baselines), local backend, report split by track:

| task | track | required tests | `noop` | `bump-pydantic` |
|---|---|---|---|---|
| `openjd-model` | compat_exception (provisional) | 2,022 | not verified (`gave_up`) | **not verified**; pytest collection failed, so the required tests never executed |
| `vonage-sdk` | lenient (provisional) | 422 | not verified (`gave_up`) | **not verified**; pytest collection failed (`pydantic_core.SchemaError` at import), so the required tests never executed |

"0/2" means no verified migration, nothing more. It is **not** 2,022 / 422 observed assertion failures: the verifier now
records `required_not_run` (never executed) separately from `required_failed` (executed and failed), and the report shows a
"collection failed" column. n = 2 and both tasks are provisional, so this is development evidence only. The `noop` row is
not executed at all (empty patch short-circuits), so its "collection failed" count is not meaningful.

Agent rows (`agent`, `agent+docs`, `agent+docs+plan`): **not run yet**; target headline once they are:

> *Built a Pydantic v1→v2 migration agent and evaluated it on N tasks. It achieved X/N verified migrations vs Y/N
> for bump-pydantic, at a median $Z model cost per attempt (API only, failed attempts included).*

Fill X/Y/Z only from `pmigrate report`. With a free or local model cost is $0, so report **tokens per attempt** instead (logged, shown in the report) and name the model; use `--max-total-tokens` as the budget. `configs/pricing.json` holds **placeholder** rates: check them against
current Anthropic pricing before quoting dollars.

## Known limitations

- Small dataset (2 tasks). A held-out split needs ~10+ tasks to mean anything; until then everything is `dev`.
- `openjd-model`: the human reference imports `pydantic.v1.utils.lenient_issubclass`. This is now a **declared,
  machine-readable `policy_exceptions` entry** (code + exact substring + rationale) that applies identically to the agent,
  baselines and the reference, and the task is in its own `compat_exception` track, never pooled with strict-native.
  `vonage-sdk` is in the `lenient` track because its reference is only partly native.
- Derived test adaptations (`--derive-adaptation`) leak the post-migration test behaviour. `pmigrate audit-adaptation <task>` writes
  `docs/audits/<task>.md` flagging removed tests, net-removed assertions, changed exception types and expected values; the
  **rationale column is still TODO** until a human fills it in, which is what keeps tasks provisional.
- The policy checker is regex-level defence in depth; review a sample of "successful" diffs before publishing numbers.
- Cost excludes sandbox compute. Retrieval is BM25 over the migration guide (deliberately simple; measure before
  adding embeddings). No Langfuse integration: every event, tool call and transcript is in SQLite, and
  `runner.run_attempt(on_event=…)` is the single hook for exporting to a tracer.
- LangGraph is intentionally not used (a framework port would confound the planning/retrieval comparisons).

## Layout

```
src/pmigrate/  tasks · sandbox · env · workspace · policy · verify · prepare   (benchmark + harness)
               agent · tools · llm · scan · docs · baseline                      (agent + baselines)
               runner · evalrun · report · db · cli · toy                         (experiments)
app/           FastAPI demo (allowlisted tasks, SSE progress, diff/verification/docs tabs)
tasks/         task specs + baselines + CANDIDATES.md        docs/  methodology, threat model, resume/blog
tests/         unit (no network) + integration (builds real venvs, ~1 min)
docker/        runner image                                   scripts/probe_repos.py
```

## Current results

**Status: development evidence, not a benchmark.** The harness is validated end to end. On a 12-test synthetic
fixture the agent produced a verified migration (open-weights `gpt-oss-120b`). On two real repositories
(`openjd-model`, 2,022 tests; `vonage-sdk`, 422 tests) neither `bump-pydantic` nor the agent has yet produced a
verified migration.

- One attempt per cell; confidence intervals are wide and tasks are provisional (adaptations not yet human-reviewed).
- Infrastructure errors (rate limits, exhausted credit) are excluded from success rates and reported separately.
- The strongest model tried, `deepseek-v3.2`, showed correct migration reasoning (for example, spotting that the v2
  regex engine rejects lookaheads) but hit the wall-clock budget on a slow provider.
- Reproduce with `pmigrate report --model <name>`.
