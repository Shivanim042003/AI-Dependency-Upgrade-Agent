"""Thin LLM layer: one real client (Anthropic Messages API) and one scripted client.

Responses are normalised to plain dicts so a run's transcript can be stored in SQLite/JSON and
replayed later by `ScriptedClient` (used by tests and by the offline demo mode).
"""
from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, o: "Usage") -> None:
        self.input_tokens += o.input_tokens
        self.output_tokens += o.output_tokens
        self.cache_read_tokens += o.cache_read_tokens
        self.cache_write_tokens += o.cache_write_tokens

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class LLMResponse:
    blocks: list                      # [{"type":"text","text":..}|{"type":"tool_use","id","name","input"}]
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)

    @property
    def text(self) -> str:
        return "\n".join(b["text"] for b in self.blocks if b["type"] == "text")

    @property
    def tool_uses(self) -> list:
        return [b for b in self.blocks if b["type"] == "tool_use"]

    def to_dict(self) -> dict:
        return {"blocks": self.blocks, "stop_reason": self.stop_reason, "usage": self.usage.to_dict()}

    @staticmethod
    def from_dict(d: dict) -> "LLMResponse":
        return LLMResponse(d["blocks"], d.get("stop_reason", "end_turn"), Usage(**d.get("usage", {})))


class LLMClient(Protocol):
    def complete(self, *, model: str, system: str, messages: list, tools: Optional[list],
                 max_tokens: int, temperature: Optional[float]) -> LLMResponse: ...


class AnthropicClient:
    def __init__(self, api_key: Optional[str] = None):
        import anthropic
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=key, max_retries=4, timeout=180)

    def complete(self, *, model, system, messages, tools, max_tokens, temperature=None) -> LLMResponse:
        # Prompt caching: stable prefix (system + tools) and the growing conversation tail.
        msgs = copy.deepcopy(messages)
        if msgs:
            last = msgs[-1]["content"]
            if isinstance(last, list) and last:
                last[-1]["cache_control"] = {"type": "ephemeral"}
        kwargs = dict(model=model, max_tokens=max_tokens, messages=msgs,
                      system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}])
        if tools:
            kwargs["tools"] = tools
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = self.client.messages.create(**kwargs)
        blocks = []
        for b in resp.content:
            if b.type == "text":
                blocks.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        u = resp.usage
        usage = Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        return LLMResponse(blocks, resp.stop_reason or "end_turn", usage)


# ---------------------------------------------------------------------------------------------
# OpenAI-compatible chat-completions client (OpenRouter, Groq, Gemini's OpenAI endpoint, Ollama,
# vLLM, LM Studio, OpenAI...). No SDK dependency: plain HTTPS + JSON.
# ---------------------------------------------------------------------------------------------
def tools_to_openai(tools: Optional[list]) -> Optional[list]:
    if not tools:
        return None
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t["input_schema"]}} for t in tools]


def messages_to_openai(system: str, messages: list) -> list:
    """Translate our Anthropic-style block messages into OpenAI chat messages."""
    out = [{"role": "system", "content": system}]
    for m in messages:
        blocks = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
        if m["role"] == "assistant":
            text = "\n".join(b["text"] for b in blocks if b["type"] == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                     for b in blocks if b["type"] == "tool_use"]
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            # tool results must directly follow the assistant tool_calls message
            for b in blocks:
                if b["type"] == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": str(b.get("content", ""))})
            text = "\n".join(b["text"] for b in blocks if b["type"] == "text")
            if text:
                out.append({"role": "user", "content": text})
    return out


def response_from_openai(data: dict) -> LLMResponse:
    choice = data["choices"][0]
    msg = choice.get("message", {})
    blocks = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    for i, c in enumerate(msg.get("tool_calls") or []):
        fn = c.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            args = {}
        blocks.append({"type": "tool_use", "id": c.get("id") or f"call_{i}", "name": fn.get("name", ""), "input": args})
    u = data.get("usage") or {}
    usage = Usage(input_tokens=u.get("prompt_tokens", 0) or 0, output_tokens=u.get("completion_tokens", 0) or 0)
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return LLMResponse(blocks, stop, usage)


def _retry_after(headers, detail: str):
    """Seconds the provider asked us to wait (Retry-After header, or 'try again in 1m5.4s' in the body)."""
    import re
    try:
        ra = headers.get("retry-after") if headers else None
        if ra:
            return float(ra)
    except (ValueError, AttributeError):
        pass
    m = re.search(r"try again in ([0-9hms.]+)", detail or "")
    if not m:
        return None
    units = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    parts = re.findall(r"([0-9.]+)(ms|h|m|s)", m.group(1))
    return sum(float(v) * units[u] for v, u in parts) if parts else None


