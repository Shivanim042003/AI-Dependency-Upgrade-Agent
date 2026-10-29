"""Agent loop, budgets, revert guard and tool safety - with a fake Env so no venvs are built."""
import pytest

from pmigrate.agent import AgentResult, build_system, run_agent
from pmigrate.config import AgentConfig, Budget
from pmigrate.env import TestRun
from pmigrate.llm import ScriptedClient, Usage, text_reply, tool_call
from pmigrate.tools import ToolBox, tool_defs
from pmigrate.verify import Baseline
from pmigrate.workspace import Workspace, init_fresh_repo

REQ = ["t1", "t2", "t3"]


class FakeEnv:
    """Tests 'pass' according to which marker strings exist in src/m.py."""
    def __init__(self, ws, task):
        self.ws, self.task = ws, task

    def run_tests(self, *, strict=False, timeout=None, junit_name="", extra_args=""):
        src = (self.ws.root / "src" / "m.py").read_text()
        passed = {t for t in REQ if f"# fix-{t}" in src}
        if "# breaks-everything" in src:
            passed = set()
        outcomes = {t: ("passed" if t in passed else "failed") for t in REQ}
        return TestRun(outcomes=outcomes, messages={t: "AssertionError" for t in REQ if t not in passed},
                       stdout_tail="pytest tail", strict=strict)


@pytest.fixture
def box(tmp_path, task):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "m.py").write_text("x = 1\n")
    (repo / "tests" / "test_m.py").write_text("pass\n")
    ws = Workspace(repo, init_fresh_repo(repo))
    base = Baseline(v1_passed=REQ, required=REQ, reference_passed=REQ, v2_initial_passed=[])

    def make(cfg=None):
        cfg = cfg or AgentConfig()
        return ToolBox(ws=ws, env=FakeEnv(ws, task), base=base, cfg=cfg), cfg
    return make, ws


def edit(marker):
    return tool_call("edit_file", path="src/m.py", old="x = 1", new=f"x = 1  # {marker}")


def test_happy_path_finishes_and_produces_diff(box, task):
    make, ws = box
    tb, cfg = make()
    llm = ScriptedClient([tool_call("run_tests"), tool_call("write_file", path="src/m.py", content="# fix-t1 # fix-t2 # fix-t3\n"),
                          tool_call("run_tests"), tool_call("finish", status="done", summary="ok")])
    r = run_agent(task, tb, cfg, llm)
    assert r.end_reason == "finished" and r.steps == 4 and r.test_runs == 2
    assert "fix-t1" in r.diff and r.cost_usd > 0 and r.summary == "ok"


def test_step_budget_ends_attempt(box, task):
    make, ws = box
    cfg = AgentConfig(budget=Budget(max_steps=3))
    tb, cfg = make(cfg)
    r = run_agent(task, tb, cfg, ScriptedClient([tool_call("list_files")] * 10))
    assert r.end_reason == "budget_steps" and r.steps == 3


def test_cost_budget_ends_attempt(box, task):
    make, ws = box
    cfg = AgentConfig(budget=Budget(max_cost_usd=0.01))
    tb, cfg = make(cfg)
    big = Usage(input_tokens=1_000_000, output_tokens=0)
    r = run_agent(task, tb, cfg, ScriptedClient([tool_call("list_files", usage=big)] * 5))
    assert r.end_reason == "budget_cost" and r.steps == 1


def test_test_run_budget(box, task):
    make, ws = box
    cfg = AgentConfig(budget=Budget(max_test_runs=2))
    tb, cfg = make(cfg)
    script = []
    for i in range(6):                      # an edit between runs, otherwise the no-change guard answers
        script += [tool_call("write_file", path=f"scratch{i}.py", content=f"x = {i}\n"), tool_call("run_tests")]
    r = run_agent(task, tb, cfg, ScriptedClient(script))
    assert r.end_reason == "budget_tests" and r.test_runs == 2


def test_gave_up(box, task):
    make, ws = box
    tb, cfg = make()
    r = run_agent(task, tb, cfg, ScriptedClient([tool_call("finish", status="giving_up", summary="stuck")]))
    assert r.end_reason == "gave_up" and r.diff == ""


def test_protected_edit_is_denied_not_applied(box, task):
    make, ws = box
    tb, cfg = make()
    r = run_agent(task, tb, cfg, ScriptedClient([
        tool_call("write_file", path="tests/test_m.py", content="assert False"),
        tool_call("finish", status="done", summary="x")]))
    assert r.denied_edits == ["tests/test_m.py"] and r.diff == ""


def test_revert_guard_restores_best_state(box, task):
    make, ws = box
    cfg = AgentConfig(revert_on_worse=True)
    tb, cfg = make(cfg)
    r = run_agent(task, tb, cfg, ScriptedClient([
        tool_call("run_tests"),                       # 3 failing (baseline)
        edit("fix-t1"), tool_call("run_tests"),       # 2 failing -> new best
        edit("breaks-everything"), tool_call("run_tests"),   # 3 failing -> worse -> revert
        tool_call("finish", status="done", summary="x")]))
    assert r.reverts == 1
    assert "fix-t1" in r.diff and "breaks-everything" not in r.diff


def test_without_revert_toggle_worse_state_stays(box, task):
    make, ws = box
    tb, cfg = make(AgentConfig(revert_on_worse=False))
    r = run_agent(task, tb, cfg, ScriptedClient([
        tool_call("run_tests"), edit("fix-t1"), tool_call("run_tests"),
        edit("breaks-everything"), tool_call("run_tests"), tool_call("finish", status="done", summary="x")]))
    assert r.reverts == 0 and "breaks-everything" in r.diff


def test_final_state_restored_if_last_run_was_worse(box, task):
    make, ws = box
    tb, cfg = make(AgentConfig(revert_on_worse=True, budget=Budget(max_steps=5)))
    r = run_agent(task, tb, cfg, ScriptedClient([
        tool_call("run_tests"), edit("fix-t1"), tool_call("run_tests"),
        edit("breaks-everything"), tool_call("run_tests")]))     # budget ends here; run_tests already reverted
    assert "breaks-everything" not in r.diff


def test_llm_exception_is_recorded_not_raised(box, task):
    make, ws = box
    tb, cfg = make()
    r = run_agent(task, tb, cfg, ScriptedClient([]))              # exhausted -> RuntimeError inside loop
    assert r.end_reason == "error" and "exhausted" in r.error


def test_planning_adds_one_toolless_step_and_scan(box, task):
    make, ws = box
    (ws.root / "src" / "m.py").write_text("from pydantic import validator\nx = 1\n")
    tb, cfg = make(AgentConfig(use_planning=True))
    llm = ScriptedClient([text_reply("1) fix validators"), tool_call("finish", status="giving_up", summary="s")])
    r = run_agent(task, tb, cfg, llm)
    assert r.plan.startswith("1)") and llm.calls[0]["tools"] == [] and "run_tests" in llm.calls[1]["tools"]
    assert r.steps == 2


def test_docs_tool_only_exposed_when_enabled():
    assert "lookup_migration_rule" not in [t["name"] for t in tool_defs(False)]
    assert "lookup_migration_rule" in [t["name"] for t in tool_defs(True)]


def test_selector_cannot_inject_options(box, task):
    make, ws = box
    tb, _ = make()
    out, err = tb.call("run_tests", {"selector": ["--co", "-p", "evil"]})
    assert "not options" in out


def test_system_prompt_reflects_policy(task):
    assert "deprecation warnings treated as errors" in build_system(task, AgentConfig())
    task.policy = "lenient"
    assert "deprecation warnings treated as errors" not in build_system(task, AgentConfig())
