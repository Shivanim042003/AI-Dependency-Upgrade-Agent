import pytest

from pmigrate.policy import check_patch
from pmigrate.tasks import TaskSpec

DIFF = ("diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n"
        "+from pydantic.v1.utils import lenient_issubclass\n+from pydantic.v1 import BaseModel\n")


def mk(**kw):
    return TaskSpec(id="t", repo_url="x", base_sha="a", reference_sha="b", **kw)


def test_tracks():
    assert mk().track == "strict_native"
    assert mk(policy="lenient").track == "lenient"
    assert mk(policy_exceptions=[{"code": "PYDANTIC_V1_SHIM", "contains": "x", "rationale": "r"}]).track == "compat_exception"


def test_exception_waives_only_the_declared_import_for_everyone():
    t = mk(policy_exceptions=[{"code": "PYDANTIC_V1_SHIM", "contains": "pydantic.v1.utils import lenient_issubclass",
                               "rationale": "pure helper, no v1 models"}])
    remaining = check_patch(DIFF, t)
    assert [v.detail for v in remaining] == ["from pydantic.v1 import BaseModel"]     # the real shim is still caught
    assert len(check_patch(DIFF, mk())) == 2                                          # strict task: both flagged


def test_exception_requires_rationale():
    with pytest.raises(AssertionError):
        mk(policy_exceptions=[{"code": "PYDANTIC_V1_SHIM", "contains": "x", "rationale": ""}]).validate()


def test_synthetic_fixture_has_its_own_track_and_is_not_run_by_default(tmp_path):
    from pmigrate.db import DB
    from pmigrate.evalrun import run_experiment
    from pmigrate.tasks import save_task
    assert mk(synthetic=True).track == "synthetic_fixture"
    t = mk(synthetic=True, status="reproducible"); t.id = "toy"
    save_task(t, tmp_path)
    with pytest.raises(SystemExit):                       # only fixture present -> nothing eligible to run
        run_experiment("x", variants=["noop"], tasks_dir=tmp_path, db=DB(tmp_path / "r.sqlite"), dry_run=True)
    out = run_experiment("x", variants=["noop"], tasks_dir=tmp_path, db=DB(tmp_path / "r.sqlite"),
                         dry_run=True, include_synthetic=True)
    assert out["jobs"] == 1
