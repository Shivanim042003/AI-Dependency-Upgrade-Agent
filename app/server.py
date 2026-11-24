"""Allowlisted demo API. Only preconfigured tasks run; nothing user-supplied is ever executed.

Modes (env PMIGRATE_DEMO_MODE or `pmigrate serve --mode`):
  live    real model calls (needs a configured model provider; see docs/PROVIDERS.md), real sandboxed verification
  replay  replays a recorded/scripted agent transcript (no API key, no spend), real verification

Safety controls: task + variant allowlist, one run at a time, per-IP hourly limit, per-run
budget, a daily spend cap, and refusal to use the unsandboxed local backend for live runs.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pmigrate.config import RESULTS_DIR, TASKS_DIR, Budget
from pmigrate.db import DB
from pmigrate.llm import LLMResponse, ScriptedClient, llm_configured, text_reply
from pmigrate.report import build_report
from pmigrate.runner import run_attempt
from pmigrate.sandbox import docker_available
from pmigrate.tasks import load_task

app = FastAPI(title="pmigrate demo")
STATIC = Path(__file__).parent / "static"
MODE = os.environ.get("PMIGRATE_DEMO_MODE", "live")
DEMO_BUDGET = Budget(max_steps=30, max_cost_usd=float(os.environ.get("PMIGRATE_DEMO_RUN_USD", "0.50")),
                     max_wall_s=600, max_test_runs=10)
DAILY_CAP_USD = float(os.environ.get("PMIGRATE_DEMO_DAILY_USD", "5.0"))
PER_IP_PER_HOUR = int(os.environ.get("PMIGRATE_DEMO_PER_IP_HOUR", "5"))

JOBS: dict = {}
_busy = threading.Semaphore(1)
_ip_hits: dict = defaultdict(deque)


def allowlist() -> dict:
    p = TASKS_DIR / "demo.json"
    return json.loads(p.read_text()) if p.exists() else {"tasks": [], "variants": []}


def _backend() -> str:
    forced = os.environ.get("PMIGRATE_BACKEND")
    if forced:
        return forced
    if docker_available():
        return "docker"
    if MODE == "replay" or os.environ.get("PMIGRATE_ALLOW_LOCAL_BACKEND") == "1":
        return "local"
    raise HTTPException(503, "No Docker daemon available and the unsandboxed local backend is disabled. "
                             "Start Docker, or set PMIGRATE_ALLOW_LOCAL_BACKEND=1 for a trusted local demo.")


def _spent_last_24h() -> float:
    with DB().conn() as c:
        r = c.execute("SELECT COALESCE(SUM(cost_usd),0) s FROM runs WHERE experiment='demo' AND started_at > ?",
                      (time.time() - 86400,)).fetchone()
    return float(r["s"])


def _replay_script(task_id: str, variant: str) -> list:
    db = DB()
    with db.conn() as c:
        r = c.execute("SELECT run_id FROM runs WHERE task_id=? AND variant=? AND success=1 AND experiment!='demo' "
                      "ORDER BY cost_usd ASC LIMIT 1", (task_id, variant)).fetchone()
    if r:
        tr = db.load_transcript(r["run_id"])
        resp = [LLMResponse.from_dict(x) for x in tr if x.get("role") == "assistant"]
        if resp:
            return resp
    if task_id == "toy-users":
        from pmigrate.toy import scripted_solution
        script = scripted_solution()
        if "plan" in variant:
            script.insert(0, text_reply("Plan: 1) migrate models (validators, Config, Field args, Optional defaults, "
                                        "GenericModel) 2) migrate service API calls 3) move BaseSettings to "
                                        "pydantic-settings 4) update dependencies 5) run tests."))
        return script
    raise HTTPException(404, "no recorded transcript for this task/variant; run it live or record one with `pmigrate eval`")


@app.get("/api/health")
def health():
    return {"mode": MODE, "docker": docker_available(), "api_key": llm_configured(),
            "daily_cap_usd": DAILY_CAP_USD, "spent_24h_usd": round(_spent_last_24h(), 4),
            "per_run_budget": {"max_cost_usd": DEMO_BUDGET.max_cost_usd, "max_steps": DEMO_BUDGET.max_steps}}


@app.get("/api/tasks")
def tasks():
    al = allowlist()
    out = []
    for tid in al["tasks"]:
        try:
            t = load_task(tid)
        except Exception:
            continue
        out.append({"id": t.id, "repo_url": t.repo_url if t.repo_url.startswith("https://") else "(local fixture)",
                    "pydantic": f"{t.pydantic_before} -> {t.pydantic_after}", "policy": t.policy,
                    "python": t.python_version, "notes": t.notes})
    return {"tasks": out, "variants": al["variants"]}


@app.post("/api/runs", status_code=202)
async def start_run(request: Request):
    body = await request.json()
    task_id, variant = body.get("task_id"), body.get("variant")
    al = allowlist()
    if task_id not in al["tasks"] or variant not in al["variants"]:
        raise HTTPException(400, "task/variant not in the demo allowlist")
    ip = request.client.host if request.client else "?"
    q = _ip_hits[ip]
    now = time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= PER_IP_PER_HOUR:
        raise HTTPException(429, f"limit of {PER_IP_PER_HOUR} runs per hour reached")
    is_agent = variant.startswith("agent")
    if MODE == "live" and is_agent:
        if not llm_configured():
            raise HTTPException(503, "live mode needs an LLM provider configured on the server (or start with --mode replay)")
        if _spent_last_24h() >= DAILY_CAP_USD:
            raise HTTPException(429, "daily demo spend cap reached; try replay mode or come back tomorrow")
    backend = _backend()
    script = _replay_script(task_id, variant) if (MODE == "replay" and is_agent) else None
    if not _busy.acquire(blocking=False):
        raise HTTPException(429, "another run is in progress; try again in a minute")
    q.append(now)
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "events": [], "done": False, "result": None, "error": None, "task": task_id, "variant": variant}
    JOBS[jid] = job

    def work():
        try:
            task = load_task(task_id)
            llm = ScriptedClient(script) if script else None
            rec = run_attempt(task, variant, 0, experiment="demo", budget=DEMO_BUDGET, backend=backend,
                              db=DB(), llm=llm, on_event=lambda rid, ev: job["events"].append(ev))
            a, v = rec.agent, rec.verification
            job["result"] = {
                "success": rec.success, "failure_category": rec.failure_category, "end_reason": rec.end_reason,
                "cost_usd": round(rec.cost_usd, 4), "steps": rec.steps, "agent_wall_s": round(rec.agent_wall_s, 1),
                "diff": rec.diff[:60000], "verification": v.to_json(),
                "summary": a.summary if a else "", "plan": a.plan if a else "",
                "retrieved_docs": [{**d, "text": d["text"][:700]} for d in (a.retrieved_docs if a else [])],
                "denied_edits": a.denied_edits if a else [], "mode": MODE,
            }
        except Exception as e:
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            job["done"] = True
            _busy.release()

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": jid}


def _job(jid: str) -> dict:
    if jid not in JOBS:
        raise HTTPException(404, "unknown run")
    return JOBS[jid]


@app.get("/api/runs/{jid}")
def get_run(jid: str):
    j = _job(jid)
    return {"id": jid, "done": j["done"], "error": j["error"], "result": j["result"], "n_events": len(j["events"])}


@app.get("/api/runs/{jid}/events")
async def run_events(jid: str):
    j = _job(jid)

    async def gen():
        i = 0
        while True:
            while i < len(j["events"]):
                ev = j["events"][i]
                yield f"data: {json.dumps({'type': ev['type'], 'ts': ev['ts'], **ev['payload']}, default=str)}\n\n"
                i += 1
            if j["done"] and i >= len(j["events"]):
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/results")
def results(experiment: str | None = None, split: str | None = None):
    md, data = build_report(DB(), experiment, split)
    return {"markdown": md, "summary": data.get("summary", {}), "warnings": data.get("warnings", [])}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
