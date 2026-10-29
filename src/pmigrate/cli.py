"""`pmigrate` command line."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .config import RESULTS_DIR, TASKS_DIR, Budget


def _budget(a) -> Budget:
    b = Budget()
    for k in ("max_steps", "max_cost_usd", "max_wall_s", "max_test_runs", "max_total_tokens"):
        v = getattr(a, k, None)
        if v is not None:
            setattr(b, k, v)
    return b


def cmd_check_env(a):
    from .sandbox import can_unshare_net, docker_available
    from .llm import llm_configured
    rows = [
        ("uv", bool(shutil.which("uv")), "needed to provision per-task Python + venvs"),
        ("git", bool(shutil.which("git")), ""),
        ("docker daemon", docker_available(), "recommended backend for untrusted repos (PMIGRATE_BACKEND=docker)"),
        ("local net isolation (unshare -n)", can_unshare_net(), "only matters for the local backend"),
        ("LLM provider configured", llm_configured(),
         "agent variants need one: ANTHROPIC_API_KEY, or PMIGRATE_PROVIDER=openai + PMIGRATE_BASE_URL (+ key)"),
    ]
    for name, ok, note in rows:
        print(f"{'OK ' if ok else 'NO '} {name:34s} {note}")
    from .config import CACHE_DIR
    print(f"cache dir: {CACHE_DIR}")


def cmd_fetch_docs(a):
    from .docs import fetch_docs
    for p in fetch_docs(a.tag):
        print("wrote", p)


def cmd_discover(a):
    from .prepare import discover_upgrade_commits
    from .tasks import TaskSpec
    from .workspace import upstream_clone
    t = TaskSpec(id=a.id, repo_url=a.repo, base_sha="HEAD", reference_sha="HEAD")
    up = upstream_clone(t)
    cands = discover_upgrade_commits(up, a.limit)
    print(json.dumps(cands, indent=2))
    if not cands:
        print("no obvious v1->v2 dependency-bump commits found", file=sys.stderr)


def cmd_prepare(a):
    from .prepare import prepare_task
    from .tasks import TaskSpec, load_task, task_path
    if a.from_json:
        t = TaskSpec(**json.loads(Path(a.from_json).read_text()))
    else:
        t = TaskSpec(id=a.id, repo_url=a.repo, base_sha=a.base, reference_sha=a.ref,
                     python_version=a.python, pydantic_before=a.pydantic_before, pydantic_after=a.pydantic_after,
                     extra_deps_after=a.extra_after or [], extra_deps_before=a.extra_before or [],
                     install_cmd=a.install, exclude_newer=a.exclude_newer, test_deps=a.test_deps or ["pytest"], test_cmd=a.test_cmd,
                     policy=a.policy, split=a.split, notes=a.notes or "", deselect=a.deselect or [],
                     policy_exceptions=[dict(zip(("code", "contains", "rationale"), x.split("|", 2))) for x in a.exception],
                     provisional=a.provisional)
    r = prepare_task(t, backend=a.backend, derive_adaptation=a.derive_adaptation, strip_deprecation=a.strip_deprecation,
                     max_dropped_frac=a.max_dropped_frac)
    print(f"{r.id}: {r.status}" + (f" ({r.exclusion_reason})" if r.exclusion_reason else ""))


def cmd_run(a):
    from .db import DB
    from .runner import run_attempt
    from .tasks import load_task
    t = load_task(a.task)
    rec = run_attempt(t, a.variant, a.attempt, experiment=a.experiment, model=a.model, budget=_budget(a),
                      backend=a.backend, db=DB(), keep_dir=a.keep,
                      on_event=(lambda rid, ev: print(f"  [{ev['type']}] {json.dumps(ev['payload'], default=str)[:160]}")) if a.verbose else None)
    v = rec.verification
    print(json.dumps({"run_id": rec.run_id, "success": rec.success, "failure": rec.failure_category,
                      "end_reason": rec.end_reason, "cost_usd": round(rec.cost_usd, 4), "steps": rec.steps,
                      "required_failing": len(v.required_failing), "regressions": len(v.regressions),
                      "violations": v.violations[:5]}, indent=2))


def cmd_eval(a):
    from .evalrun import run_experiment
    out = run_experiment(a.experiment, variants=a.variants, split=a.split, task_ids=a.tasks, attempts=a.attempts,
                         workers=a.workers, model=a.model, budget=_budget(a), backend=a.backend,
                         max_total_cost=a.max_total_cost, dry_run=a.dry_run, confirm_frozen=a.confirm_frozen,
                         include_synthetic=a.include_synthetic)
    print(json.dumps(out))


def cmd_report(a):
    from .db import DB
    from .report import build_report
    md, data = build_report(DB(), a.experiment, a.split, model=a.model)
    RESULTS_DIR.mkdir(exist_ok=True)
    name = f"report-{a.experiment or 'all'}{'-' + a.split if a.split else ''}"
    (RESULTS_DIR / f"{name}.md").write_text(md)
    (RESULTS_DIR / f"{name}.json").write_text(json.dumps(data, indent=2, default=str))
    print(md)


def cmd_init_toy(a):
    from .toy import init_toy_task
    t = init_toy_task(backend=a.backend)
    print(f"{t.id}: {t.status}")


def cmd_audit(a):
    from .audit import audit_patch, render_markdown
    from .config import ROOT
    from .tasks import load_task
    t = load_task(a.task)
    md = render_markdown(t, audit_patch(t.test_adaptation_patch or ""))
    out = ROOT / "docs" / "audits" / f"{t.id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(md)
    print(f"wrote {out}")


def cmd_serve(a):
    import uvicorn
    from .config import ROOT
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("PMIGRATE_DEMO_MODE", a.mode)
    uvicorn.run("app.server:app", host=a.host, port=a.port, reload=False)


def main(argv=None):
    p = argparse.ArgumentParser(prog="pmigrate", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def budget_args(sp):
        sp.add_argument("--max-steps", type=int, dest="max_steps")
        sp.add_argument("--max-cost-usd", type=float, dest="max_cost_usd")
        sp.add_argument("--max-wall-s", type=int, dest="max_wall_s")
        sp.add_argument("--max-total-tokens", type=int, dest="max_total_tokens")
        sp.add_argument("--max-test-runs", type=int, dest="max_test_runs")

    s = sub.add_parser("check-env"); s.set_defaults(fn=cmd_check_env)
    s = sub.add_parser("init-toy", help="build + register the synthetic smoke-test task")
    s.add_argument("--backend", default="auto"); s.set_defaults(fn=cmd_init_toy)
    s = sub.add_parser("fetch-docs"); s.add_argument("--tag", default="v2.10.0"); s.set_defaults(fn=cmd_fetch_docs)

    s = sub.add_parser("discover", help="find candidate upgrade commits in a repo")
    s.add_argument("repo"); s.add_argument("--id", default="probe"); s.add_argument("--limit", type=int, default=5)
    s.set_defaults(fn=cmd_discover)

    s = sub.add_parser("prepare-task", help="verify the 3 states and write tasks/<id>.json")
    s.add_argument("--from-json"); s.add_argument("--id"); s.add_argument("--repo"); s.add_argument("--base")
    s.add_argument("--ref"); s.add_argument("--python", default="3.11")
    s.add_argument("--pydantic-before", default="1.10.13"); s.add_argument("--pydantic-after", default="2.10.0")
    s.add_argument("--extra-after", nargs="*"); s.add_argument("--extra-before", nargs="*")
    s.add_argument("--install", default="-e ."); s.add_argument("--test-deps", nargs="*")
    s.add_argument("--exclude-newer", default="", help="resolve all dependencies as of this date (uv --exclude-newer), e.g. 2025-02-27")
    s.add_argument("--test-cmd", default="pytest -q"); s.add_argument("--policy", default="native_v2")
    s.add_argument("--split", default="dev"); s.add_argument("--notes")
    s.add_argument("--deselect", nargs="*", help="pytest node ids to exclude (environment artefacts)")
    s.add_argument("--derive-adaptation", action="store_true",
                   help="use the reference commit's test diff as evaluator-owned test adaptation (review it!)")
    s.add_argument("--exception", action="append", default=[], metavar="CODE|CONTAINS|RATIONALE",
                   help="declare a policy exception that applies to agent, baselines and human reference alike")
    s.add_argument("--provisional", action="store_true", help="mark adaptations/exceptions as not yet human-reviewed")
    s.add_argument("--strip-deprecation", action="store_true",
                   help="unwrap pytest.deprecated_call() in a derived adaptation so tests don't demand deprecated usage")
    s.add_argument("--max-dropped-frac", type=float, default=0.01,
                   help="max fraction of v1-passing tests the reference itself may break (they are dropped from `required`)")
    s.add_argument("--backend", default="auto"); s.set_defaults(fn=cmd_prepare)

    s = sub.add_parser("run", help="one attempt on one task")
    s.add_argument("task"); s.add_argument("--variant", default="agent+docs"); s.add_argument("--attempt", type=int, default=0)
    s.add_argument("--experiment", default="adhoc"); s.add_argument("--model"); s.add_argument("--backend", default="auto")
    s.add_argument("--keep", action="store_true"); s.add_argument("-v", "--verbose", action="store_true")
    budget_args(s); s.set_defaults(fn=cmd_run)

    s = sub.add_parser("eval", help="run variants x tasks x attempts")
    s.add_argument("experiment"); s.add_argument("--variants", nargs="+",
                   default=["noop", "bump-pydantic", "agent", "agent+docs"])
    s.add_argument("--split", choices=["dev", "heldout"]); s.add_argument("--tasks", nargs="*")
    s.add_argument("--attempts", type=int, default=1); s.add_argument("--workers", type=int, default=2)
    s.add_argument("--model"); s.add_argument("--backend", default="auto")
    s.add_argument("--max-total-cost", type=float, dest="max_total_cost")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--confirm-frozen", action="store_true")
    s.add_argument("--include-synthetic", action="store_true", help="also run the synthetic smoke fixture (never reported)")
    budget_args(s); s.set_defaults(fn=cmd_eval)

    s = sub.add_parser("report"); s.add_argument("--experiment"); s.add_argument("--split", choices=["dev", "heldout"])
    s.add_argument("--model", help="only agent runs whose model name contains this text (e.g. gpt-oss)")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("audit-adaptation", help="list what a task's test adaptation changes (for human review)")
    s.add_argument("task"); s.set_defaults(fn=cmd_audit)

    s = sub.add_parser("serve", help="FastAPI demo (allowlisted tasks only)")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8000)
    s.add_argument("--mode", choices=["live", "replay"], default="live"); s.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