class ToolCallRejected(RuntimeError):
    """The provider refused the model's tool call (bad name or arguments). Recoverable: the agent can be told."""


class OpenAICompatClient:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: int = 180):
        self.base_url = (base_url or os.environ.get("PMIGRATE_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("PMIGRATE_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        self.timeout = int(os.environ.get("PMIGRATE_LLM_TIMEOUT", timeout))
        if not self.api_key and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            raise RuntimeError("set PMIGRATE_API_KEY (or OPENAI_API_KEY) for the OpenAI-compatible provider")

    def complete(self, *, model, system, messages, tools, max_tokens, temperature=None) -> LLMResponse:
        import urllib.error
        import urllib.request
        if not getattr(self, "_probed", False):
            try:
                urllib.request.urlopen(urllib.request.Request(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key or 'none'}"}), timeout=8).close()
            except urllib.error.HTTPError:
                pass   # server reachable (some do not implement /models)
            except Exception as e:
                raise RuntimeError(f"cannot reach {self.base_url} within 8s ({e}); check URL, firewall, and that the server is running")
            self._probed = True
        body = {"model": model, "messages": messages_to_openai(system, messages), "max_tokens": max_tokens}
        ot = tools_to_openai(tools)
        if ot:
            body["tools"] = ot
        if temperature is not None:
            body["temperature"] = temperature
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "pmigrate/0.1", "Accept": "application/json",
                                              "Authorization": f"Bearer {self.api_key or 'none'}"})
        last = None
        for attempt in range(8):
            delay = None
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return response_from_openai(json.loads(r.read()))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                last = RuntimeError(f"HTTP {e.code}: {detail}")
                delay = _retry_after(e.headers, detail) if e.code == 429 else None
                if delay is not None and delay > 180:
                    raise RuntimeError(f"rate limit: provider asked to wait {delay:.0f}s (quota exhausted?): {detail}")
                if e.code == 400 and any(k in detail for k in ("tool call validation", "Failed to parse tool call", "tool_use_failed")):
                    if attempt >= 1:                     # one resample, then let the agent be told
                        raise ToolCallRejected(detail)
                elif e.code not in (408, 409, 429, 500, 502, 503, 504):
                    raise last
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
            time.sleep(delay + 1 if delay else min(30, 2 ** attempt * 2))      # honour the provider's retry hint
        raise RuntimeError(f"LLM request failed after retries: {last}")


def make_llm():
    """Provider from env: PMIGRATE_PROVIDER=anthropic (default) | openai (any OpenAI-compatible endpoint)."""
    prov = os.environ.get("PMIGRATE_PROVIDER", "anthropic").lower()
    if prov in ("openai", "openai-compat", "compat"):
        return OpenAICompatClient()
    return AnthropicClient()


def llm_configured() -> bool:
    prov = os.environ.get("PMIGRATE_PROVIDER", "anthropic").lower()
    if prov in ("openai", "openai-compat", "compat"):
        base = os.environ.get("PMIGRATE_BASE_URL", "")
        return bool(os.environ.get("PMIGRATE_API_KEY") or os.environ.get("OPENAI_API_KEY") or "localhost" in base or "127.0.0.1" in base)
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class ScriptedClient:
    """Returns pre-recorded responses in order. Used for tests and offline demo replay."""

    def __init__(self, responses: list):
        self.responses = [r if isinstance(r, LLMResponse) else LLMResponse.from_dict(r) for r in responses]
        self.calls: list = []

    def complete(self, *, model, system, messages, tools, max_tokens, temperature=None) -> LLMResponse:
        self.calls.append({"n_messages": len(messages), "tools": [t["name"] for t in tools or []]})
        if not self.responses:
            raise RuntimeError("ScriptedClient exhausted")
        return self.responses.pop(0)


def tool_call(name: str, _id: str = "t", usage: Optional[Usage] = None, **inp) -> LLMResponse:
    """Helper for building scripted responses."""
    return LLMResponse([{"type": "tool_use", "id": f"{_id}-{name}-{int(time.time() * 1e6) % 10**9}",
                         "name": name, "input": inp}], "tool_use", usage or Usage(1000, 100))


def text_reply(text: str, usage: Optional[Usage] = None) -> LLMResponse:
    return LLMResponse([{"type": "text", "text": text}], "end_turn", usage or Usage(500, 100))
