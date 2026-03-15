"""Event Buffer — SQLite-based event queue for autonomous mode.

When the robot operates in AUTONOMOUS mode, safety events, Q&A logs,
and telemetry snapshots are written here instead of being sent to the
workstation. On Wi-Fi reconnect, they are batch-uploaded and cleared.

Schema:
    CREATE TABLE events (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts        REAL    NOT NULL,  -- UNIX timestamp
        type      TEXT    NOT NULL,  -- "safety_event" | "qa_log" | "telemetry"
        data      TEXT    NOT NULL,  -- JSON payload
        synced    INTEGER NOT NULL DEFAULT 0
    )

Retention: 7 days max age; 100MB max file size (oldest events pruned first).

Usage:
    buf = EventBuffer("/data/event_buffer.db")
    buf.write_event("safety_event", {"rule_id": "BATT-001", "battery": 8.3})
    pending = buf.get_pending(limit=500)
    buf.mark_synced([e["id"] for e in pending])
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL    NOT NULL,
    type   TEXT    NOT NULL,
    data   TEXT    NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_ts     ON events (ts);
CREATE INDEX IF NOT EXISTS events_synced ON events (synced);
"""

MAX_AGE_DAYS = 7
MAX_SIZE_MB  = 100


class EventBuffer:
    """Thread-safe SQLite event queue."""

    def __init__(self, db_path: str = "/data/event_buffer.db"):
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_event(self, event_type: str, data: dict) -> int:
        """Write one event. Returns row id."""
        payload = json.dumps(data, ensure_ascii=False)
        with self._conn() as con:
            cur = con.execute(
                "INSERT INTO events (ts, type, data) VALUES (?, ?, ?)",
                (time.time(), event_type, payload),
            )
            return cur.lastrowid

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_pending(self, limit: int = 500) -> List[dict]:
        """Return up to *limit* unsynced events, oldest first."""
        with self._conn() as con:
            rows = con.execute(
                "SELECT id, ts, type, data FROM events "
                "WHERE synced = 0 ORDER BY ts ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "type": r[2], "data": json.loads(r[3])}
            for r in rows
        ]

    def pending_count(self) -> int:
        with self._conn() as con:
            return con.execute(
                "SELECT COUNT(*) FROM events WHERE synced = 0"
            ).fetchone()[0]

    # ------------------------------------------------------------------
    # Sync lifecycle
    # ------------------------------------------------------------------

    def mark_synced(self, ids: List[int]) -> int:
        """Mark events as synced. Returns number of rows updated."""
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with self._conn() as con:
            cur = con.execute(
                f"UPDATE events SET synced = 1 WHERE id IN ({placeholders})",
                ids,
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup_old(self) -> int:
        """Delete events older than MAX_AGE_DAYS. Returns deleted count."""
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        with self._conn() as con:
            cur = con.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            deleted = cur.rowcount
            con.execute("VACUUM")
        if deleted:
            logger.info("EventBuffer: pruned %d events older than %d days",
                        deleted, MAX_AGE_DAYS)
        return deleted

    def enforce_size_limit(self) -> int:
        """Delete oldest events if db exceeds MAX_SIZE_MB. Returns deleted count."""
        db_size_mb = Path(self._path).stat().st_size / (1024 * 1024) if Path(self._path).exists() else 0
        if db_size_mb < MAX_SIZE_MB:
            return 0

        # Delete oldest 20% to make room
        with self._conn() as con:
            total = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            to_delete = max(1, total // 5)
            # Get IDs of oldest rows
            ids = [r[0] for r in con.execute(
                "SELECT id FROM events ORDER BY ts ASC LIMIT ?", (to_delete,)
            ).fetchall()]
            if ids:
                placeholders = ",".join("?" * len(ids))
                cur = con.execute(
                    f"DELETE FROM events WHERE id IN ({placeholders})", ids
                )
                deleted = cur.rowcount
                con.execute("VACUUM")
                logger.warning("EventBuffer: size limit hit (%.1fMB), pruned %d events",
                               db_size_mb, deleted)
                return deleted
        return 0

    def stats(self) -> dict:
        with self._conn() as con:
            total  = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            synced = con.execute("SELECT COUNT(*) FROM events WHERE synced=1").fetchone()[0]
        size_mb = Path(self._path).stat().st_size / (1024 * 1024) if Path(self._path).exists() else 0
        return {
            "total_events":   total,
            "pending_events": total - synced,
            "synced_events":  synced,
            "db_size_mb":     round(size_mb, 2),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self):
        with self._conn() as con:
            con.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        return con
