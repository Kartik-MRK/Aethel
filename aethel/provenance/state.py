"""Durable, atomic worker state shared with the read-only dashboard."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, key TEXT, payload TEXT NOT NULL, PRIMARY KEY(kind, key))")
            with db:
                yield db
        finally:
            db.close()

    def get(self, kind: str, key: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM records WHERE kind=? AND key=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def records(self, kind: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM records WHERE kind=? ORDER BY key", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def put(self, kind: str, key: str, record: dict) -> dict:
        record = {**record, "updated_at": utc_now()}
        with self.connection() as db:
            db.execute(
                "INSERT INTO records(kind,key,payload) VALUES(?,?,?) ON CONFLICT(kind,key) DO UPDATE SET payload=excluded.payload",
                (kind, key, json.dumps(record, sort_keys=True, allow_nan=False)),
            )
        return record
