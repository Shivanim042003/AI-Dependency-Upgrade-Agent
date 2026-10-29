"""Turn a (repo, base_sha, reference_sha) triple into a verified benchmark task.

For each task we establish three states and record them:

  v1_pass         base_sha            + pydantic v1   -> green (run twice to catch flakiness)
  v2_fail         base_sha(+adapt.)   + pydantic v2   -> something required is failing
  reference_pass  base_sha(+adapt.) + the reference commit's NON-TEST changes + pydantic v2
                                                       -> green

The last state proves the task is solvable by editing source files only, which is exactly
what the agent is allowed to do. Tasks failing any state are marked `excluded` with a reason
so the final report can show an honest denominator.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from .env import Env, TestRun, is_protected
from .policy import check_patch
from .sandbox import make_sandbox
from .tasks import TaskSpec, save_task
from .verify import Baseline, _runs_dir
from .workspace import (WorkspaceError, apply_patch, git, prepare_workspace,
                        upstream_clone)

Log = Callable[[str], None]


def reference_source_diff(task: TaskSpec) -> tuple[str, str]:
    """(source_diff, test_diff) of base_sha..reference_sha, split by protected-path rules."""
    up = upstream_clone(task)
    names = git(up, "diff", "--name-only", task.base_sha, task.reference_sha).splitlines()
    src = [n for n in names if not is_protected(n)]
    tst = [n for n in names if is_protected(n)]
    sd = git(up, "diff", "--binary", task.base_sha, task.reference_sha, "--", *src) if src else ""
    td = git(up, "diff", "--binary", task.base_sha, task.reference_sha, "--", *tst) if tst else ""
    return sd, td


DEPRECATION_ASSERT = re.compile(r"^\+.*(deprecated_call|pytest\.warns\([^)]*Deprecat|filterwarnings)", re.M)


def strip_deprecation_assertions(task: TaskSpec, patch: str) -> str:
    """Rewrite a derived test adaptation so tests no longer *require* deprecation warnings.

    Human migrations are often partial: they leave deprecated calls in place and wrap the tests in
    `pytest.deprecated_call()`. Keeping those wrappers would force a native-v2 agent to *keep*
    deprecated usage (the test fails with 'DID NOT WARN'). We unwrap them so the tests assert
    behaviour only. Done on a scratch tree so the result is a valid, re-generated unified diff.
    """
    import shutil
    scratch = Path(tempfile.mkdtemp(prefix="adapt-", dir=_runs_dir()))
    try:
        repo = scratch / "repo"
        prepare_workspace(task.__class__(**{**asdict(task), "test_adaptation_patch": None}), repo, adapt_tests=False)
        apply_patch(repo, patch)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "adapted")
        for f in repo.rglob("*.py"):
            text = f.read_text(encoding="utf-8")
            if "deprecated_call" not in text:
                continue
            out, lines, i = [], text.split("\n"), 0
            while i < len(lines):
                m = re.match(r"^(\s*)with pytest\.deprecated_call\(\):\s*$", lines[i])
                if not m:
                    out.append(lines[i]); i += 1; continue
                base_indent, j, block = len(m.group(1)), i + 1, []
                while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > base_indent):
                    block.append(lines[j]); j += 1
                first = next((b for b in block if b.strip()), "")
                cut = (len(first) - len(first.lstrip())) - base_indent
                out += [b[cut:] if b.strip() else b for b in block]
                i = j
            f.write_text("\n".join(out), encoding="utf-8")
        git(repo, "add", "-A")
        # diff of (tests-after-stripping) vs the ORIGINAL base: first commit of the scratch repo is base
        first = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
        return git(repo, "diff", "--binary", first)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _setup(sb, task, version, log: Log) -> Optional[str]:
    env = Env(sb, task, version)
    r = env.setup()
    if not r.ok:
        log(f"  env setup failed ({version}): {r.tail(600)}")
        return r.tail(600)
    return None


def _run(sb, task, version, strict, name) -> TestRun:
    env = Env(sb, task, version)
    return env.run_tests(strict=strict, junit_name=name)


def prepare_task(task: TaskSpec, *, backend: str = "auto", derive_adaptation: bool = False,
                 strip_deprecation: bool = False,
                 log: Log = print, tasks_dir: Optional[Path] = None,
                 max_dropped_frac: float = 0.01) -> TaskSpec:
    def exclude(reason: str) -> TaskSpec:
        task.status, task.exclusion_reason = "excluded", reason
        log(f"[{task.id}] EXCLUDED: {reason}")
        save_task(task, **({"tasks_dir": tasks_dir} if tasks_dir else {}))
        return task

    log(f"[{task.id}] cloning upstream and checking SHAs")
    try:
        upstream_clone(task)
        src_diff, test_diff = reference_source_diff(task)
    except WorkspaceError as e:
        return exclude(f"git: {e}")
    if not src_diff.strip():
        return exclude("reference commit has no non-test changes")
    # The human reference must satisfy the same policy we hold the agent to; otherwise "solvable" is
    # not established under the rules the agent plays by.
    ref_violations = check_patch(src_diff, task)     # task.policy_exceptions already waived
    if ref_violations:
        desc = "; ".join(f"{v.code} in {v.file}: {v.detail}" for v in ref_violations[:3])
        return exclude(f"the human reference violates the migration policy ({desc}). Declare a "
                       "policy_exception (--exception CODE|contains|rationale) that applies to everyone, "
                       "or accept the exclusion.")
    if derive_adaptation and test_diff.strip():
        task.test_adaptation_patch = test_diff
        task.notes += " [adaptation derived from reference test diff - review manually]"
    if task.test_adaptation_patch and DEPRECATION_ASSERT.search(task.test_adaptation_patch):
        if strip_deprecation:
            task.test_adaptation_patch = strip_deprecation_assertions(task, task.test_adaptation_patch)
            task.notes += " [deprecated_call wrappers stripped from adaptation]"
            log(f"[{task.id}] stripped pytest.deprecated_call wrappers from the derived adaptation")
        elif task.policy == "native_v2":
            return exclude("test adaptation asserts deprecation warnings (the reference is only partly "
                           "migrated): a native-v2 solution would fail those tests. Re-run with "
                           "--strip-deprecation (and probably --policy lenient).")

    states: dict = {}
    base = Baseline()
    runs = _runs_dir()
    sbs = []

    def new_sb():
        sb = make_sandbox(Path(tempfile.mkdtemp(prefix="prep-", dir=runs)), backend)
        sbs.append(sb)
        return sb

    try:
        # ---- state 1: v1 green -----------------------------------------------------------
        log(f"[{task.id}] state 1/3: base + pydantic {task.pydantic_before}")
        sb = new_sb()
        prepare_workspace(task, sb.repo_dir, adapt_tests=False)
        err = _setup(sb, task, "v1", log)
        if err:
            return exclude(f"v1 environment could not be built: {err[-300:]}")
        r1a = _run(sb, task, "v1", False, "v1a.xml")
        r1b = _run(sb, task, "v1", False, "v1b.xml")
        states["v1_pass"] = {"green": r1a.green, "counts": r1a.counts(), "duration_s": round(r1a.duration_s, 1),
                             "stable": r1a.passed == r1b.passed, "network_isolated": r1a.network_isolated}
        if not r1a.green:
            return exclude(f"base is not green under pydantic v1 ({r1a.counts()}, junit={r1a.junit_found}): "
                           f"{r1a.stdout_tail[-300:]}")
        if r1a.passed != r1b.passed:
            return exclude("flaky: passing set differs across two v1 runs")
        base.v1_passed = sorted(r1a.passed)

        # ---- state 3 first (reference solvability) --------------------------------------
        log(f"[{task.id}] state 3/3: base + reference source diff + pydantic {task.pydantic_after}")
        sb = new_sb()
        prepare_workspace(task, sb.repo_dir)  # includes test adaptation, if any
        try:
            apply_patch(sb.repo_dir, src_diff)
        except WorkspaceError as e:
            return exclude(f"reference source diff does not apply onto base+adaptation: {e}")
        err = _setup(sb, task, "v2", log)
        if err:
            return exclude(f"v2 environment could not be built: {err[-300:]}")
        r3 = _run(sb, task, "v2", False, "ref-normal.xml")
        r3s = _run(sb, task, "v2", True, "ref-strict.xml") if task.policy == "native_v2" else None
        required = sorted(set(base.v1_passed) & r3.passed)
        base.reference_passed = sorted(r3.passed)
        base.required = required
        # Tests that passed on v1 but still fail with the reference's own source changes are not
        # satisfiable (renamed/removed/changed by the human commit). A tiny fraction is dropped from
        # `required` and listed for review; more than that means the task needs test adaptation.
        dropped = sorted(set(base.v1_passed) - r3.passed)
        ref_ok = bool(required) and len(dropped) <= max_dropped_frac * len(base.v1_passed)
        states["reference_pass"] = {"green_on_required": ref_ok, "counts": r3.counts(),
                                    "required": len(required), "duration_s": round(r3.duration_s, 1),
                                    "dropped_tests": dropped[:50], "dropped_count": len(dropped)}
        if not ref_ok:
            return exclude("not solvable by source-only changes (tests need adaptation?); "
                           f"{len(dropped)} of {len(base.v1_passed)} v1-passing tests still failing e.g. {dropped[:3]}")
        if dropped:
            task.notes += f" [{len(dropped)} v1-passing tests dropped from `required`: the reference itself fails them]"
        if r3s is not None and (set(required) - r3s.passed):
            return exclude(f"reference is not clean under native-v2 strictness "
                           f"({len(set(required) - r3s.passed)} tests use deprecated APIs); "
                           "re-run with policy=lenient to keep it as a lenient task")
        states["reference_pass"]["native_clean"] = r3s is not None

        # ---- state 2: something to fix ---------------------------------------------------
        log(f"[{task.id}] state 2/3: base + pydantic {task.pydantic_after} (must fail)")
        sb = new_sb()
        prepare_workspace(task, sb.repo_dir)
        err = _setup(sb, task, "v2", log)
        if err:
            return exclude(f"v2 environment could not be built on base: {err[-300:]}")
        r2 = _run(sb, task, "v2", False, "v2n.xml")
        r2s = _run(sb, task, "v2", True, "v2s.xml") if task.policy == "native_v2" else None
        base.v2_initial_passed = sorted(r2.passed)
        failing_normal = set(required) - r2.passed
        failing_strict = (set(required) - r2s.passed) if r2s is not None else set()
        states["v2_fail"] = {"failing_required_normal": len(failing_normal),
                             "failing_required_strict": len(failing_strict),
                             "counts": r2.counts(), "collection_error": not r2.junit_found}
        if not failing_normal and not failing_strict:
            return exclude("nothing to fix: all required tests already pass on pydantic v2 "
                           "(even under strict deprecation warnings)")

        # ---- accepted ---------------------------------------------------------------------
        task.status, task.exclusion_reason, task.states = "reproducible", None, states
        base.save(task.id, **({"tasks_dir": tasks_dir} if tasks_dir else {}))
        save_task(task, **({"tasks_dir": tasks_dir} if tasks_dir else {}))
        log(f"[{task.id}] REPRODUCIBLE: {len(required)} required tests, "
            f"{len(failing_normal)} failing (normal) / {len(failing_strict)} (strict) at start")
        return task
    finally:
        for sb in sbs:
            sb.cleanup()


# --------------------------------------------------------------------------------------
# Candidate discovery: find the commit where a repo moved from pydantic v1 to v2.
# --------------------------------------------------------------------------------------
DEP_PATHSPECS = ["*requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg", "poetry.lock", "uv.lock"]


def discover_upgrade_commits(repo_dir: Path, limit: int = 5) -> list[dict]:
    """Heuristic: commits that touch dependency files and mention pydantic in the diff, where
    the added lines allow v2 (>=2, ^2, ==2.) and the removed lines were v1-era constraints."""
    import re
    out = git(repo_dir, "log", "--format=%H|%ad|%s", "--date=short", "-G", "pydantic", "--", *DEP_PATHSPECS)
    cands = []
    for line in out.splitlines():
        sha, date, subj = line.split("|", 2)
        d = git(repo_dir, "show", "--format=", "-U0", sha, "--", *DEP_PATHSPECS)
        added = [l[1:] for l in d.splitlines() if l.startswith("+") and not l.startswith("+++")]
        removed = [l[1:] for l in d.splitlines() if l.startswith("-") and not l.startswith("---")]
        plus_v2 = any(re.search(r"pydantic.*(>=\s*2|\^\s*2|==\s*2|~=\s*2|<\s*3)", l, re.I) for l in added)
        minus_v1 = any(re.search(r"pydantic.*(<\s*2|\^\s*1|==\s*1|~=\s*1|>=\s*1|<\s*=?\s*1)", l, re.I) for l in removed)
        if plus_v2 and minus_v1:
            parent = git(repo_dir, "rev-parse", f"{sha}^").strip()
            n_src = len([n for n in git(repo_dir, "show", "--name-only", "--format=", sha).splitlines()
                         if n.endswith(".py") and not is_protected(n)])
            cands.append({"reference_sha": sha, "base_sha": parent, "date": date,
                          "subject": subj, "python_files_changed": n_src})
        if len(cands) >= limit:
            break
    return cands
