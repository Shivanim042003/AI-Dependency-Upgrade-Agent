"""The plain-Python agent loop: run tests -> read errors -> find breaking code -> patch -> rerun.

No orchestration framework on purpose. Optional toggles (each evaluated against the same
baseline, one at a time): docs lookup, a planning pre-step, and revert-if-tests-get-worse.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import AgentConfig, cost_usd
from .llm import LLMClient, LLMResponse, ToolCallRejected, Usage
from .scan import format_inventory, scan_usages
from .tasks import TaskSpec
from .tools import ToolBox, tool_defs

SYSTEM = """You are a senior Python engineer migrating a repository from Pydantic v1 to Pydantic v2.

The repo is checked out in your workspace. Pydantic {after} is installed{extras}. The test suite is the
specification: make every currently-required test pass.

Rules (breaking any of them makes the attempt count as FAILED, however green the tests are):
1. Do a NATIVE v2 migration. Never import from `pydantic.v1` or `pydantic.deprecated`, never downgrade or cap
   pydantic below 2, never add compatibility shims around v1 behaviour.
2. Tests, conftest, CI and pytest configuration are read-only. Fix the source code, not the tests.
3. Never add skips/xfails or suppress warnings. {strict_note}
4. Keep changes minimal and targeted; do not refactor unrelated code.
5. Update dependency declarations when v2 needs it (e.g. `pydantic-settings`), without capping pydantic below 2.

