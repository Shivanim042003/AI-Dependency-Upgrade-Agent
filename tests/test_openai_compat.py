"""OpenAI-compatible provider: wire-format translation, and a full agent loop over real HTTP to a mock server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pmigrate.agent import run_agent
from pmigrate.config import AgentConfig
from pmigrate.env import TestRun
from pmigrate.llm import (OpenAICompatClient, llm_configured, messages_to_openai, response_from_openai,
                          tools_to_openai)
from pmigrate.tools import ToolBox, tool_defs
from pmigrate.verify import Baseline
from pmigrate.workspace import Workspace, init_fresh_repo


def test_messages_translate_tool_roundtrip():
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "go"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"},
                                          {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "x = 1"},
                                     {"type": "text", "text": "continue"}]},
    ]
    out = messages_to_openai("SYS", msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "user"]
    assert out[2]["tool_calls"][0]["function"] == {"name": "read_file", "arguments": '{"path": "a.py"}'}
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "x = 1"}


def test_tools_translate():
    t = tools_to_openai(tool_defs(True))
    assert all(x["type"] == "function" and "parameters" in x["function"] for x in t)
    assert tools_to_openai(None) is None


def test_response_parsing_and_bad_arguments():
    r = response_from_openai({"choices": [{"message": {"content": "hi", "tool_calls": [
        {"id": "a", "function": {"name": "run_tests", "arguments": "{}"}},
        {"id": "b", "function": {"name": "search", "arguments": "{not json"}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3}})
    assert r.text == "hi" and [t["name"] for t in r.tool_uses] == ["run_tests", "search"]
    assert r.tool_uses[1]["input"] == {} and r.usage.input_tokens == 10 and r.usage.output_tokens == 3


def test_llm_configured_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PMIGRATE_PROVIDER", raising=False)
    assert not llm_configured()
    monkeypatch.setenv("PMIGRATE_PROVIDER", "openai")
    monkeypatch.setenv("PMIGRATE_BASE_URL", "http://localhost:11434/v1")
    assert llm_configured()          # local Ollama needs no key


class Mock(BaseHTTPRequestHandler):
    script: list = []
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Mock.seen.append(body)
        msg = Mock.script.pop(0)
        data = json.dumps({"choices": [{"message": msg}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def call(name, **args):
    return {"content": None, "tool_calls": [{"id": f"id-{len(Mock.seen)}-{name}", "type": "function",
                                             "function": {"name": name, "arguments": json.dumps(args)}}]}


class FakeEnv:
    def __init__(self, ws, task): self.ws, self.task = ws, task
    def run_tests(self, **kw):
        ok = "FIXED" in (self.ws.root / "m.py").read_text()
        return TestRun(outcomes={"t1": "passed" if ok else "failed"}, stdout_tail="")


def test_agent_loop_over_http(tmp_path, task):
    srv = HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Mock.seen = []
    Mock.script = [call("run_tests"), call("write_file", path="m.py", content="FIXED = 1\n"), call("run_tests"),
                   call("finish", status="done", summary="fixed it")]
    repo = tmp_path / "repo"; repo.mkdir(); (repo / "m.py").write_text("x = 1\n")
    ws = Workspace(repo, init_fresh_repo(repo))
    cfg = AgentConfig(model="any-model")
    tb = ToolBox(ws=ws, env=FakeEnv(ws, task), base=Baseline(required=["t1"], v1_passed=["t1"]), cfg=cfg)
    llm = OpenAICompatClient(base_url=f"http://127.0.0.1:{srv.server_port}/v1", api_key="k")
    r = run_agent(task, tb, cfg, llm)
    srv.shutdown()
    assert r.end_reason == "finished" and "FIXED" in r.diff and r.steps == 4
    assert r.usage.input_tokens == 400 and r.cost_usd >= 0
    # second request must carry the assistant tool_call followed by its tool result (protocol correctness)
    roles = [m["role"] for m in Mock.seen[1]["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert Mock.seen[0]["tools"][0]["type"] == "function"
