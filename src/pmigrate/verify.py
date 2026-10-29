"""Clean-room verification.

Patch generation and verification are deliberately separate: the verifier never reuses the
agent's workspace or venv. It rebuilds the task from scratch, applies *only the diff*, and
runs the tests in a fresh environment whose pins come from the task spec.

Success  =  every required test passes in the pinned v2 environment
            AND no policy violations
            AND (native_v2 tasks) every required test also passes with
                PydanticDeprecatedSince20 promoted to an error.
"""
from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .config import CACHE_DIR, TASKS_DIR
from .env import Env, TestRun
from .policy import DEP_FILES, _parse_diff, check_patch, violations_to_json
from .sandbox import make_sandbox
from .tasks import TaskSpec
from .workspace import WorkspaceError, apply_patch, prepare_workspace

FAILURE_CATEGORIES = ("environment", "incorrect_patch", "regression", "budget_exhausted",
                      "policy_violation", "gave_up")


@dataclass
class Baseline:
    """Test-id sets recorded when the task was prepared."""
    v1_passed: list = field(default_factory=list)          # base + pydantic v1
    v2_initial_passed: list = field(default_factory=list)  # base(+adaptation) + pydantic v2, before any fix
    reference_passed: list = field(default_factory=list)   # base + reference *source* diff + pydantic v2
    required: list = field(default_factory=list)           # v1_passed ∩ reference_passed

    def save(self, task_id: str, tasks_dir: Path = TASKS_DIR) -> Path:
        p = tasks_dir / f"{task_id}.baseline.json"
        p.write_text(json.dumps({k: sorted(v) for k, v in asdict(self).items()}, indent=1) + "\n")
        return p

    @staticmethod
    def load(task_id: str, tasks_dir: Path = TASKS_DIR) -> "Baseline":
        return Baseline(**json.loads((tasks_dir / f"{task_id}.baseline.json").read_text()))


@dataclass
class Verification:
    success: bool = False
    tests_pass: bool = False          # required tests all pass (normal run)
    native_ok: Optional[bool] = None  # required tests pass in strict-deprecation run (native_v2 only)
    policy_ok: bool = True
    violations: list = field(default_factory=list)
    required_total: int = 0
    required_failing: list = field(default_factory=list)   # = not_run + failed
    required_not_run: list = field(default_factory=list)   # never executed (collection/import failure, missing)
    required_failed: list = field(default_factory=list)    # executed and failed/errored
    collection_failed: bool = False
    regressions: list = field(default_factory=list)   # passed before the agent started, fail now
    env_error: Optional[str] = None
    empty_patch: bool = False
    patch_applies: bool = True
    duration_s: float = 0.0
    network_isolated: Optional[bool] = None
    test_counts: dict = field(default_factory=dict)
    touches_dep_files: bool = False

    def to_json(self) -> dict:
        d = asdict(self)
        for k in ("required_failing", "required_not_run", "required_failed"):
            d[k] = d[k][:50]
        d["n_not_run"], d["n_failed"] = len(self.required_not_run), len(self.required_failed)
        d["regressions"] = d["regressions"][:50]
        return d


def evaluate_run(task: TaskSpec, base: Baseline, normal: TestRun, strict: Optional[TestRun]) -> Verification:
    """Pure function over parsed test runs (unit-testable without any sandbox)."""
    req = set(base.required)
    v = Verification(required_total=len(req))
    failing = sorted(req - normal.passed)
    v.required_failing = failing
    v.required_not_run = sorted(req - set(normal.outcomes))
    v.required_failed = sorted((req & set(normal.outcomes)) - normal.passed)
    v.collection_failed = normal.collection_failed
    v.tests_pass = bool(req) and not failing and not normal.timed_out and normal.junit_found
    v.regressions = sorted((req & set(base.v2_initial_passed)) - normal.passed)
    v.test_counts = normal.counts()
    if task.policy == "native_v2":
        if strict is None:
            v.native_ok = False
        else:
            v.native_ok = bool(req) and not (req - strict.passed) and not strict.timed_out
    return v


def verify_patch(task: TaskSpec, patch: str, *, backend: str = "auto",
                 keep_dir: Optional[Path] = None, tasks_dir: Path = TASKS_DIR) -> Verification:
    t0 = time.time()
    base = Baseline.load(task.id, tasks_dir)
    v = Verification(required_total=len(base.required))

    if not patch.strip():
        v.empty_patch = True
        v.required_failing = v.required_not_run = sorted(base.required)
        v.duration_s = time.time() - t0
        return v

    vs = check_patch(patch, task)
    v.touches_dep_files = any(DEP_FILES.search(f['b']) for f in _parse_diff(patch))
    v.violations = violations_to_json(vs)
    v.policy_ok = not vs

    run_dir = Path(keep_dir) if keep_dir else Path(tempfile.mkdtemp(prefix="verify-", dir=_runs_dir()))
    sb = make_sandbox(run_dir, backend)
    try:
        if not v.policy_ok and any(x.code == "PROTECTED_PATH_EDIT" for x in vs):
            # Do not even run: the patch tampers with the evaluation surface.
            v.required_failing = v.required_not_run = sorted(base.required)
            return v
        prepare_workspace(task, sb.repo_dir)
        try:
            apply_patch(sb.repo_dir, patch)
        except WorkspaceError as e:
            v.patch_applies = False
            v.env_error = f"patch does not apply: {str(e)[:300]}"
            v.required_failing = v.required_not_run = sorted(base.required)
            return v
        env = Env(sb, task, "v2")
        r = env.setup()
        if not r.ok:
            v.env_error = f"env setup failed: {r.tail(800)}"
            v.required_failing = v.required_not_run = sorted(base.required)
            return v
        normal = env.run_tests(strict=False, junit_name="verify-normal.xml")
        strict = env.run_tests(strict=True, junit_name="verify-strict.xml") if task.policy == "native_v2" else None
        res = evaluate_run(task, base, normal, strict)
        res.violations, res.policy_ok = v.violations, v.policy_ok
        res.touches_dep_files = v.touches_dep_files
        res.network_isolated = normal.network_isolated
        v = res
        v.success = v.tests_pass and v.policy_ok and (v.native_ok is not False)
        return v
    finally:
        v.duration_s = time.time() - t0
        if not keep_dir:
            sb.cleanup()


def classify_failure(end_reason: str, v: Verification) -> Optional[str]:
    """Single category per failed attempt. Order encodes precedence."""
    if v.success:
        return None
    if not v.policy_ok:
        return "policy_violation"
    if end_reason.startswith("budget"):
        return "budget_exhausted"
    if v.empty_patch or end_reason == "gave_up":
        return "gave_up"
    if v.env_error and v.patch_applies:
        return "incorrect_patch" if v.touches_dep_files else "environment"
    if v.regressions:
        return "regression"
    return "incorrect_patch"


def _runs_dir() -> Path:
    d = CACHE_DIR / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d