Workflow: call run_tests first to see what fails. Before editing, use `search` to find ALL usages of the affected
APIs across the repo (behaviour changes such as Optional fields becoming required also matter). Edit, re-run, repeat.
Call `finish` when all tests pass, or if you are genuinely stuck. Budget: at most {max_steps} steps and
{max_tests} test runs; running out counts as failure.{docs_note}"""

STRICT_NOTE = ("Tests here run with Pydantic deprecation warnings treated as errors, so v1-style APIs that still "
               "'work' in v2 (.dict(), parse_obj, @validator, class Config, ...) will fail; use the v2 APIs.")
DOCS_NOTE = ("\nYou can call `lookup_migration_rule` to read the official v2 migration guide for a given API; "
             "use it instead of guessing v2 names/semantics.")


@dataclass
class AgentResult:
    end_reason: str = "error"          # finished | gave_up | budget_steps | budget_cost | budget_time | budget_tests | error
    summary: str = ""
    steps: int = 0
    tool_calls: int = 0
    test_runs: int = 0
    reverts: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    wall_s: float = 0.0
    diff: str = ""
    plan: str = ""
    retrieved_docs: list = field(default_factory=list)
    denied_edits: list = field(default_factory=list)
    transcript: list = field(default_factory=list)
    error: Optional[str] = None


def build_system(task: TaskSpec, cfg: AgentConfig) -> str:
    extras = f" (plus {', '.join(task.extra_deps_after)})" if task.extra_deps_after else ""
    return SYSTEM.format(
        after=task.pydantic_after, extras=extras,
        strict_note=STRICT_NOTE if task.policy == "native_v2" else "",
        max_steps=cfg.budget.max_steps, max_tests=cfg.budget.max_test_runs,
        docs_note=DOCS_NOTE if cfg.use_docs else "")


def prompt_hash(task: TaskSpec, cfg: AgentConfig) -> str:
    import hashlib
    return hashlib.sha256((build_system(task, cfg) + json.dumps(tool_defs(cfg.use_docs), sort_keys=True)
                           + str(cfg.use_planning)).encode()).hexdigest()[:12]


def _compact(messages: list, keep_last: Optional[int] = None, limit_chars: Optional[int] = None) -> list:
    """Elide old tool results/edit arguments so long attempts stay within context. Never touches the last turns.
    Defaults suit large-context models; for small contexts set PMIGRATE_CONTEXT_CHARS (e.g. 12000)."""
    import os
    keep_last = keep_last if keep_last is not None else int(os.environ.get("PMIGRATE_KEEP_LAST", "8"))
    limit_chars = limit_chars if limit_chars is not None else int(os.environ.get("PMIGRATE_CONTEXT_CHARS", "140000"))
    total = len(json.dumps(messages))
    if total <= limit_chars:
        return messages
    out = json.loads(json.dumps(messages))
    for m in out[: max(0, len(out) - keep_last)]:
        if not isinstance(m["content"], list):
            continue
        for b in m["content"]:
            if m["role"] == "user" and b.get("type") == "tool_result" and len(str(b.get("content", ""))) > 400:
                b["content"] = str(b["content"])[:300] + "\n...[older output elided to save context]"
            elif m["role"] == "assistant" and b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
                for k, v in list(b["input"].items()):
                    if isinstance(v, str) and len(v) > 200:
                        b["input"][k] = v[:150] + "...[elided]"
    return out


def run_agent(task: TaskSpec, toolbox: ToolBox, cfg: AgentConfig, llm: LLMClient,
              emit: Callable = lambda *a, **k: None) -> AgentResult:
    res = AgentResult()
    t0 = time.time()
    system = build_system(task, cfg)
    tools = tool_defs(cfg.use_docs)
    b = cfg.budget
    messages: list = [{"role": "user", "content": [{"type": "text", "text":
        "Migrate this repository to Pydantic v2 following the rules. Start by running the tests."}]}]

    def charge(r: LLMResponse) -> None:
        res.usage.add(r.usage)
        res.cost_usd = cost_usd(cfg.model, res.usage.input_tokens, res.usage.output_tokens,
                                res.usage.cache_read_tokens, res.usage.cache_write_tokens)

    def call_llm(msgs, use_tools=True) -> LLMResponse:
        r = llm.complete(model=cfg.model, system=system, messages=_compact(msgs),
                         tools=tools if use_tools else None, max_tokens=cfg.max_tokens,
                         temperature=cfg.temperature)
        res.steps += 1
        charge(r)
        res.transcript.append({"role": "assistant", **r.to_dict()})
        emit("llm", step=res.steps, text=r.text[:600], tool_uses=[t["name"] for t in r.tool_uses],
             cost_usd=round(res.cost_usd, 4))
        return r

    def over_budget() -> Optional[str]:
        if res.steps >= b.max_steps:
            return "budget_steps"
        if res.cost_usd >= b.max_cost_usd or (res.usage.input_tokens + res.usage.output_tokens) >= b.max_total_tokens:
            return "budget_cost"
        if time.time() - t0 >= b.max_wall_s:
            return "budget_time"
        return toolbox.budget_hit

    try:
        # ---- optional planning pre-step: scan ALL usages first ---------------------------
        if cfg.use_planning:
            inv = format_inventory(scan_usages(toolbox.ws.root))
            emit("scan", inventory=inv[:4000])
            messages[0]["content"][0]["text"] += (
                "\n\nBefore touching any file, write a concise ordered migration plan (max 12 bullets) based on this "
                "inventory. Do not call tools in this reply.\n\n" + inv)
            r = call_llm(messages, use_tools=False)
            res.plan = r.text
            emit("plan", text=r.text[:3000])
            messages.append({"role": "assistant", "content": [{"type": "text", "text": r.text or "(plan)"}]})
            messages.append({"role": "user", "content": [{"type": "text",
                             "text": "Good. Now execute the plan. Start by running the tests."}]})

        # ---- main loop -------------------------------------------------------------------
        rejected = 0
        idle_turns = 0
        while True:
            why = over_budget()
            if why:
                res.end_reason = why
                break
            try:
                r = call_llm(messages)
                rejected = 0
            except ToolCallRejected as e:
                rejected += 1
                res.steps += 1
                emit("tool_rejected", message=str(e)[:300])
                if rejected > 4:
                    raise
                names = ", ".join(t["name"] for t in tools)
                messages.append({"role": "user", "content": [{"type": "text", "text":
                                 f"Your last tool call was rejected by the API ({str(e)[:200]}). Only these tools exist: "
                                 f"{names}. Use exactly those names and the documented arguments."}]})
                continue
            blocks = [x for x in r.blocks if x["type"] in ("text", "tool_use")]
            messages.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no output)"}]})
            uses = r.tool_uses
            if not uses:
                # no `finish` call: nudge, but after 2 nudges treat the text as its closing statement
                # (the independent verifier decides success) instead of burning the step budget
                idle_turns += 1
                if idle_turns >= 3:
                    res.end_reason, res.summary = "finished", (r.text or "")[:500]
                    break
                messages.append({"role": "user", "content": [{"type": "text", "text":
                                 "Continue using the tools, or call `finish` if you are done or stuck."}]})
                continue
            idle_turns = 0
            results = []
            for u in uses:
                res.tool_calls += 1
                out, is_err = toolbox.call(u["name"], u["input"] or {})
                emit("tool", name=u["name"], input=_short(u["input"]), error=is_err, output=out[:1500])
                results.append({"type": "tool_result", "tool_use_id": u["id"], "content": out,
                                **({"is_error": True} if is_err else {})})
            messages.append({"role": "user", "content": results})
            res.transcript.append({"role": "tool_results", "results": [
                {"name": u["name"], "ok": not x.get("is_error", False), "chars": len(x["content"])}
                for u, x in zip(uses, results)]})
            if toolbox.finished:
                status, summary = toolbox.finished
                res.summary = summary
                res.end_reason = "finished" if status == "done" else "gave_up"
                break
    except Exception as e:  # LLM/API failure: record, do not crash the harness
        res.end_reason, res.error = "error", f"{type(e).__name__}: {str(e)[:400]}"
        emit("error", message=res.error)

    toolbox.finalize()
    res.test_runs = toolbox.test_runs
    res.reverts = toolbox.reverts
    res.retrieved_docs = toolbox.retrieved
    res.denied_edits = list(toolbox.ws.denied_edits)
    res.wall_s = time.time() - t0
    try:
        res.diff = toolbox.ws.diff()
    except Exception as e:
        res.diff, res.error = "", (res.error or "") + f" diff failed: {e}"
    emit("end", reason=res.end_reason, steps=res.steps, cost_usd=round(res.cost_usd, 4), wall_s=round(res.wall_s, 1))
    return res


def _short(inp: dict) -> dict:
    return {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in (inp or {}).items()}
