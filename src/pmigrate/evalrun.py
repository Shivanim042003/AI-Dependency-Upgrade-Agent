"""Batch evaluation: tasks x variants x attempts, resumable, with a global cost cap."""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .agent import prompt_hash
from .config import TASKS_DIR, Budget
from .db import DB
from .runner import BASELINE_VARIANTS, parse_variant, run_attempt
from .tasks import TaskSpec, load_all


@dataclass
class Job:
    task: TaskSpec
    variant: str
    attempt: int


def plan_jobs(tasks: list[TaskSpec], variants: list[str], attempts: int, db: DB, experiment: str) -> list[Job]:
    jobs = []
    for t in tasks:
        for v in variants:
            n = 1 if v in BASELINE_VARIANTS else attempts   # deterministic baselines run once
            for a in range(n):
                if not db.done(experiment, t.id, v, a):
                    jobs.append(Job(t, v, a))
    return jobs


def heldout_tuning_warnings(db: DB, tasks: list[TaskSpec], variants: list[str]) -> list[str]:
    """Flag prompts/code that changed between heldout runs of the same variant (= tuning on test)."""
    warn = []
    held = {t.id for t in tasks if t.split == "heldout"}
    if not held:
        return warn
    with db.conn() as c:
        for v in variants:
            rows = c.execute("SELECT DISTINCT prompt_hash FROM runs WHERE split='heldout' AND variant=? "
                             "AND prompt_hash IS NOT NULL", (v,)).fetchall()
            if len(rows) > 1:
                warn.append(f"variant {v}: {len(rows)} different prompt hashes already used on heldout tasks")
    return warn


def run_experiment(experiment: str, *, variants: list[str], split: Optional[str] = None,
                   task_ids: Optional[list[str]] = None, attempts: int = 1, workers: int = 2,
                   model: Optional[str] = None, budget: Optional[Budget] = None, backend: str = "auto",
                   max_total_cost: Optional[float] = None, dry_run: bool = False,
                   confirm_frozen: bool = False, include_synthetic: bool = False, db: Optional[DB] = None,
                   tasks_dir: Path = TASKS_DIR, llm=None, log=print) -> dict:
    db = db or DB()
    budget = budget or Budget()
    tasks = load_all(tasks_dir, eligible_only=True, split=split)
    if task_ids:
        tasks = [t for t in tasks if t.id in task_ids]          # naming a fixture explicitly is allowed
    elif not include_synthetic:
        tasks = [t for t in tasks if not t.synthetic]           # fixtures never sneak into experiments
    if not tasks:
        raise SystemExit("no eligible tasks match (run `pmigrate prepare-task` first)")
    for v in variants:
        parse_variant(v)   # validate names early
    if any(t.split == "heldout" for t in tasks) and not (confirm_frozen or dry_run):
        raise SystemExit("heldout tasks selected: freeze prompts/code on the dev split first, then pass "
                         "--confirm-frozen. (Heldout results are only meaningful if you never tune on them.)")
    for w in heldout_tuning_warnings(db, tasks, variants):
        log(f"WARNING: {w}")

    jobs = plan_jobs(tasks, variants, attempts, db, experiment)
    n_agent = sum(1 for j in jobs if j.variant not in BASELINE_VARIANTS)
    log(f"experiment={experiment}: {len(tasks)} tasks, variants={variants}, {len(jobs)} jobs to run "
        f"({n_agent} agent attempts, worst-case ${n_agent * budget.max_cost_usd:.2f} in model spend)")
    if dry_run:
        return {"jobs": len(jobs), "agent_jobs": n_agent, "worst_case_usd": n_agent * budget.max_cost_usd}

    spent, lock = [0.0], threading.Lock()
    results = []

    def work(j: Job):
        with lock:
            if max_total_cost is not None and j.variant not in BASELINE_VARIANTS and spent[0] >= max_total_cost:
                return j, None, "skipped: global cost cap reached"
        try:
            rec = run_attempt(j.task, j.variant, j.attempt, experiment=experiment, model=model, budget=budget,
                              backend=backend, db=db, llm=llm, tasks_dir=tasks_dir)
        except Exception as e:
            return j, None, f"{type(e).__name__}: {e}"
        with lock:
            spent[0] += rec.cost_usd
        return j, rec, None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, j) for j in jobs]
        for f in as_completed(futs):
            j, rec, err = f.result()
            done += 1
            if err:
                log(f"[{done}/{len(jobs)}] {j.task.id} {j.variant}#{j.attempt}: {err}")
            else:
                log(f"[{done}/{len(jobs)}] {j.task.id} {j.variant}#{j.attempt}: "
                    f"{'PASS' if rec.success else 'fail:' + str(rec.failure_category)} "
                    f"${rec.cost_usd:.3f} {rec.steps} steps {rec.agent_wall_s:.0f}s")
            results.append((j, rec, err))
    return {"jobs": len(jobs), "spent_usd": spent[0], "done": done}
