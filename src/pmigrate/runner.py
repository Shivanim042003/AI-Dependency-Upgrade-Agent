"""Run one attempt end to end: workspace -> (agent | bump-pydantic | noop) -> clean verify -> log."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .agent import AgentResult, prompt_hash, run_agent
from .baseline import run_bump_pydantic
from .config import CACHE_DIR, ROOT, AgentConfig, Budget
from .db import DB
from .docs import MigrationDocs
from .env import Env
from .sandbox import make_sandbox
from .tasks import TaskSpec
from .tools import ToolBox
from .verify import Baseline, Verification, classify_failure, verify_patch
from .workspace import Workspace, prepare_workspace

BASELINE_VARIANTS = ("noop", "bump-pydantic")


def parse_variant(name: str, model: Optional[str] = None, budget: Optional[Budget] = None) -> Optional[AgentConfig]:
    """'agent+docs+plan+revert' -> AgentConfig. Returns None for non-agent baselines."""
    if name in BASELINE_VARIANTS:
        return None
    parts = name.split("+")
    if parts[0] != "agent" or any(p not in ("docs", "plan", "revert") for p in parts[1:]):
        raise ValueError(f"unknown variant {name!r}; use noop, bump-pydantic, or agent[+docs][+plan][+revert]")
    cfg = AgentConfig(use_docs="docs" in parts, use_planning="plan" in parts, revert_on_worse="revert" in parts)
    if model:
        cfg.model = model
    if budget:
        cfg.budget = budget
    return cfg


def code_version() -> str:
    h = hashlib.sha256()
    for p in sorted((ROOT / "src" / "pmigrate").glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:10]


@dataclass
class RunRecord:
    run_id: str
    task_id: str
    variant: str
    success: bool
    failure_category: Optional[str]
    end_reason: str
    cost_usd: float
    steps: int
    agent_wall_s: float
    verification: Verification
    agent: Optional[AgentResult] = None
    diff: str = ""
    events: list = field(default_factory=list)


def run_attempt(task: TaskSpec, variant: str, attempt: int = 0, *, experiment: str = "adhoc",
                model: Optional[str] = None, budget: Optional[Budget] = None, backend: str = "auto",
                db: Optional[DB] = None, llm=None, on_event: Optional[Callable] = None,
                keep_dir: bool = False, tasks_dir: Optional[Path] = None) -> RunRecord:
    from .config import TASKS_DIR
    tasks_dir = tasks_dir or TASKS_DIR
    cfg = parse_variant(variant, model, budget)
    run_id = uuid.uuid4().hex[:12]
    events: list = []

    def emit(type_: str, **payload):
        ev = {"ts": time.time(), "type": type_, "payload": payload}
        events.append(ev)
        if on_event:
            on_event(run_id, ev)

    run_dir = CACHE_DIR / "runs" / run_id
    sb = make_sandbox(run_dir, backend)
    backend_used = sb.name
    started = time.time()
    agent_res: Optional[AgentResult] = None
    diff, end_reason, agent_wall, error = "", "finished", 0.0, None
    emit("start", run_id=run_id, task=task.id, variant=variant, backend=sb.name, attempt=attempt)
    try:
        base_commit = prepare_workspace(task, sb.repo_dir)
        ws = Workspace(sb.repo_dir, base_commit)
        if variant == "noop":
            end_reason = "gave_up"
        elif variant == "bump-pydantic":
            t0 = time.time()
            br = run_bump_pydantic(ws)
            diff, agent_wall = br.diff, time.time() - t0
            emit("bump_pydantic", returncode=br.returncode, output=br.output[-1200:],
                 reverted_protected=br.reverted_protected)
        else:
            env = Env(sb, task, "v2")
            r = env.setup()
            if not r.ok:
                raise RuntimeError(f"env setup failed: {r.tail(500)}")
            base = Baseline.load(task.id, tasks_dir)
            docs = MigrationDocs(f"v{task.pydantic_after}") if cfg.use_docs else None
            if llm is None:
                from .llm import make_llm
                llm = make_llm()
            tb = ToolBox(ws=ws, env=env, base=base, cfg=cfg, docs=docs, emit=emit)
            agent_res = run_agent(task, tb, cfg, llm, emit)
            diff, end_reason, agent_wall, error = agent_res.diff, agent_res.end_reason, agent_res.wall_s, agent_res.error
    except Exception as e:
        end_reason, error = "error", f"{type(e).__name__}: {str(e)[:500]}"
        emit("error", message=error)
    finally:
        if not keep_dir:
            sb.cleanup()

    # ---- clean-room verification (fresh workspace + fresh env) --------------------------
    emit("verify_start", diff_chars=len(diff))
    if end_reason == "error" and not diff:
        v = Verification(env_error=error, required_failing=[])
        v.empty_patch = True
    else:
        v = verify_patch(task, diff, backend=backend, tasks_dir=tasks_dir)
    category = classify_failure(end_reason, v) if end_reason != "error" else "environment"
    emit("verified", success=v.success, category=category, violations=v.violations[:5])

    rec = RunRecord(run_id, task.id, variant, v.success, category, end_reason,
                    agent_res.cost_usd if agent_res else 0.0, agent_res.steps if agent_res else 0,
                    agent_wall, v, agent_res, diff, events)
    if db is not None:
        _log(db, rec, task, cfg, experiment, attempt, backend_used, started, error)
    return rec


def _log(db: DB, rec: RunRecord, task: TaskSpec, cfg: Optional[AgentConfig], experiment: str,
         attempt: int, backend: str, started: float, error: Optional[str]) -> None:
    a, v = rec.agent, rec.verification
    u = a.usage if a else None
    db.insert_run({
        "run_id": rec.run_id, "experiment": experiment, "task_id": task.id, "variant": rec.variant,
        "attempt": attempt, "split": task.split, "model": cfg.model if cfg else None, "backend": backend, "backend_used": backend,
        "track": task.track, "provisional": int(task.provisional),
        "required_not_run": len(v.required_not_run), "collection_failed": int(v.collection_failed),
        "prompt_hash": prompt_hash(task, cfg) if cfg else None, "code_version": code_version(),
        "started_at": started, "ended_at": time.time(), "end_reason": rec.end_reason,
        "success": int(rec.success), "tests_pass": int(v.tests_pass),
        "native_ok": None if v.native_ok is None else int(v.native_ok), "policy_ok": int(v.policy_ok),
        "required_total": v.required_total, "required_failing": len(v.required_failing),
        "regressions": len(v.regressions), "failure_category": rec.failure_category,
        "steps": rec.steps, "tool_calls": a.tool_calls if a else 0, "test_runs": a.test_runs if a else 0,
        "reverts": a.reverts if a else 0,
        "input_tokens": u.input_tokens if u else 0, "output_tokens": u.output_tokens if u else 0,
        "cache_read_tokens": u.cache_read_tokens if u else 0, "cache_write_tokens": u.cache_write_tokens if u else 0,
        "cost_usd": rec.cost_usd, "agent_wall_s": rec.agent_wall_s, "verify_wall_s": v.duration_s,
        "diff": rec.diff, "plan": a.plan if a else None, "summary": a.summary if a else None,
        "verification_json": json.dumps(v.to_json()),
        "config_json": json.dumps(asdict(cfg), default=str) if cfg else None, "error": error,
    })
    db.add_events(rec.run_id, rec.events)
    if a:
        db.save_transcript(rec.run_id, a.transcript)
