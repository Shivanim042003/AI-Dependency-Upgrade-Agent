import pytest

from pmigrate.report import mcnemar_exact, paired, pct, summarize, wilson


def row(task, variant, ok, cost=0.1, cat=None, reg=0):
    return {"task_id": task, "variant": variant, "success": int(ok), "cost_usd": cost, "agent_wall_s": 10,
            "steps": 5, "test_runs": 2, "regressions": reg, "failure_category": cat, "model": "m"}


def test_wilson_bounds():
    lo, hi = wilson(0, 10)
    assert lo == 0 and 0.2 < hi < 0.35
    lo, hi = wilson(10, 10)
    assert hi == 1 and lo > 0.65
    assert wilson(0, 0) == (0.0, 0.0)


def test_mcnemar_exact():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 0) == pytest.approx(0.0625)
    assert mcnemar_exact(3, 3) == 1.0


def test_summarize_cost_per_success_includes_failed_attempts():
    rows = [row("a", "agent", True, 0.2), row("b", "agent", False, 0.4, "incorrect_patch"),
            row("c", "agent", False, 0.4, "regression", reg=3)]
    s = summarize(rows)["agent"]
    assert s["successes"] == 1 and s["attempts"] == 3
    assert s["cost_per_success"] == pytest.approx(1.0)          # total spend / successes
    assert s["failures"] == {"incorrect_patch": 1, "regression": 1}
    assert s["attempts_with_regression"] == 1 and s["regressions_total"] == 3


def test_paired_counts_discordant_tasks():
    rows = [row("a", "x", True), row("a", "y", False), row("b", "x", True), row("b", "y", True),
            row("c", "x", False), row("c", "y", True), row("d", "x", False), row("d", "y", False)]
    p = paired(rows, "x", "y")
    assert (p["a_only"], p["b_only"], p["both"], p["neither"]) == (1, 1, 1, 1)


def test_percentile():
    assert pct([1, 2, 3, 4], 0.5) == 2.5
