"""End-to-end on the synthetic fixture. Builds real venvs (needs uv + PyPI access, ~1 min)."""
import shutil
import sys

import pytest

from pmigrate.db import DB
from pmigrate.llm import ScriptedClient, tool_call
from pmigrate.prepare import prepare_task, reference_source_diff
from pmigrate.runner import run_attempt
from pmigrate.tasks import TaskSpec, load_task
from pmigrate.toy import MODELS_V2, build, scripted_solution
from pmigrate.verify import verify_patch

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not shutil.which("uv"), reason="needs uv")]


@pytest.fixture(scope="module")
def toy(tmp_path_factory):
    root = tmp_path_factory.mktemp("toy")
    info = build(root / "repo")
    tasks_dir = root / "tasks"
    tasks_dir.mkdir()
    t = TaskSpec(id="toy-it", repo_url=info["path"], base_sha=info["base_sha"], reference_sha=info["reference_sha"],
                 extra_deps_after=["pydantic-settings==2.6.1"], test_cmd="pytest -q")
    t = prepare_task(t, backend="local", tasks_dir=tasks_dir, log=lambda *_: None)
    assert t.status == "reproducible", t.exclusion_reason
    return t, tasks_dir


def test_three_states_recorded(toy):
    t, _ = toy
    assert t.states["v1_pass"]["green"] and t.states["v1_pass"]["stable"]
    assert t.states["reference_pass"]["green_on_required"] and t.states["reference_pass"]["native_clean"]
    assert t.states["v2_fail"]["failing_required_strict"] == 12


def test_reference_patch_verifies(toy):
    t, d = toy
    src, _ = reference_source_diff(t)
    v = verify_patch(t, src, backend="local", tasks_dir=d)
    assert v.success and v.native_ok and not v.violations


def test_empty_patch_fails(toy):
    t, d = toy
    assert not verify_patch(t, "", backend="local", tasks_dir=d).success


def test_v1_shim_is_caught(toy):
    t, d = toy
    src, _ = reference_source_diff(t)
    cheat = src.replace("from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator",
                        "from pydantic.v1 import BaseModel, Field")
    v = verify_patch(t, cheat, backend="local", tasks_dir=d)
    assert not v.success and any(x["code"] == "PYDANTIC_V1_SHIM" for x in v.violations)


def test_deprecated_apis_fail_native_but_pass_lenient(toy):
    """v2 installed, v1-style calls still 'work' -> only strict verification notices."""
    t, d = toy
    src, _ = reference_source_diff(t)
    lazy = src.replace("User.model_validate(payload)", "User.parse_obj(payload)", 1)
    assert lazy != src
    v = verify_patch(t, lazy, backend="local", tasks_dir=d)
    assert v.tests_pass and v.native_ok is False and not v.success


def test_full_attempt_scripted_agent_and_db(toy, tmp_path):
    t, d = toy
    db = DB(tmp_path / "r.sqlite")
    rec = run_attempt(t, "agent", experiment="it", backend="local", db=db, tasks_dir=d,
                      llm=ScriptedClient(scripted_solution()))
    assert rec.success and rec.agent.test_runs == 2
    row = dict(db.runs("it")[0])
    assert row["success"] == 1 and row["steps"] == 8 and row["input_tokens"] > 0 and row["failure_category"] is None
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM events WHERE run_id=?", (rec.run_id,)).fetchone()["n"] > 5
    assert db.load_transcript(rec.run_id)


def test_cheating_agent_is_blocked(toy, tmp_path):
    t, d = toy
    rec = run_attempt(t, "agent", backend="local", tasks_dir=d, llm=ScriptedClient([
        tool_call("write_file", path="tests/test_service.py", content="def test_x(): pass\n"),
        tool_call("write_file", path="src/toyapp/models.py", content="from pydantic.v1 import BaseModel\n"),
        tool_call("finish", status="done", summary="done")]))
    assert not rec.success and rec.failure_category == "policy_violation"
    assert rec.agent.denied_edits == ["tests/test_service.py"]


def test_baselines_run(toy):
    t, d = toy
    assert run_attempt(t, "noop", backend="local", tasks_dir=d).failure_category == "gave_up"
    b = run_attempt(t, "bump-pydantic", backend="local", tasks_dir=d)
    assert b.diff and not b.success        # codemod can't fix Optional-default semantics / settings move
