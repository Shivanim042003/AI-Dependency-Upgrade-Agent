"""Aggregate runs into the honest numbers: success, regressions, cost, time, failure modes."""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from .config import TASKS_DIR
from .db import DB
from .tasks import load_all


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar on discordant pairs (b = A-only, c = B-only)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def pct(xs: list, q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    i = (len(xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def summarize(rows: list) -> dict:
    by_variant = defaultdict(list)
    for r in rows:
        by_variant[r["variant"]].append(r)
    out = {}
    for v, rs in by_variant.items():
        n = len(rs)
        k = sum(r["success"] for r in rs)
        tasks = {r["task_id"] for r in rs}
        solved_tasks = {r["task_id"] for r in rs if r["success"]}
        costs = [r["cost_usd"] or 0 for r in rs]
        walls = [(r["agent_wall_s"] or 0) for r in rs]
        out[v] = {
            "attempts": n, "tasks": len(tasks), "successes": k,
            "tasks_solved_at_least_once": len(solved_tasks),
            "success_rate": k / n if n else 0.0, "ci95": wilson(k, n),
            "attempts_collection_failed": sum(1 for r in rs if r.get("collection_failed")),
            "regressions_total": sum(r["regressions"] or 0 for r in rs),
            "attempts_with_regression": sum(1 for r in rs if (r["regressions"] or 0) > 0),
            "tokens_median": statistics.median([(r.get("input_tokens") or 0) + (r.get("output_tokens") or 0) for r in rs]) if rs else 0,
            "cost_median": statistics.median(costs) if costs else 0.0,
            "cost_mean": statistics.fmean(costs) if costs else 0.0,
            "cost_p90": pct(costs, 0.9), "cost_total": sum(costs),
            "cost_per_success": (sum(costs) / k) if k else None,
            "wall_median_s": statistics.median(walls) if walls else 0.0, "wall_p90_s": pct(walls, 0.9),
            "steps_median": statistics.median([r["steps"] or 0 for r in rs]) if rs else 0,
            "test_runs_median": statistics.median([r["test_runs"] or 0 for r in rs]) if rs else 0,
            "failures": dict(Counter(r["failure_category"] for r in rs if not r["success"])),
            "models": sorted({r["model"] for r in rs if r["model"]}),
        }
    return out


def paired(rows: list, a: str, b: str) -> Optional[dict]:
    """Per-task comparison (a task counts as solved if any attempt succeeded)."""
    solved = defaultdict(dict)
    for r in rows:
        if r["variant"] in (a, b):
            solved[r["task_id"]][r["variant"]] = solved[r["task_id"]].get(r["variant"], 0) or r["success"]
    both = [t for t, d in solved.items() if a in d and b in d]
    if not both:
        return None
    a_only = sum(1 for t in both if solved[t][a] and not solved[t][b])
    b_only = sum(1 for t in both if solved[t][b] and not solved[t][a])
    return {"a": a, "b": b, "tasks": len(both), "a_only": a_only, "b_only": b_only,
            "both": sum(1 for t in both if solved[t][a] and solved[t][b]),
            "neither": sum(1 for t in both if not solved[t][a] and not solved[t][b]),
            "p_exact": mcnemar_exact(a_only, b_only)}


def _section(rows: list, order_all: Optional[list] = None, baseline: str = "bump-pydantic") -> list[str]:
    summ = summarize(rows)
    order = sorted(summ, key=lambda v: (v not in ("noop", "bump-pydantic"), v))
    L = []
    L.append("| Variant | Verified | 95% CI | Regressions | Collection failed | Median cost | Median tokens | $/success | Median time | Median steps |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for v in order:
        s_ = summ[v]
        lo, hi = s_["ci95"]
        cps = f"${s_['cost_per_success']:.3f}" if s_["cost_per_success"] is not None else "-"
        L.append(f"| {v} | {s_['successes']}/{s_['attempts']} ({s_['success_rate']:.0%}) | {lo:.0%}-{hi:.0%} | "
                 f"{s_['attempts_with_regression']} attempts ({s_['regressions_total']} tests) | "
                 f"{s_['attempts_collection_failed']}/{s_['attempts']} | ${s_['cost_median']:.3f} | "
                 f"{s_['tokens_median']:,.0f} | {cps} | {s_['wall_median_s']:.0f}s | {s_['steps_median']:.0f} |")
    cats = ["environment", "incorrect_patch", "regression", "budget_exhausted", "policy_violation", "gave_up"]
    L.append("\n| Variant | " + " | ".join(cats) + " |")
    L.append("|---|" + "---|" * len(cats))
    for v in order:
        f = summ[v]["failures"]
        L.append(f"| {v} | " + " | ".join(str(f.get(c, 0)) for c in cats) + " |")
    pairs = [(a, b) for a in order for b in order if a < b and (b == baseline or a == baseline)] or \
            [(order[i], order[j]) for i in range(len(order)) for j in range(i + 1, len(order))]
    pl = []
    for a, b in pairs:
        p = paired(rows, a, b)
        if p:
            pl.append(f"| {a} | {b} | {p['tasks']} | {p['a_only']} | {p['b_only']} | {p['both']} | {p['neither']} | {p['p_exact']:.3f} |")
    if pl:
        L.append("\nPaired per-task comparison:\n\n| A | B | tasks | A only | B only | both | neither | exact p |\n|---|---|---|---|---|---|---|---|")
        L += pl
    return L


TRACK_NOTES = {
    "synthetic_fixture": "SMOKE-TEST FIXTURE: built to exercise the pipeline. Not a benchmark result; ignore for any claim.",
    "strict_native": "No v1 shims, no exceptions; deprecation warnings are errors. The headline track.",
    "compat_exception": "Strict native rules plus task-declared, machine-readable exceptions (same for every party). Reported separately.",
    "lenient": "Deprecated-but-working v2 APIs allowed (no `pydantic.v1` shim). A different, easier objective; never pool with strict.",
}


def build_report(db: DB, experiment: Optional[str] = None, split: Optional[str] = None,
                 tasks_dir: Path = TASKS_DIR, baseline: str = "bump-pydantic",
                 model: Optional[str] = None) -> tuple[str, dict]:
    rows = [dict(r) for r in db.runs(experiment, split)]
    if model:   # agent runs of that model only; baselines (no model) stay for comparison
        rows = [r for r in rows if not r["variant"].startswith("agent") or model in (r.get("model") or "")]
    if not rows:
        return "No runs found.\n", {}
    all_tasks = load_all(tasks_dir)
    task_track = {t.id: t.track for t in all_tasks}
    for r in rows:
        r["track"] = r.get("track") or task_track.get(r["task_id"]) or "unknown"
    # infrastructure errors (rate limits, no credit, crashes) say nothing about the agent: keep them out of the
    # success-rate denominators and list them separately
    env_rows = [r for r in rows if not r["success"] and r["failure_category"] == "environment"]
    rows = [r for r in rows if r not in env_rows]
    env_by = defaultdict(Counter)
    for r in env_rows:
        env_by[r["track"]][r["variant"]] += 1
    if not rows:
        return f"No scored runs ({len(env_rows)} infrastructure errors excluded).\n", {}
    summ = summarize(rows)
    in_runs = {r["task_id"] for r in rows}
    elig = [t for t in all_tasks if t.eligible and not t.synthetic and t.id in in_runs and (not split or t.split == split)]
    excl = [t for t in all_tasks if t.status == "excluded"]
    L = [f"# Results{f' - {experiment}' if experiment else ''}{f' ({split} split)' if split else ''}\n"]
    L.append(f"Eligible tasks in these runs: **{len(elig)}** | excluded during preparation: **{len(excl)}** "
             f"(of {len(all_tasks)} candidates)\n")
    real_strict = [t for t in elig if t.track == "strict_native"]
    if not real_strict:
        L.append("> **No real strict-native task yet**, so there is no headline track. Do not quote any number from this "
                 "report as a benchmark result.\n")
    prov = [t.id for t in elig if t.provisional]
    if prov:
        L.append(f"> **Provisional tasks (adaptations/exceptions not yet human-reviewed): {', '.join(prov)}.** "
                 "Treat results as development evidence, not benchmark claims.\n")
    by_track = defaultdict(list)
    for r in rows:
        by_track[r["track"]].append(r)
    for tr in sorted(by_track, key=lambda k: (k == "synthetic_fixture", k)):
        ids = sorted({r["task_id"] for r in by_track[tr]})
        L.append(f"## Track: {tr} ({len(ids)} task{'s' if len(ids) != 1 else ''}: {', '.join(ids)})\n")
        L.append(f"*{TRACK_NOTES.get(tr, '')}*\n")
        L += _section(by_track[tr], baseline=baseline)
        if env_by.get(tr):
            L.append("\nInfrastructure errors excluded from the rates above (not agent results): " +
                     ", ".join(f"{v}: {n}" for v, n in sorted(env_by[tr].items())))
        L.append("")
    L.append("*Cost = model API spend only (failed attempts included); compute excluded. Free/local models cost $0: use "
             "tokens. `configs/pricing.json` is a placeholder until verified. \"Collection failed\" means pytest aborted "
             "before running tests, so required tests were never executed (not observed assertion failures). "
             "With few tasks, report counts and traces, not just rates.*\n")
    if excl:
        L.append("## Exclusions\n")
        for reason, n in Counter((t.exclusion_reason or "?").split(":")[0][:80] for t in excl).most_common():
            L.append(f"- {n} x {reason}")
    warns = []
    if env_rows:
        warns.append(f"{len(env_rows)} infrastructure-error runs (category `environment`) were excluded from all rates")
    ag = [r for r in rows if r["variant"].startswith("agent")]
    if len({r["code_version"] for r in ag}) > 1:
        warns.append("multiple code versions contributed to these runs (agent code changed mid-experiment)")
    if len({m for s_ in summ.values() for m in s_["models"]}) > 1:
        warns.append("multiple models in one report")
    backs = {r.get("backend_used") or r.get("backend") for r in rows}
    if len(backs) > 1:
        warns.append(f"results mix execution backends ({', '.join(sorted(map(str, backs)))}); do not pool them")
    elif backs and "local" in backs:
        warns.append("all runs used the local backend (not a security boundary; Docker path unvalidated)")
    held = [r for r in rows if r["split"] == "heldout" and r["prompt_hash"]]
    for v in {r["variant"] for r in held}:
        if len({r["prompt_hash"] for r in held if r["variant"] == v}) > 1:
            warns.append(f"{v}: prompt changed between heldout runs - possible tuning on the test split")
    if warns:
        L.append("\n## Warnings\n" + "\n".join(f"- {w}" for w in warns))
    return "\n".join(L) + "\n", {"summary": summ, "warnings": warns}
