"""Feedback store: the chemist's accept / reject decision and comment for each suggestion.

One SQLite file (GMS_DATA_DIR/feedback.db). Each row keeps what is needed to analyse the
decision later: the suggestion, its evidence and check result, and the reaction it was made
for. No personal data is stored; `session` is a random id that only groups one visit.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

import gms

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_utc TEXT NOT NULL,
    app_version TEXT NOT NULL,
    session TEXT NOT NULL,
    reaction_hash TEXT NOT NULL,
    reaction_name TEXT,
    reaction_class TEXT,
    suggestion_id TEXT,
    rule_id TEXT,
    kind TEXT,
    title TEXT,
    status TEXT,
    confidence TEXT,
    decision TEXT NOT NULL,
    comment TEXT,
    greenness_gain REAL,
    reaction_json TEXT,
    suggestion_json TEXT
)"""
COLUMNS = ["id", "created_utc", "app_version", "session", "reaction_hash", "reaction_name", "reaction_class",
           "suggestion_id", "rule_id", "kind", "title", "status", "confidence", "decision", "comment",
           "greenness_gain", "reaction_json", "suggestion_json"]
DECISIONS = ("accept", "reject", "comment")
MAX_COMMENT = 2000
_lock = threading.Lock()


def reaction_hash(reaction_dict: dict) -> str:
    return hashlib.sha1(json.dumps(reaction_dict, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.execute(_SCHEMA)
    return con


def record(path: Path, session: str, reaction_dict: dict, decision: str, comment: str = "",
           suggestion: Optional[dict] = None) -> int:
    """Store one decision. `suggestion` is Candidate.summary() (None for a general comment)."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    s = suggestion or {}
    row = (
        datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), gms.__version__, session,
        reaction_hash(reaction_dict), reaction_dict.get("name"), reaction_dict.get("reaction_class"),
        s.get("id"), s.get("rule_id"), s.get("kind"), s.get("title"), s.get("status"), s.get("confidence"),
        decision, (comment or "").strip()[:MAX_COMMENT], s.get("greenness_gain"),
        json.dumps(reaction_dict, default=str), json.dumps(s, default=str) if s else None)
    with _lock:
        con = _connect(Path(path))
        try:
            cur = con.execute(f"INSERT INTO feedback ({', '.join(COLUMNS[1:])}) VALUES ({', '.join('?' * 17)})", row)
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def rows(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with _lock:
        con = _connect(Path(path))
        try:
            cur = con.execute(f"SELECT {', '.join(COLUMNS)} FROM feedback ORDER BY id")
            return [dict(zip(COLUMNS, r)) for r in cur.fetchall()]
        finally:
            con.close()


def to_csv(path: Path) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    w.writeheader()
    w.writerows(rows(path))
    return buf.getvalue().encode("utf-8")
