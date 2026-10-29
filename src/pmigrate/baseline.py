"""Deterministic baseline: Pydantic's own codemod, `bump-pydantic`.

Runs in a tools venv separate from the project env. Edits to protected paths (tests) are
reverted afterwards so the baseline is judged by the same rules as the agent: source-only changes.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import CACHE_DIR
from .env import is_protected
from .sandbox import LocalSandbox, Sandbox
from .workspace import Workspace, git

TOOLS_VENV = CACHE_DIR / "tools" / "bump-venv"


def ensure_bump_pydantic() -> Path:
    exe = TOOLS_VENV / "bin" / "bump-pydantic"
    if exe.exists():
        return exe
    TOOLS_VENV.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["uv", "venv", "--python", "3.12", str(TOOLS_VENV)], check=True, capture_output=True)
    subprocess.run(["uv", "pip", "install", "--python", str(TOOLS_VENV / "bin" / "python"), "bump-pydantic"],
                   check=True, capture_output=True)
    return exe


@dataclass
class BumpResult:
    diff: str
    wall_s: float
    returncode: int
    output: str
    reverted_protected: list


def run_bump_pydantic(ws: Workspace) -> BumpResult:
    """Run the codemod on the workspace (host-side; it only parses/rewrites Python source and
    never imports or executes repository code)."""
    exe = ensure_bump_pydantic()
    t0 = time.time()
    # Target every top-level dir/file that contains non-test Python source.
    targets = []
    for p in sorted(ws.root.iterdir()):
        if p.name.startswith(".") or p.name in ("node_modules", "venv", "build", "dist"):
            continue
        if p.is_file() and p.suffix == ".py" and not is_protected(p.name):
            targets.append(p.name)
        elif p.is_dir() and not is_protected(p.name + "/x.py") and any(p.rglob("*.py")):
            targets.append(p.name)
    outputs, rc = [], 0
    for tgt in targets:
        r = subprocess.run([str(exe), tgt], cwd=ws.root, capture_output=True, text=True, timeout=600)
        rc = rc or r.returncode
        outputs.append(f"$ bump-pydantic {tgt}\n{r.stdout[-800:]}{r.stderr[-400:]}")
    # restore protected paths if the codemod touched them (e.g. tests nested inside the package)
    changed = ws.changed_files()
    reverted = [c for c in changed if is_protected(c)]
    for c in reverted:
        git(ws.root, "checkout", ws.base, "--", c, check=False)
    return BumpResult(ws.diff(), time.time() - t0, rc, "\n".join(outputs), reverted)
