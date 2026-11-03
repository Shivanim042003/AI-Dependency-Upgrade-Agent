"""Per-run action breakdown from recorded traces (no model calls)."""
import sqlite3, json, sys, collections

EXPS = sys.argv[1:] or ["cmp2", "cmp3", "cmp4", "cmp5", "cmp6"]
con = sqlite3.connect("results/runs.sqlite")
q = ",".join("?" * len(EXPS))
runs = con.execute(
    f"select run_id, experiment, task_id, variant, success, failure_category, steps, agent_wall_s "
    f"from runs where experiment in ({q}) and coalesce(failure_category,'') != 'environment' "
    f"order by task_id, variant, experiment", EXPS).fetchall()

def key(d):
    i = d.get("input") or {}
    return (d.get("name"), json.dumps({k: v for k, v in i.items() if k not in ("new", "old", "content")}, sort_keys=True))

out = ["# Failure analysis from recorded tool calls", "",
       "| Task | Variant | Exp | Result | Steps | Wall s | Calls | read | search | edit | tests | first edit | repeated reads | repeated searches | tests w/o edit | errored calls |",
       "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
for rid, exp, task, var, ok, cat, steps, wall in runs:
    calls = []
    for (p,) in con.execute("select payload from events where run_id=? and type='tool' order by seq", (rid,)):
        try: calls.append(json.loads(p))
        except Exception: pass
    names = [c.get("name") for c in calls]
    cnt = collections.Counter(names)
    first_edit = next((i + 1 for i, n in enumerate(names) if n == "edit_file"), None)
    seen = collections.Counter(key(c) for c in calls if c.get("name") in ("read_file", "search"))
    rep_reads = sum(v - 1 for (n, _), v in seen.items() if n == "read_file" and v > 1)
    rep_search = sum(v - 1 for (n, _), v in seen.items() if n == "search" and v > 1)
    edited_since_test, no_edit_tests = True, 0
    for c in calls:
        if c.get("name") == "edit_file": edited_since_test = True
        elif c.get("name") == "run_tests":
            if not edited_since_test: no_edit_tests += 1
            edited_since_test = False
    errs = sum(1 for c in calls if c.get("error"))
    res = "PASS" if ok else (cat or "fail")
    out.append(f"| {task} | {var} | {exp} | {res} | {steps} | {int(wall or 0)} | {len(calls)} | {cnt['read_file']} | {cnt['search']} | {cnt['edit_file']} | {cnt['run_tests']} | {first_edit} | {rep_reads} | {rep_search} | {no_edit_tests} | {errs} |")
out += ["", "Exact-duplicate counts only (same tool and arguments). Near-duplicate searches are not counted, so these are lower bounds.",
        "Results are from recorded traces; they describe behavior, not causes."]
open("results/failure_analysis.md", "w").write("\n".join(out) + "\n")
print("\n".join(out))
