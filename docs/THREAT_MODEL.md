# Execution safety

Running other people's test suites is running other people's code. Controls, by backend:

| control | Docker backend | Local backend |
|---|---|---|
| disposable worker per command | yes (`docker run --rm`) | no (subprocess) |
| no host secrets in the sandbox | empty env, only whitelisted vars | scrubbed env (no API keys) |
| no Docker socket | not mounted | n/a |
| restricted mounts | only the run dir + uv caches | whole host FS readable by the process |
| CPU / memory / pids limits | `--cpus`, `--memory`, `--pids-limit` | rlimits (CPU time, file size) only |
| network off after dependency install | `--network none` | `unshare -n` when the kernel allows it |
| non-root, caps dropped, no-new-privileges, read-only rootfs | yes | no |

**The local backend is a development convenience, not a security boundary.** The demo refuses live runs
on it unless `PMIGRATE_ALLOW_LOCAL_BACKEND=1`. The Docker backend is implemented but was not exercised in
the environment this project was built in (no Docker daemon); run `docker build -t pmigrate-runner docker/`
and `PMIGRATE_BACKEND=docker pmigrate check-env` before trusting it.

Agent-side: the agent has **no shell**. Its tools are file read/search/edit (confined to the repo, `.git`
and symlink escapes rejected, protected paths read-only) and `run_tests` (fixed command; selectors may not
start with `-`). Dependency installation (the only networked phase) happens in the harness, before the agent runs.

Demo-side: only allowlisted tasks run (`tasks/demo.json`), never user-supplied URLs; one run at a time,
per-IP hourly limit, per-run budget, daily spend cap.

Residual risks: the policy checker is heuristic; a determined agent could hide behaviour in source (e.g.
monkeypatching at import time). Mitigations: clean-room verification, strict-deprecation run, and manual
review of a sample of "successful" diffs (do this before publishing numbers).
