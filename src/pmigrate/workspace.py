"""The agent's workspace: a git checkout of base_sha with all upstream history removed.

History removal matters: the human upgrade commit lives in upstream history, so the agent
must not be able to `git log`/`git show` its way to the answer. The workspace is re-initialised
as a single fresh commit; the upstream clone stays in the host cache, never in the run dir.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .config import CACHE_DIR
from .env import is_protected
from .tasks import TaskSpec

GIT_ID = ["-c", "user.name=pmigrate", "-c", "user.email=pmigrate@localhost",
          "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", "dist", "build", ".eggs"}


def _unescape(t: str) -> str:
    return t.replace("\\n", "\n").replace("\\t", "\t")


def _closest(text: str, old: str) -> str:
    """Hint for a failed edit: the file line most similar to the first line of `old`."""
    import difflib
    first = old.strip().splitlines()[0] if old.strip() else ""
    lines = text.splitlines()
    if not first or not lines:
        return ""
    best = max(range(len(lines)), key=lambda i: difflib.SequenceMatcher(None, first, lines[i]).ratio())
    if difflib.SequenceMatcher(None, first, lines[best]).ratio() < 0.5:
        return ""
    return f"\nClosest line in the file (line {best + 1}): {lines[best]!r}"


def _indent(s: str) -> int:
    return len(s) - len(s.lstrip())


def _fuzzy_matches(text: str, old: str) -> list:
    """Line-aligned matches of `old` in `text` that differ only in trailing whitespace and a constant
    indentation shift. Returns [(first_line_index, n_lines, indent_delta)]. Windows whose file lines are
    tab-indented are skipped (re-indenting them would risk mixed indentation)."""
    t = text.split("\n")
    o = old.strip("\n").split("\n")
    if not any(l.strip() for l in o):
        return []
    out = []
    for i in range(len(t) - len(o) + 1):
        delta, ok = None, True
        for raw, b in zip(t[i:i + len(o)], o):
            if "\t" in raw[:_indent(raw)]:
                ok = False
                break
            a, b = raw.expandtabs(4).rstrip(), b.expandtabs(4).rstrip()
            if not a.strip() or not b.strip():
                if a.strip() or b.strip():
                    ok = False
                    break
                continue
            if a.lstrip() != b.lstrip():
                ok = False
                break
            d = _indent(a) - _indent(b)
            if delta is None:
                delta = d
            elif d != delta:
                ok = False
                break
        if ok and delta is not None:
            out.append((i, len(o), delta))
    return out


def _reindent(lines: list, delta: int):
    """Shift non-blank lines by `delta` spaces; None if that is not safely possible."""
    res = []
    for l in lines:
        if not l.strip() or delta == 0:
            res.append(l)
        elif delta > 0:
            if l.startswith("\t"):
                return None
            res.append(" " * delta + l)
        else:
            if not l.startswith(" " * -delta):
                return None
            res.append(l[-delta:])
    return res


def _match_lines(text: str, old: str, limit: int = 6) -> list:
    pos, s = [], 0
    while len(pos) < limit:
        j = text.find(old, s)
        if j < 0:
            break
        pos.append(text.count("\n", 0, j) + 1)
        s = j + 1
    return pos


class WorkspaceError(Exception):
    pass


def git(cwd: Path, *args: str, check: bool = True, input: Optional[str] = None) -> str:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "HTTPS_PROXY", "HTTP_PROXY",
           "https_proxy", "http_proxy", "SSL_CERT_FILE", "GIT_SSL_CAINFO", "NO_PROXY", "no_proxy")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    r = subprocess.run(["git", *GIT_ID, *args], cwd=cwd, capture_output=True, text=True,
                       env=env, input=input)
    if check and r.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {r.stderr.strip()[:500]}")
    return r.stdout


def upstream_clone(task: TaskSpec) -> Path:
    """Full clone in the host cache (history included). Never exposed to the agent."""
    dest = CACHE_DIR / "repos" / task.id
    if dest.exists():
        # make sure we have both shas; fetch if not
        have = all(subprocess.run(["git", "cat-file", "-e", f"{s}^{{commit}}"], cwd=dest,
                                  capture_output=True).returncode == 0
                   for s in (task.base_sha, task.reference_sha))
        if not have:
            # the task's repo_url may have changed since the clone was cached
            git(dest, "remote", "set-url", "origin", task.repo_url)
            git(dest, "fetch", "--all", "--tags", "-q")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    # local-path "repo_url" is supported for fixtures and offline use
    git(dest.parent, "clone", "-q", task.repo_url, str(dest))
    return dest


def export_tree(task: TaskSpec, sha: str, dest: Path) -> None:
    up = upstream_clone(task)
    dest.mkdir(parents=True, exist_ok=True)
    arc = subprocess.run(["git", "archive", sha], cwd=up, capture_output=True)
    if arc.returncode != 0:
        raise WorkspaceError(f"git archive {sha}: {arc.stderr.decode()[:300]}")
    subprocess.run(["tar", "-x", "-C", str(dest)], input=arc.stdout, check=True)


def init_fresh_repo(repo: Path) -> str:
    git(repo, "init", "-q", "-b", "main")
    # build/test byproducts must never leak into the agent's diff
    ex = repo / ".git" / "info" / "exclude"
    ex.parent.mkdir(parents=True, exist_ok=True)
    ex.write_text("*.egg-info/\n__pycache__/\n*.pyc\n.pytest_cache/\nbuild/\n.eggs/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    return git(repo, "rev-parse", "HEAD").strip()


def apply_patch(repo: Path, patch: str, *, check_only: bool = False) -> None:
    if not patch.strip():
        return
    if not patch.endswith("\n"):
        patch += "\n"
    args = ["apply", "--whitespace=nowarn", "--index"] if not check_only else ["apply", "--check"]
    git(repo, *args, "-", input=patch)


def prepare_workspace(task: TaskSpec, repo_dir: Path, *, sha: Optional[str] = None,
                      adapt_tests: bool = True) -> str:
    """Materialise `sha` (default base_sha) into repo_dir as a one-commit repo. Returns HEAD."""
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    export_tree(task, sha or task.base_sha, repo_dir)
    if adapt_tests and task.test_adaptation_patch:
        # init first so --index works, then commit adaptation as part of the base
        git(repo_dir, "init", "-q", "-b", "main")
        git(repo_dir, "add", "-A")
        git(repo_dir, "commit", "-q", "-m", "upstream")
        apply_patch(repo_dir, task.test_adaptation_patch)
        shutil.rmtree(repo_dir / ".git")
    if (repo_dir / ".git").exists():
        shutil.rmtree(repo_dir / ".git")
    return init_fresh_repo(repo_dir)


class Workspace:
    """File operations for the agent's tools. All paths are confined to the repo."""

    def __init__(self, repo_dir: Path, base_commit: str):
        self.root = repo_dir.resolve()
        self.base = base_commit
        self.best_commit = base_commit
        self.denied_edits: list[str] = []   # attempts to edit protected paths (logged)

    # ---- path safety -------------------------------------------------------------------
    def resolve(self, rel: str) -> Path:
        if not rel or rel.startswith("/") or "\x00" in rel:
            raise WorkspaceError(f"path must be repo-relative: {rel!r}")
        p = (self.root / rel).resolve()
        if self.root != p and self.root not in p.parents:
            raise WorkspaceError(f"path escapes the repository: {rel}")
        parts = p.relative_to(self.root).parts
        if ".git" in parts:
            raise WorkspaceError("access to .git is not allowed")
        return p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    # ---- reads -------------------------------------------------------------------------
    def list_files(self, subdir: str = ".", max_entries: int = 300) -> str:
        base = self.resolve(subdir)
        if not base.exists():
            raise WorkspaceError(f"path not found: {subdir} (use '.' for the repo root)")
        out = []
        for dp, dns, fns in os.walk(base):
            dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
            for f in sorted(fns):
                out.append(self.rel(Path(dp) / f))
                if len(out) >= max_entries:
                    return "\n".join(out) + f"\n...[truncated at {max_entries}]"
        return "\n".join(out) or "(empty)"

    def read_file(self, path: str, start: int = 1, end: Optional[int] = None,
                  max_chars: int = 12000) -> str:
        p = self.resolve(path)
        if not p.is_file():
            raise WorkspaceError(f"not a file: {path}")
        data = p.read_bytes()
        if b"\x00" in data[:4096]:
            raise WorkspaceError("binary file")
        lines = data.decode("utf-8", errors="replace").splitlines()
        start = max(1, int(start))
        end = min(len(lines), int(end)) if end else min(len(lines), start + 399)
        chunk = [f"{i + 1:5d}  {lines[i]}" for i in range(start - 1, end)]
        text = "\n".join(chunk)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated; narrow start/end]"
        return f"{path} (lines {start}-{end} of {len(lines)})\n{text}"

    def search(self, pattern: str, path: str = ".", glob: Optional[str] = None,
               max_results: int = 60, context: int = 0) -> str:
        base = self.resolve(path)
        if not base.exists():
            raise WorkspaceError(f"path not found: {path} (use '.' for the repo root)")
        note = ""
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))      # weak models forget to escape ( . [ : search it literally
            note = "(note: not a valid regex, so it was searched as literal text)\n"
        from fnmatch import fnmatch
        res, n = [], 0
        for dp, dns, fns in os.walk(base):
            dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
            for f in sorted(fns):
                if glob and not fnmatch(f, glob):
                    continue
                fp = Path(dp) / f
                try:
                    raw = fp.read_bytes()
                except OSError:
                    continue
                if b"\x00" in raw[:2048] or len(raw) > 2_000_000:
                    continue
                lines = raw.decode("utf-8", errors="replace").splitlines()
                for i, line in enumerate(lines):
                    if rx.search(line):
                        lo, hi = max(0, i - context), min(len(lines), i + context + 1)
                        for j in range(lo, hi):
                            sep = ":" if j == i else "-"
                            res.append(f"{self.rel(fp)}{sep}{j + 1}{sep}{lines[j][:300]}")
                        n += 1
                        if n >= max_results:
                            return note + "\n".join(res) + f"\n...[stopped at {max_results} matches]"
        return note + ("\n".join(res) or "(no matches)")

    # ---- writes ------------------------------------------------------------------------
    def _guard_write(self, path: str) -> Path:
        p = self.resolve(path)
        rel = self.rel(p)
        if is_protected(rel):
            self.denied_edits.append(rel)
            raise WorkspaceError(
                f"{rel} is a protected test/config file; edits are not allowed. "
                "Fix the library/source code instead of the tests.")
        return p

    def edit_file(self, path: str, old: str, new: str) -> str:
        p = self._guard_write(path)
        if not p.is_file():
            raise WorkspaceError(f"not a file: {path}")
        text = p.read_text(encoding="utf-8")
        cnt = text.count(old)
        if old == "":
            raise WorkspaceError("`old` must be non-empty (use write_file for new files)")
        if cnt == 0 and "\\n" in old:
            # weak models double-escape newlines (literal backslash-n). Retry unescaped, only if it then matches once.
            old2, new2 = _unescape(old), _unescape(new)
            if text.count(old2) == 1:
                old, new, cnt = old2, new2, 1
        if cnt == 0:
            fm = _fuzzy_matches(text, old)
            if len(fm) == 1:
                i, n, delta = fm[0]
                newl = _reindent(new.strip("\n").split("\n"), delta)
                if newl is not None:
                    lines = text.split("\n")
                    lines[i:i + n] = newl
                    p.write_text("\n".join(lines), encoding="utf-8")
                    return f"edited {path} (matched lines {i + 1}-{i + n} ignoring whitespace differences)"
            if len(fm) > 1:
                at = ", ".join(str(i + 1) for i, _, _ in fm[:6])
                raise WorkspaceError(f"`old` text matches {len(fm)} places ignoring whitespace (starting at lines {at}); "
                                     "include more context to make it unique.")
            raise WorkspaceError("`old` text not found. Re-read the file; whitespace must match exactly." + _closest(text, old))
        if cnt > 1:
            at = ", ".join(str(n) for n in _match_lines(text, old))
            raise WorkspaceError(f"`old` text matches {cnt} places (starting at lines {at}); include more context to make it unique.")
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"edited {path}"

    def write_file(self, path: str, content: str) -> str:
        p = self._guard_write(path)
        if p.exists() and p.is_dir():
            raise WorkspaceError("is a directory")
        if "\n" not in content and "\\n" in content:
            content = _unescape(content)            # double-escaped newlines from a weak model
        if p.is_file():
            prev = p.read_text(encoding="utf-8", errors="replace")
            if len(prev) > 200 and len(content) < 0.3 * len(prev):
                raise WorkspaceError(f"refusing to overwrite {path}: new content is far shorter than the existing "
                                     f"file ({len(content)} vs {len(prev)} chars). Use edit_file for small changes.")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {path} ({len(content)} chars)"

    # ---- git state ---------------------------------------------------------------------
    def diff(self) -> str:
        git(self.root, "add", "-A")
        return git(self.root, "diff", "--cached", "--binary", self.base)

    def changed_files(self) -> list[str]:
        git(self.root, "add", "-A")
        out = git(self.root, "diff", "--cached", "--name-only", self.base)
        return [l for l in out.splitlines() if l]

    def snapshot(self) -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "snapshot")
        self.best_commit = git(self.root, "rev-parse", "HEAD").strip()
        return self.best_commit

    def restore_best(self) -> None:
        git(self.root, "reset", "-q", "--hard", self.best_commit)
        git(self.root, "clean", "-fdq")
