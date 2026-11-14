"""SQLite persistence: previous Canvas state (for change detection), notifications, approvals."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(
  kind TEXT, course_id TEXT, key TEXT, data TEXT,
  PRIMARY KEY(kind, course_id, key));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS http_cache(url TEXT PRIMARY KEY, etag TEXT, body TEXT, links TEXT);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT UNIQUE,
  course_name TEXT, event_type TEXT, priority TEXT, title TEXT, what_changed TEXT,
  when_text TEXT, url TEXT, why TEXT, details TEXT, created_at REAL, read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS deliveries(
  notification_id INTEGER, channel TEXT, status TEXT, attempts INTEGER DEFAULT 0, last_error TEXT,
  PRIMARY KEY(notification_id, channel));
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT UNIQUE, payload TEXT);
CREATE TABLE IF NOT EXISTS sync_status(
  source TEXT PRIMARY KEY, status TEXT, detail TEXT, at REAL);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, payload TEXT, status TEXT,
  created_at REAL, decided_at REAL);
"""


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._lock = threading.RLock()

    @contextmanager
    def tx(self):
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    # ---- meta
    def get_meta(self, k: str, default=None):
        with self._lock:
            r = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def set_meta(self, k: str, v) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, json.dumps(v)))

    # ---- snapshots
    def load_snapshots(self, kind: str, course_id: str) -> dict[str, dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT key, data FROM snapshots WHERE kind=? AND course_id=?", (kind, course_id)
            ).fetchall()
        return {r["key"]: json.loads(r["data"]) for r in rows}

    # ---- HTTP cache (conditional requests => incremental sync)
    def get_cache(self, url: str):
        with self._lock:
            return self.conn.execute("SELECT * FROM http_cache WHERE url=?", (url,)).fetchone()

    def put_cache(self, url: str, etag: str, body: str, links: str = "") -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO http_cache VALUES(?,?,?,?)", (url, etag, body, links))

    # ---- sync status
    def set_status(self, source: str, status: str, detail: str = "") -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO sync_status VALUES(?,?,?,?)", (source, status, detail, time.time())
            )

    def statuses(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM sync_status ORDER BY source")]

    # ---- approvals (gate for anything that would change the Canvas account)
    def add_approval(self, action: str, payload: dict) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO approvals(action,payload,status,created_at) VALUES(?,?,?,?)",
                (action, json.dumps(payload), "pending", time.time()),
            )
            return cur.lastrowid

    def decide_approval(self, approval_id: int, approved: bool) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE approvals SET status=?, decided_at=? WHERE id=? AND status='pending'",
                ("approved" if approved else "rejected", time.time(), approval_id),
            )

    def get_approval(self, approval_id: int):
        with self._lock:
            return self.conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
