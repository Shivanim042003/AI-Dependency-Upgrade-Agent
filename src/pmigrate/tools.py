"""The agent's tools. Deliberately small: no arbitrary shell, only file ops + run_tests.

All tools return (text, is_error). Test progress is tracked here so the optional
"revert if tests get worse" guard can snapshot/restore the workspace.
"""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import AgentConfig
from .docs import MigrationDocs, format_hits
from .env import Env, TestRun
from .verify import Baseline
from .workspace import Workspace, WorkspaceError

MAX_TOOL_CHARS = 7000


def tool_defs(use_docs: bool) -> list:
    tools = [
        {"name": "list_files", "description": "List files under a directory (repo-relative).",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string", "default": "."}}}},
        {"name": "read_file",
         "description": "Read a file with line numbers. Use start_line/end_line for large files.",
         "input_schema": {"type": "object", "required": ["path"], "properties": {
             "path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}}},
        {"name": "search",
         "description": "Regex search across files (like ripgrep). Use it to find ALL usages of an API before editing.",
         "input_schema": {"type": "object", "required": ["pattern"], "properties": {
             "pattern": {"type": "string"}, "path": {"type": "string", "default": "."},
             "glob": {"type": "string", "description": "filename glob, e.g. *.py"},
             "context": {"type": "integer", "description": "lines of context around matches"}}}},
        {"name": "edit_file",
         "description": "Replace one exact, unique occurrence of `old` with `new` in a file. Whitespace must match.",
         "input_schema": {"type": "object", "required": ["path", "old", "new"], "properties": {
             "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}}},
        {"name": "write_file", "description": "Create or overwrite a file with full content.",
         "input_schema": {"type": "object", "required": ["path", "content"], "properties": {
             "path": {"type": "string"}, "content": {"type": "string"}}}},
        {"name": "run_tests",
         "description": "Run the test suite in the pinned Pydantic v2 environment. Optional `selector` limits to "
                        "specific paths/node ids (partial runs do not count as progress). Limited number of runs.",
         "input_schema": {"type": "object", "properties": {"selector": {"description": "list of paths/node ids; omit to run all tests"}}}},
    ]
    if use_docs:
        tools.append({
            "name": "lookup_migration_rule",
            "description": "Search the official Pydantic v2 migration guide and changelog (pinned to the target "
                           "version) for how a v1 API/behaviour maps to v2. Query with API names or error text.",
            "input_schema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}})
    tools.append({
        "name": "finish",
        "description": "End the task. status='done' when you believe all tests pass; 'giving_up' if you cannot proceed.",
        "input_schema": {"type": "object", "required": ["status", "summary"], "properties": {
            "status": {"type": "string", "enum": ["done", "giving_up"]},
            "summary": {"type": "string", "description": "What changed and why (shown to the reviewer)."}}}})
    return tools


def _clip(s: str, n: int = MAX_TOOL_CHARS) -> str:
    return s if len(s) <= n else s[: n // 2] + "\n...[output truncated]...\n" + s[-n // 2:]


def format_test_result(run: TestRun, required: set, strict: bool, partial: bool) -> str:
    c = run.counts()
    head = f"TESTS: {c['passed']} passed, {c['failed']} failed, {c['error']} errors, {c['skipped']} skipped"
    if run.timed_out:
        head += " (TIMED OUT)"
    if not partial:
        head += f" | required tests passing: {len(required & run.passed)}/{len(required)}"
    if strict:
        head += "\n(strict mode: Pydantic deprecation warnings are errors, so v1-style APIs fail even if they still work)"
    lines = [head]
    fails = sorted(run.failing)
    if fails:
        lines.append(f"\nFailing ({len(fails)}):")
        for nid in fails[:15]:
            lines.append(f"- {nid}: {run.messages.get(nid, '')[:300]}")
        if len(fails) > 15:
            lines.append(f"... {len(fails) - 15} more")
    if not run.junit_found or fails or run.timed_out:
        lines.append("\n--- pytest output (tail) ---\n" + run.stdout_tail[-3000:])
    return _clip("\n".join(lines))


@dataclass
class ToolBox:
    ws: Workspace
    env: Env
    base: Baseline
    cfg: AgentConfig
    docs: Optional[MigrationDocs] = None
    emit: Callable = lambda *a, **k: None

    finished: Optional[tuple] = None
    test_runs: int = 0
    best_fail: Optional[int] = None
    last_fail: Optional[int] = None
    reverts: int = 0
    retrieved: list = field(default_factory=list)
    budget_hit: Optional[str] = None

    def __post_init__(self):
        self.required = set(self.base.required)
        self.strict = self.env.task.policy == "native_v2"

    # ---------------------------------------------------------------------------------
    def call(self, name: str, args: dict) -> tuple[str, bool]:
        try:
            fn = getattr(self, f"t_{name}", None)
            if fn is None:
                return f"unknown tool {name}", True
            import inspect
            params = set(inspect.signature(fn).parameters)
            alias = {"line_start": "start_line", "line_end": "end_line", "file_path": "path", "filepath": "path",
                     "filename": "path", "directory": "path", "dir": "path", "regex": "pattern", "query": "pattern"}
            args, ignored = dict(args), []
            for k in list(args):
                if k not in params:
                    tgt = alias.get(k)
                    if tgt in params and tgt not in args:
                        args[tgt] = args.pop(k)
                    else:
                        args.pop(k); ignored.append(k)
            repeats = 0
            if name in ("list_files", "read_file", "search", "lookup_migration_rule"):
                key = (name, json.dumps(args, sort_keys=True, default=str), getattr(self, "_edits", 0))
                seen = self.__dict__.setdefault("_seen", {})
                seen[key] = repeats = seen.get(key, 0) + 1
            out = _clip(fn(**args))
            if repeats >= 3:
                out += (f"\n(note: you have made this exact call {repeats} times with no edit in between, so the result "
                        "cannot change. Make an edit, run the tests, or call finish.)")
            if repeats >= 6:
                self.budget_hit = "stalled"      # looping on identical reads: end the attempt instead of burning the budget
            if ignored:
                out += f"\n(note: ignored unknown argument(s): {', '.join(ignored)}; valid: {', '.join(sorted(params - {'self'}))})"
            return out, False
        except WorkspaceError as e:
            return str(e), True
        except TypeError as e:
            hint = ""
            if "missing" in str(e) and name in ("write_file", "edit_file"):
                hint = " (the call may have been cut off by the output-token limit; use a smaller edit_file instead of a large write_file)"
            return f"bad arguments for {name}: {e}{hint}", True
        except Exception as e:  # tool bugs must not crash the attempt
            return f"tool error: {type(e).__name__}: {e}", True

    def t_list_files(self, path: str = ".") -> str:
        return self.ws.list_files(path or ".")

    def t_read_file(self, path: str, start_line: int = 1, end_line: Optional[int] = None) -> str:
        return self.ws.read_file(path, start_line, end_line)

    def t_search(self, pattern: str, path: str = ".", glob: Optional[str] = None, context: int = 0) -> str:
        return self.ws.search(pattern, path or ".", glob, context=max(0, min(int(context or 0), 5)))

    def t_edit_file(self, path: str, old: str, new: str) -> str:
        out = self.ws.edit_file(path, old, new)
        self._edits = getattr(self, "_edits", 0) + 1
        return out

    def t_write_file(self, path: str, content: str) -> str:
        out = self.ws.write_file(path, content)
        self._edits = getattr(self, "_edits", 0) + 1
        return out

    def t_lookup_migration_rule(self, query: str) -> str:
        if not self.docs:
            return "documentation lookup is not enabled"
        hits = self.docs.search(query, k=3)
        for h in hits:
            self.retrieved.append({"query": query, **h.to_dict()})
        self.emit("retrieval", query=query, hits=[{"heading": h.heading, "url": h.url, "score": h.score} for h in hits])
        return format_hits(hits)

    def t_finish(self, status: str, summary: str) -> str:
        self.finished = (status, summary)
        return "recorded"

    def t_run_tests(self, selector: Optional[list] = None) -> str:
        if isinstance(selector, str):          # weak models send '[]' or 'tests/x.py' as a string
            try:
                selector = json.loads(selector)
            except Exception:
                selector = [selector]
            if isinstance(selector, str):
                selector = [selector]
        selector = [x for x in (selector or []) if isinstance(x, str) and x.strip()] or None
        b = self.cfg.budget
        if self.test_runs >= b.max_test_runs:
            self.budget_hit = "budget_tests"
            return f"BUDGET EXHAUSTED: no test runs left ({b.max_test_runs}). Call finish."
        edits = getattr(self, "_edits", 0)
        if self.test_runs > 0 and edits == getattr(self, "_edits_at_last_test", -1):
            return ("NO FILES CHANGED since your last run_tests, so the result would be identical. "
                    "Use edit_file to fix the failure shown above, then run_tests again.")
        self._edits_at_last_test = edits
        self.test_runs += 1
        extra = ""
        partial = bool(selector)
        if selector:
            for s in selector:
                if s.startswith("-") or any(ch in s for ch in "\n\r\x00"):
                    return "selector entries must be paths or node ids, not options"
            extra = " ".join(shlex.quote(s) for s in selector)
        run = self.env.run_tests(strict=self.strict, timeout=b.test_timeout_s, extra_args=extra,
                                 junit_name=f"agent-{self.test_runs}.xml")
        text = format_test_result(run, self.required, self.strict, partial)
        n_fail = len(self.required - run.passed)
        self.emit("test_run", n=self.test_runs, passed=len(run.passed), failing=len(run.failing),
                  required_failing=n_fail, required_total=len(self.required), partial=partial)
        if partial:
            return text
        self.last_fail = n_fail
        if self.best_fail is None or n_fail < self.best_fail:
            self.best_fail = n_fail
            self.ws.snapshot()
        elif n_fail > self.best_fail and self.cfg.revert_on_worse:
            self.ws.restore_best()
            self.reverts += 1
            self.last_fail = self.best_fail      # workspace now equals the best state
            self.emit("revert", from_failing=n_fail, to_failing=self.best_fail)
            text = (f"REVERTED: your last edits increased failing required tests from {self.best_fail} to {n_fail}; "
                    f"the workspace was restored to the best state so far ({self.best_fail} failing). "
                    "Try a different approach.\n\n" + text)
        return text

    # called by the agent loop once at the end
    def finalize(self) -> None:
        if (self.cfg.revert_on_worse and self.best_fail is not None and self.last_fail is not None
                and self.last_fail > self.best_fail):
            self.ws.restore_best()
            self.reverts += 1
            self.emit("revert", from_failing=self.last_fail, to_failing=self.best_fail, final=True)
