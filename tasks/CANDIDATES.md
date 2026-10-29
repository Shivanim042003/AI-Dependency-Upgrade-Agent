# Candidate log (Week 1 dataset)

Every repo probed with `scripts/probe_repos.py` / `pmigrate discover`, and what happened. Keeping the
rejected ones is the point: the report's denominator is "eligible of candidates", not "the ones that worked".

| repo | commit | outcome | reason |
|---|---|---|---|
| Vonage/vonage-python-sdk | `0a8d217` "Migrate to pydantic 2" | **eligible, provisional** (`vonage-sdk`, track `lenient`) | 422 required tests. Reference is only partly native (still uses deprecated `@validator`, and its test diff wraps calls in `pytest.deprecated_call()`), so the adaptation was unwrapped with `--strip-deprecation` and the task runs under `lenient` |
| OpenJobDescription/openjd-model-for-python | `67b324f` "Update model parsing to use Pydantic V2" | **eligible, provisional** (`openjd-model`, track `compat_exception`) | 2,022 required tests, strict-clean. The human reference imports `pydantic.v1.utils.lenient_issubclass`; this is a declared `policy_exceptions` entry (same rule for agent, baselines, reference). 3 tests the reference itself breaks are dropped from `required` (the adaptation rewrites them under new names); 1 environment-artefact test is deselected. See `docs/audits/openjd-model.md` |
| HomeAssistant-API/HomeAssistantAPI | `6da6f6d` "migrate to pydantic v2.0" | excluded | tests need a live Home Assistant server (`HOMEASSISTANTAPI_URL`/`TOKEN`); not runnable offline |
| yanyongyu/githubkit | `e7a96da` "Support pydantic v2" | not prepared | dual v1/v2 compatibility layer (reference would violate the native policy); 59 files |
| cloudevents/sdk-python | `5a1063e` "Pydantic v2 native implementation" | not prepared | adds parallel `pydantic/v1` and `v2` packages (dual support, not a migration) |
| pgjones/quart-schema | `5067539` "Initially support Pydantic >=2" | not prepared | dual support |
| frenck/python-tailscale | `dc08e6b` "Bump pydantic 1.10.9 → 2.0.2" | not prepared | dependabot bump, no code change; the code migration is a different commit |
| gtalarico/pyairtable, fastapi-users, atlanhq/atlan-python, NowanIlfideme/pydantic-yaml, aiogram, scalekit-sdk-python, modelcontextprotocol/python-sdk, alcrene/scityping | - | not prepared | no clean single-commit v1→v2 code migration found by the heuristic, or dual-support/removal-of-v1 commits |

**Status: 2 real eligible tasks, both provisional, none in the strict-native track (+1 synthetic smoke fixture that is excluded from reported results).**
The Week-1 goal was 5; getting there is mostly candidate hunting. Discovery tips: search PR titles
("Migrate to Pydantic v2"), run `pmigrate discover <repo>`, prefer libraries (pure-Python, offline tests, 5-30 changed
source files). Prefer commits that *replace* v1 code over ones that add a dual-support layer.
