"""Python environments and test execution inside a Sandbox."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import CACHE_DIR, PROTECTED_GLOBS
from .sandbox import RunResult, Sandbox, q
from .tasks import TaskSpec

STRICT_FLAG = "-W error::pydantic.warnings.PydanticDeprecatedSince20"


@dataclass
class TestRun:
    __test__ = False  # not a pytest test class
    """Parsed result of one pytest invocation."""
    outcomes: dict = field(default_factory=dict)   # nodeid -> passed|failed|error|skipped
    messages: dict = field(default_factory=dict)   # nodeid -> short failure message
    returncode: int = 0
    timed_out: bool = False
    duration_s: float = 0.0
    stdout_tail: str = ""
    junit_found: bool = True
    strict: bool = False
    network_isolated: Optional[bool] = None
    collection_failed: bool = False   # pytest aborted before running tests (import/collection errors)

    @property
    def passed(self) -> set:
        return {k for k, v in self.outcomes.items() if v == "passed"}

    @property
    def failing(self) -> set:
        return {k for k, v in self.outcomes.items() if v in ("failed", "error")}

    @property
    def green(self) -> bool:
        return self.junit_found and not self.timed_out and not self.failing and bool(self.passed)

    def counts(self) -> dict:
        c = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
        for v in self.outcomes.values():
            c[v] = c.get(v, 0) + 1
        return c


def parse_junit(path: Path) -> tuple[dict, dict]:
    outcomes, messages = {}, {}
    root = ET.parse(path).getroot()
    for tc in root.iter("testcase"):
        nid = f"{tc.get('classname', '')}::{tc.get('name', '')}"
        status = "passed"
        msg = ""
        for child in tc:
            if child.tag == "failure":
                status, msg = "failed", (child.get("message") or child.text or "")
            elif child.tag == "error":
                status, msg = "error", (child.get("message") or child.text or "")
            elif child.tag == "skipped":
                status, msg = "skipped", (child.get("message") or "")
        # a nodeid can appear twice (setup error + call); worst outcome wins
        order = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
        if nid in outcomes and order[outcomes[nid]] >= order[status]:
            continue
        outcomes[nid] = status
        if msg:
            messages[nid] = msg.strip().replace("\n", " ")[:500]
    return outcomes, messages


def is_protected(rel: str) -> bool:
    from fnmatch import fnmatch
    while rel.startswith("./"):       # NOT lstrip("./"): that would also eat the dot of ".github"
        rel = rel[2:]
    rel = rel.lstrip("/")
    parts = rel.split("/")
    for g in PROTECTED_GLOBS:
        if fnmatch(rel, g):
            return True
        # '**/x/**' style globs: fnmatch '*' already crosses '/', but handle leading dirs
        if g.startswith("**/") and fnmatch(rel, g[3:]):
            return True
    return any(p in (".git",) for p in parts)


class Env:
    """A venv inside the sandbox with the task's pinned dependencies."""

    def __init__(self, sb: Sandbox, task: TaskSpec, version: str):
        assert version in ("v1", "v2")
        self.sb, self.task, self.version = sb, task, version
        self.setup_result: Optional[RunResult] = None

    @property
    def pydantic_pin(self) -> str:
        return self.task.pydantic_before if self.version == "v1" else self.task.pydantic_after

    @property
    def extra_deps(self) -> list[str]:
        return self.task.extra_deps_before if self.version == "v1" else self.task.extra_deps_after

    def setup(self, timeout: int = 900) -> RunResult:
        """Create the venv and install deps. This is the only phase with network access."""
        t = self.task
        # `--override` replaces whatever the project itself declares for pydantic, so a repo
        # pinned to `pydantic<2` still resolves against the v2 environment (and an agent that
        # edits the pin cannot change the verification environment).
        overrides = self.sb.run_dir / "overrides.txt"
        overrides.write_text(f"pydantic=={self.pydantic_pin}\n")
        py = shlex.quote(t.python_version)
        pkgs = [f"pydantic=={self.pydantic_pin}", *self.extra_deps, *t.test_deps]
        newer = f"--exclude-newer {q(t.exclude_newer)} " if t.exclude_newer else ""
        cmd = (
            f"uv venv --python {py} --seed {q(self.sb.work_root + '/venv')} >/dev/null && "
            f"uv pip install --python {q(self.sb.work_root + '/venv/bin/python')} "
            f"--override {q(self.sb.work_root + '/overrides.txt')} {newer}"
            f"{' '.join(q(p) for p in pkgs)} {t.install_cmd}"
        )
        self.setup_result = self.sb.exec(cmd, cwd="repo", timeout=timeout, network=True)
        return self.setup_result

    @property
    def editable(self) -> bool:
        return self.task.install_cmd.split()[0] == "-e" if self.task.install_cmd.strip() else True

    def run_tests(self, *, strict: bool = False, timeout: Optional[int] = None,
                  junit_name: str = "junit.xml", extra_args: str = "") -> TestRun:
        t = self.task
        timeout = timeout or t.test_timeout_s
        junit_rel = f"{self.sb.work_root}/{junit_name}"
        (self.sb.run_dir / junit_name).unlink(missing_ok=True)
        prefix = ""
        if not self.editable:
            prefix = (f"uv pip install --python {q(self.sb.work_root + '/venv/bin/python')} "
                      f"--override {q(self.sb.work_root + '/overrides.txt')} "
                      f"--no-deps --reinstall {t.install_cmd} >/dev/null 2>&1; ")
        desel = " ".join(f"--deselect {q(d)}" for d in t.deselect)
        cmd = (f"{prefix}{t.test_cmd} {desel} {STRICT_FLAG if strict else ''} {extra_args} "
               f"--rootdir={q(self.sb.work_root + '/repo')} "
               f"-p no:cacheprovider --junitxml={q(junit_rel)} -o junit_family=xunit2")
        r = self.sb.exec(cmd, cwd="repo", timeout=timeout, network=False)
        run = TestRun(returncode=r.returncode, timed_out=r.timed_out, duration_s=r.duration_s,
                      stdout_tail=r.tail(3500), strict=strict, network_isolated=r.isolated_network)
        run.collection_failed = bool(re.search(r"(Interrupted: \d+ errors? during collection|errors? during collection)", r.stdout + r.stderr))
        jp = self.sb.run_dir / junit_name
        if jp.exists():
            try:
                run.outcomes, run.messages = parse_junit(jp)
            except ET.ParseError:
                run.junit_found = False
        else:
            run.junit_found = False
        return run
