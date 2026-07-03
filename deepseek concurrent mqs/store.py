"""
SQLite job store. This is what makes the pipeline resumable: if your process
dies (or you Ctrl+C it) at report 600/1000, re-running the script picks up
exactly where it left off instead of re-generating (and re-paying for) the
first 600.
"""

import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    system_prompt TEXT NOT NULL,
    user_prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    report TEXT,
    error TEXT,
    updated_at REAL
);
"""


class JobStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        with self._conn() as conn:
            conn.execute(SCHEMA)
            # Any job stuck "running" from a previous crashed run goes back to pending.
            conn.execute("UPDATE jobs SET status = 'pending' WHERE status = 'running'")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")  # lets concurrent workers write safely
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def seed(self, jobs: list[dict]):
        """jobs: [{"id": ..., "system_prompt": ..., "user_prompt": ...}, ...]
        Ignores jobs whose id already exists (safe to re-run seeding)."""
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO jobs (id, system_prompt, user_prompt, status, updated_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                [(j["id"], j["system_prompt"], j["user_prompt"], time.time()) for j in jobs],
            )

    def get_pending(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, system_prompt, user_prompt FROM jobs WHERE status = 'pending'"
            ).fetchall()
        return [{"id": r[0], "system_prompt": r[1], "user_prompt": r[2]} for r in rows]

    def mark_running(self, job_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status='running', attempts = attempts + 1, updated_at=? WHERE id=?",
                (time.time(), job_id),
            )

    def mark_done(self, job_id: str, report: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status='done', report=?, error=NULL, updated_at=? WHERE id=?",
                (report, time.time(), job_id),
            )

    def mark_failed(self, job_id: str, error: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status='failed', error=?, updated_at=? WHERE id=?",
                (error, time.time(), job_id),
            )

    def mark_pending(self, job_id: str):
        """Requeue for retry."""
        with self._conn() as conn:
            conn.execute("UPDATE jobs SET status='pending', updated_at=? WHERE id=?",
                         (time.time(), job_id))

    def counts(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
        return dict(rows)

    def export_done(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, report FROM jobs WHERE status='done'"
            ).fetchall()
        return [{"id": r[0], "report": r[1]} for r in rows]
