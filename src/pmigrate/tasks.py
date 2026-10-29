"""Task specs: one pinned, reproducible historical migration per task.

Three states are recorded for every task before it may enter the benchmark:

  1. v1_pass        base_sha + pydantic v1 pinned        -> tests pass
  2. v2_fail        base_sha + pydantic v2 pinned        -> tests fail (there is something to fix)
  3. reference_pass reference_sha + pydantic v2 pinned   -> tests pass (the task is solvable)

Only tasks with status == "reproducible" are eligible. Everything else is reported as an
exclusion so the evaluation denominator stays honest.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .config import TASKS_DIR

STATUSES = ("candidate", "reproducible", "excluded")
POLICIES = ("native_v2", "lenient")


@dataclass
class TaskSpec:
    id: str
    repo_url: str
    base_sha: str                      # last commit that is v1-only and green
    reference_sha: str                 # the human upgrade commit (never given to the agent)
    python_version: str = "3.11"
    pydantic_before: str = "1.10.13"   # exact pin for the v1 environment
    pydantic_after: str = "2.10.0"     # exact pin for the v2 environment
    extra_deps_before: list[str] = field(default_factory=list)
    extra_deps_after: list[str] = field(default_factory=list)   # e.g. pydantic-settings
    exclude_newer: str = ""             # uv --exclude-newer cutoff (date): resolve deps as of the base commit
    install_cmd: str = "-e ."          # args passed to `uv pip install`
    test_deps: list[str] = field(default_factory=lambda: ["pytest"])
    test_cmd: str = "pytest -x -q"     # junit + warning flags are appended by the harness
    test_timeout_s: int = 300
    # Evaluator-owned exclusions for tests that are environment artefacts (e.g. a test asserting a
    # license header on a build-generated file). Passed as --deselect by the harness; the agent can't touch them.
    deselect: list[str] = field(default_factory=list)
    policy: str = "native_v2"          # native_v2 | lenient
    # Machine-readable policy exceptions. They apply to EVERYONE (agent, baselines, human reference):
    # a violation is waived only if its code matches and its detail contains `contains`.
    # e.g. {"code": "PYDANTIC_V1_SHIM", "contains": "pydantic.v1.utils import lenient_issubclass", "rationale": "..."}
    policy_exceptions: list[dict] = field(default_factory=list)
    provisional: bool = False          # adaptations/exceptions not yet human-reviewed
    synthetic: bool = False            # smoke-test fixture: never part of any reported result
    split: str = "dev"                 # dev | heldout
    status: str = "candidate"
    states: dict = field(default_factory=dict)       # v1_pass / v2_fail / reference_pass details
    exclusion_reason: Optional[str] = None
    # Evaluator-owned test adaptations (unified diff, tests only). Applied before the agent
    # starts and re-applied in clean verification. The agent can never edit tests itself.
    test_adaptation_patch: Optional[str] = None
    notes: str = ""

    def validate(self) -> None:
        assert self.status in STATUSES, f"bad status {self.status}"
        assert self.policy in POLICIES, f"bad policy {self.policy}"
        assert self.split in ("dev", "heldout")
        assert self.pydantic_before.startswith("1."), "pydantic_before must be a v1 pin"
        assert self.pydantic_after.startswith("2."), "pydantic_after must be a v2 pin"
        for e in self.policy_exceptions:
            assert {"code", "contains", "rationale"} <= set(e) and e["contains"] and e["rationale"], \
                "policy_exceptions need code, contains and rationale"

    @property
    def track(self) -> str:
        """strict_native | compat_exception | lenient. Report tracks separately; never pool them."""
        if self.synthetic:
            return "synthetic_fixture"
        if self.policy == "lenient":
            return "lenient"
        return "compat_exception" if self.policy_exceptions else "strict_native"

    @property
    def eligible(self) -> bool:
        return self.status == "reproducible"

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def task_path(task_id: str, tasks_dir: Path = TASKS_DIR) -> Path:
    return tasks_dir / f"{task_id}.json"


def save_task(t: TaskSpec, tasks_dir: Path = TASKS_DIR) -> Path:
    t.validate()
    tasks_dir.mkdir(parents=True, exist_ok=True)
    p = task_path(t.id, tasks_dir)
    p.write_text(t.to_json() + "\n")
    return p


def load_task(task_id: str, tasks_dir: Path = TASKS_DIR) -> TaskSpec:
    data = json.loads(task_path(task_id, tasks_dir).read_text())
    t = TaskSpec(**data)
    t.validate()
    return t


def load_all(tasks_dir: Path = TASKS_DIR, *, eligible_only: bool = False,
             split: Optional[str] = None) -> list[TaskSpec]:
    out = []
    for p in sorted(tasks_dir.glob("*.json")):
        if p.name == "demo.json" or p.name.endswith(".baseline.json"):
            continue
        t = TaskSpec(**json.loads(p.read_text()))
        t.validate()
        if eligible_only and not t.eligible:
            continue
        if split and t.split != split:
            continue
        out.append(t)
    return out
