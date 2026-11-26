"""Hub storage (SQLite). Only metadata: organizations, people, roles, licenses and audit events; never source code."""

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    plan TEXT NOT NULL,
    seats INTEGER NOT NULL,
    period_end REAL NOT NULL,
    github_token TEXT,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES orgs(id),
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    github_login TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    org_id INTEGER NOT NULL REFERENCES orgs(id),
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    github_repo TEXT,
    created REAL NOT NULL,
    PRIMARY KEY (org_id, id)
);
CREATE TABLE IF NOT EXISTS memberships (
    org_id INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    role TEXT NOT NULL,
    PRIMARY KEY (org_id, project_id, user_id)
);
CREATE TABLE IF NOT EXISTS invitations (
    code TEXT PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES orgs(id),
    email TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    created REAL NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL,
    user_id INTEGER,
    user_name TEXT,
    project_id TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    source TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_org_ts ON audit(org_id, ts);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def query(self, sql: str, args: tuple = ()) -> list:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def execute(self, sql: str, args: tuple = ()) -> int:
        with self._lock, self._conn:
            return self._conn.execute(sql, args).lastrowid

    def audit(self, org_id: int, user: dict | None, action: str, project_id: str | None = None, detail: str = "",
              source: str = "hub") -> None:
        self.execute(
            "INSERT INTO audit (org_id, user_id, user_name, project_id, action, detail, source, ts) VALUES (?,?,?,?,?,?,?,?)",
            (org_id, user["id"] if user else None, user["name"] if user else None, project_id, action, detail[:500], source, time.time()),
        )
