"""SQLite logging: one row per attempt plus a fine-grained event stream (logging starts Week 2)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  experiment TEXT, task_id TEXT, variant TEXT, attempt INTEGER, split TEXT,
  model TEXT, backend TEXT, prompt_hash TEXT, code_version TEXT,
  started_at REAL, ended_at REAL, end_reason TEXT,
  success INTEGER, tests_pass INTEGER, native_ok INTEGER, policy_ok INTEGER,
  required_total INTEGER, required_failing INTEGER, regressions INTEGER,
  failure_category TEXT,
  steps INTEGER, tool_calls INTEGER, test_runs INTEGER, reverts INTEGER,
  input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
  cost_usd REAL, agent_wall_s REAL, verify_wall_s REAL,
  diff TEXT, plan TEXT, summary TEXT, verification_json TEXT, config_json TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_exp ON runs(experiment, task_id, variant);
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT, seq INTEGER, ts REAL, type TEXT, payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
CREATE TABLE IF NOT EXISTS transcripts (run_id TEXT PRIMARY KEY, json TEXT);
"""


# columns added after the first schema version (kept in sync for old databases)
NEW_COLS = {"track": "TEXT", "backend_used": "TEXT", "required_not_run": "INTEGER", "collection_failed": "INTEGER",
            "provisional": "INTEGER"}


class DB:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self.conn() as c:
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(runs)")}
            for col, typ in NEW_COLS.items():
                if col not in have:
                    c.execute(f"ALTER TABLE runs ADD COLUMN {col} {typ}")

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=60)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def insert_run(self, row: dict) -> None:
        cols = ", ".join(row)
        qs = ", ".join("?" for _ in row)
        with self._lock, self.conn() as c:
            c.execute(f"INSERT OR REPLACE INTO runs ({cols}) VALUES ({qs})", list(row.values()))

    def add_events(self, run_id: str, events: list) -> None:
        with self._lock, self.conn() as c:
            c.executemany("INSERT INTO events VALUES (?,?,?,?,?)",
                          [(run_id, i, e["ts"], e["type"], json.dumps(e["payload"], default=str))
                           for i, e in enumerate(events)])

    def save_transcript(self, run_id: str, transcript: list) -> None:
        with self._lock, self.conn() as c:
            c.execute("INSERT OR REPLACE INTO transcripts VALUES (?,?)", (run_id, json.dumps(transcript)))

    def load_transcript(self, run_id: str) -> list:
        with self.conn() as c:
            r = c.execute("SELECT json FROM transcripts WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(r["json"]) if r else []

    def done(self, experiment: str, task_id: str, variant: str, attempt: int) -> bool:
        with self.conn() as c:
            r = c.execute("SELECT 1 FROM runs WHERE experiment=? AND task_id=? AND variant=? AND attempt=? "
                          "AND end_reason != 'error'", (experiment, task_id, variant, attempt)).fetchone()
        return r is not None

    def runs(self, experiment: Optional[str] = None, split: Optional[str] = None) -> list[sqlite3.Row]:
        q, a = "SELECT * FROM runs WHERE 1=1", []
        if experiment:
            q += " AND experiment=?"; a.append(experiment)
        if split:
            q += " AND split=?"; a.append(split)
        with self.conn() as c:
            return c.execute(q, a).fetchall()
