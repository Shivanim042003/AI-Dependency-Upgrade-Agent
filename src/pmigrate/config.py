"""Paths, budgets and pricing. Everything tunable lives here or in configs/*.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.environ.get("PMIGRATE_ROOT", Path(__file__).resolve().parents[2]))
TASKS_DIR = ROOT / "tasks"
DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "docs"
# Must live OUTSIDE the project tree: pytest walks parent directories for config files, and a
# cache inside this repo would make every sandboxed test run inherit our own pyproject.toml.
CACHE_DIR = Path(os.environ.get("PMIGRATE_CACHE", Path.home() / ".cache" / "pmigrate"))
RESULTS_DIR = ROOT / "results"
DB_PATH = Path(os.environ.get("PMIGRATE_DB", RESULTS_DIR / "runs.sqlite"))
PRICING_PATH = ROOT / "configs" / "pricing.json"

# Files the agent may never modify. Matched against repo-relative POSIX paths.
PROTECTED_GLOBS = [
    "tests/**", "test/**", "**/tests/**", "**/test/**",
    "**/test_*.py", "**/*_test.py", "**/conftest.py", "conftest.py",
    "pytest.ini", "tox.ini", "noxfile.py", ".github/**", "Makefile",
]


@dataclass
class Budget:
    """Hard per-attempt limits. Exceeding any one ends the attempt as `budget_exhausted`."""
    max_steps: int = 40            # LLM turns
    max_cost_usd: float = 1.00
    max_wall_s: int = 1200
    max_test_runs: int = 12
    max_total_tokens: int = 3_000_000   # input+output across turns; the cap that works for free/local models (cost = $0)
    test_timeout_s: int = 300


@dataclass
class AgentConfig:
    model: str = os.environ.get("PMIGRATE_MODEL", "")
    use_docs: bool = False
    use_planning: bool = False
    revert_on_worse: bool = False
    max_tokens: int = int(os.environ.get("PMIGRATE_MAX_TOKENS", "4096"))
    temperature: float = 0.0
    budget: Budget = field(default_factory=Budget)

    def label(self) -> str:
        parts = ["agent"]
        if self.use_docs:
            parts.append("docs")
        if self.use_planning:
            parts.append("plan")
        if self.revert_on_worse:
            parts.append("revert")
        return "+".join(parts)


def load_pricing() -> dict:
    """USD per million tokens, keyed by model id.

    NOTE: verify these against your provider's current pricing page before you report
    any dollar figure. Token counts are always logged so cost can be recomputed.
    """
    if PRICING_PATH.exists():
        return json.loads(PRICING_PATH.read_text())
    return {}


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_read: int = 0, cache_write: int = 0) -> float:
    p = load_pricing().get(model)          # unknown/local models cost $0 (never guess a price)
    if not p:
        return 0.0
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_read * p.get("cache_read", p["input"] * 0.1)
        + cache_write * p.get("cache_write", p["input"] * 1.25)
    ) / 1_000_000
