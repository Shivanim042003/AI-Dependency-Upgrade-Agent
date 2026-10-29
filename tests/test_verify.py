from pmigrate.env import TestRun
from pmigrate.verify import Baseline, Verification, classify_failure, evaluate_run

BASE = Baseline(v1_passed=list("abcd"), v2_initial_passed=["a", "b"], reference_passed=list("abcd"),
                required=list("abcd"))


def tr(passed, failed=()):
    out = {p: "passed" for p in passed}
    out.update({f: "failed" for f in failed})
    return TestRun(outcomes=out)


def test_all_required_pass_is_success_candidate(task):
    v = evaluate_run(task, BASE, tr("abcd"), tr("abcd"))
    assert v.tests_pass and v.native_ok and not v.regressions


def test_regression_means_previously_passing_now_failing(task):
    v = evaluate_run(task, BASE, tr("acd", "b"), tr("acd", "b"))
    assert not v.tests_pass and v.regressions == ["b"] and v.required_failing == ["b"]


def test_old_failures_are_not_regressions(task):
    v = evaluate_run(task, BASE, tr("abc", "d"), tr("abc", "d"))
    assert v.required_failing == ["d"] and v.regressions == []


def test_missing_tests_count_as_failing(task):
    v = evaluate_run(task, BASE, tr("ab"), tr("ab"))          # c, d vanished (e.g. collection error)
    assert set(v.required_failing) == {"c", "d"}


def test_native_requires_strict_pass(task):
    v = evaluate_run(task, BASE, tr("abcd"), tr("abc", "d"))   # passes normally, fails under deprecation=error
    assert v.tests_pass and v.native_ok is False


def test_lenient_ignores_strict(task):
    task.policy = "lenient"
    v = evaluate_run(task, BASE, tr("abcd"), None)
    assert v.native_ok is None


def test_classification_precedence():
    ok = Verification(success=True)
    assert classify_failure("finished", ok) is None
    assert classify_failure("finished", Verification(policy_ok=False)) == "policy_violation"
    assert classify_failure("budget_steps", Verification()) == "budget_exhausted"
    assert classify_failure("gave_up", Verification()) == "gave_up"
    assert classify_failure("finished", Verification(empty_patch=True)) == "gave_up"
    assert classify_failure("finished", Verification(env_error="x")) == "environment"
    assert classify_failure("finished", Verification(env_error="x", touches_dep_files=True)) == "incorrect_patch"
    assert classify_failure("finished", Verification(regressions=["a"])) == "regression"
    assert classify_failure("finished", Verification()) == "incorrect_patch"
    # a policy violation outranks running out of budget
    assert classify_failure("budget_cost", Verification(policy_ok=False)) == "policy_violation"
