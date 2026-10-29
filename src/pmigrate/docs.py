"""Version-pinned migration-docs lookup with section-level results and provenance.

Deliberately simple (BM25 over markdown sections) so we can *measure* whether retrieval helps
before adding embeddings / vector stores. Every result carries its source file, pinned git tag,
heading path and a permalink so a human can check what the agent was shown.

Version awareness: the corpus is fetched at the git tag matching the task's target pydantic
version, and changelog sections for releases newer than that version are filtered out.
"""
from __future__ import annotations

import math
import re
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .config import DOCS_DIR

RAW = "https://raw.githubusercontent.com/pydantic/pydantic/{tag}/{path}"
BLOB = "https://github.com/pydantic/pydantic/blob/{tag}/{path}"
SOURCES = {"migration": "docs/migration.md", "changelog": "HISTORY.md"}


def fetch_docs(tag: str = "v2.10.0", dest: Path = DOCS_DIR) -> list[Path]:
    out = []
    d = dest / tag
    d.mkdir(parents=True, exist_ok=True)
    for name, path in SOURCES.items():
        url = RAW.format(tag=tag, path=path)
        with urllib.request.urlopen(url, timeout=60) as r:
            data = r.read()
        f = d / f"{name}.md"
        f.write_bytes(data)
        out.append(f)
    return out


def _vtuple(v: str) -> tuple:
    m = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", v)
    return tuple(int(x or 0) for x in m.groups()) if m else (0, 0, 0)


@dataclass
class Section:
    source: str            # migration | changelog
    tag: str               # pinned git tag the file was fetched at
    heading: str           # "Breaking Changes > Changes to pydantic.BaseModel"
    text: str
    line: int              # 1-based line of the heading in the source file
    release: Optional[str] = None   # changelog: version this section describes

    @property
    def url(self) -> str:
        return f"{BLOB.format(tag=self.tag, path=SOURCES[self.source])}#L{self.line}"


@dataclass
class Hit:
    score: float
    source: str
    tag: str
    heading: str
    url: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


_TOK = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def tokenize(s: str) -> list[str]:
    toks = []
    for w in _TOK.findall(s):
        lw = w.lower()
        toks.append(lw)
        if "_" in lw:                       # parse_obj -> parse_obj, parse, obj
            toks.extend(p for p in lw.split("_") if p)
    return toks


def split_sections(md: str, source: str, tag: str, max_chars: int = 3500) -> list[Section]:
    lines = md.splitlines()
    heads = []           # (line_idx, level, title)
    in_code = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith("```"):
            in_code = not in_code
        if in_code:
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    secs: list[Section] = []
    stack: list[tuple[int, str]] = []
    for idx, (i, lvl, title) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        stack.append((lvl, title))
        body = "\n".join(lines[i + 1:end]).strip()
        if not body:
            continue
        release = None
        if source == "changelog":
            m = re.match(r"v?(\d+\.\d+(?:\.\d+)?)", title)
            if m:
                release = m.group(1)
            else:   # sub-heading of a release: inherit
                for _, t in reversed(stack[:-1]):
                    mm = re.match(r"v?(\d+\.\d+(?:\.\d+)?)", t)
                    if mm:
                        release = mm.group(1)
                        break
        path = " > ".join(t for _, t in stack)
        # split very long bodies on paragraph boundaries
        chunks, cur = [], ""
        for para in body.split("\n\n"):
            if len(cur) + len(para) > max_chars and cur:
                chunks.append(cur)
                cur = ""
            cur += para + "\n\n"
        if cur.strip():
            chunks.append(cur)
        for c, chunk in enumerate(chunks):
            secs.append(Section(source, tag, path + (f" (part {c + 1})" if len(chunks) > 1 else ""),
                                chunk.strip(), i + 1, release))
    return secs


class MigrationDocs:
    def __init__(self, tag: str = "v2.10.0", docs_dir: Path = DOCS_DIR, target_version: Optional[str] = None):
        self.tag = tag
        self.target = _vtuple(target_version or tag)
        base = docs_dir / tag
        if not base.exists():
            raise FileNotFoundError(f"docs for {tag} not found at {base}; run `pmigrate fetch-docs --tag {tag}`")
        self.sections: list[Section] = []
        for name in SOURCES:
            f = base / f"{name}.md"
            if f.exists():
                self.sections += split_sections(f.read_text(encoding="utf-8"), name, tag)
        # version-aware filter: drop changelog entries newer than the pinned target version, and
        # pre-2.0 history (irrelevant to a v1->v2 migration).
        keep = []
        for s in self.sections:
            if s.source == "changelog":
                if not s.release:
                    continue
                v = _vtuple(s.release)
                if v > self.target or v < (2, 0, 0):
                    continue
            keep.append(s)
        self.sections = keep
        self._build()

    def _build(self) -> None:
        self.tf: list[Counter] = []
        self.len: list[int] = []
        df: Counter = Counter()
        for s in self.sections:
            # heading terms weigh more: repeat them
            toks = tokenize(s.heading) * 3 + tokenize(s.text)
            c = Counter(toks)
            self.tf.append(c)
            self.len.append(len(toks))
            df.update(c.keys())
        n = len(self.sections)
        self.avg = sum(self.len) / max(1, n)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, k: int = 3, *, source: Optional[str] = None,
               max_chars: int = 1800) -> list[Hit]:
        q = tokenize(query)
        k1, b = 1.5, 0.75
        scored = []
        for i, s in enumerate(self.sections):
            if source and s.source != source:
                continue
            tf, L = self.tf[i], self.len[i]
            sc = 0.0
            for t in set(q):
                f = tf.get(t, 0)
                if f:
                    sc += self.idf.get(t, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * L / self.avg))
            if s.source == "migration":
                sc *= 1.25    # prefer the curated guide over changelog noise
            if sc > 0:
                scored.append((sc, i))
        scored.sort(reverse=True)
        hits = []
        for sc, i in scored[:k]:
            s = self.sections[i]
            text = s.text if len(s.text) <= max_chars else s.text[:max_chars] + "\n...[truncated; see url]"
            hits.append(Hit(round(sc, 3), s.source, s.tag, s.heading, s.url, text))
        return hits


def format_hits(hits: list[Hit]) -> str:
    if not hits:
        return "No matching documentation found. Try different keywords (API names, e.g. 'parse_obj', 'validator')."
    parts = []
    for n, h in enumerate(hits, 1):
        parts.append(f"[{n}] {h.heading}\n    source: {h.source} @ pydantic {h.tag} | {h.url}\n{h.text}")
    return "\n\n".join(parts)
