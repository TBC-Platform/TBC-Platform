# SPDX-License-Identifier: MIT
"""Local, on-disk memory: conversation history and durable user facts.

SQLite, because it is in the standard library, survives a restart, and is a
single file the owner can inspect or delete. Nothing here ever leaves the
machine.

Two tables:

* ``turns``  - the rolling conversation log, one row per exchange.
* ``facts``  - durable preferences ("my name is Sam", "I go to bed at eleven"),
  which are re-injected into the system prompt on every turn so they outlive
  history truncation.

All public methods are ``async`` and run the blocking sqlite3 work in a thread,
so a slow disk cannot stall the audio pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device      TEXT NOT NULL,
    ts          REAL NOT NULL,
    user_text   TEXT NOT NULL,
    robot_text  TEXT NOT NULL,
    latency_ms  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_turns_device_ts ON turns(device, ts DESC);

CREATE TABLE IF NOT EXISTS facts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    device   TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    ts       REAL NOT NULL,
    UNIQUE(device, key)
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    device  TEXT NOT NULL,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    detail  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
"""


@dataclass(slots=True)
class Turn:
    user_text: str
    robot_text: str
    ts: float


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        # sqlite3 connections are not safe to share across threads without
        # care; one lock keeps every access serialised, which is plenty for a
        # workload of a few writes per minute.
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await asyncio.to_thread(self._connect)
        log.info("memory store at %s", self.path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL keeps a reader (e.g. you poking at the file with sqlite3) from
        # blocking the robot mid-conversation.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        return conn

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("MemoryStore.open() was never awaited")
        return self._conn

    # ------------------------------ history -------------------------------

    async def add_turn(self, device: str, user_text: str, robot_text: str,
                       latency_ms: int = 0) -> None:
        conn = self._require()

        def _write() -> None:
            conn.execute(
                "INSERT INTO turns(device, ts, user_text, robot_text, latency_ms)"
                " VALUES (?,?,?,?,?)",
                (device, time.time(), user_text, robot_text, latency_ms),
            )
            conn.commit()

        async with self._lock:
            await asyncio.to_thread(_write)

    async def recent_turns(self, device: str, limit: int = 8) -> list[Turn]:
        """Most recent turns, oldest first (ready to append to a chat history)."""
        conn = self._require()

        def _read() -> list[Turn]:
            rows = conn.execute(
                "SELECT user_text, robot_text, ts FROM turns WHERE device=?"
                " ORDER BY ts DESC LIMIT ?",
                (device, limit),
            ).fetchall()
            return [Turn(r["user_text"], r["robot_text"], r["ts"]) for r in reversed(rows)]

        async with self._lock:
            return await asyncio.to_thread(_read)

    async def clear_history(self, device: str) -> int:
        conn = self._require()

        def _delete() -> int:
            cur = conn.execute("DELETE FROM turns WHERE device=?", (device,))
            conn.commit()
            return cur.rowcount

        async with self._lock:
            return await asyncio.to_thread(_delete)

    async def prune(self, device: str, keep: int = 500) -> None:
        """Trims history so the file cannot grow without bound."""
        conn = self._require()

        def _prune() -> None:
            conn.execute(
                "DELETE FROM turns WHERE device=? AND id NOT IN ("
                "  SELECT id FROM turns WHERE device=? ORDER BY ts DESC LIMIT ?)",
                (device, device, keep),
            )
            conn.commit()

        async with self._lock:
            await asyncio.to_thread(_prune)

    # ------------------------------- facts --------------------------------

    async def set_fact(self, device: str, key: str, value: str) -> None:
        conn = self._require()

        def _write() -> None:
            conn.execute(
                "INSERT INTO facts(device, key, value, ts) VALUES (?,?,?,?)"
                " ON CONFLICT(device, key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                (device, key.strip().lower(), value.strip(), time.time()),
            )
            conn.commit()

        async with self._lock:
            await asyncio.to_thread(_write)

    async def get_facts(self, device: str, limit: int = 20) -> list[str]:
        """Facts rendered as "key: value" strings for the system prompt."""
        conn = self._require()

        def _read() -> list[str]:
            rows = conn.execute(
                "SELECT key, value FROM facts WHERE device=? ORDER BY ts DESC LIMIT ?",
                (device, limit),
            ).fetchall()
            return [f"{r['key']}: {r['value']}" for r in rows]

        async with self._lock:
            return await asyncio.to_thread(_read)

    async def forget(self, device: str, key: str | None = None) -> int:
        conn = self._require()

        def _delete() -> int:
            if key is None:
                cur = conn.execute("DELETE FROM facts WHERE device=?", (device,))
            else:
                cur = conn.execute(
                    "DELETE FROM facts WHERE device=? AND key=?", (device, key.strip().lower())
                )
            conn.commit()
            return cur.rowcount

        async with self._lock:
            return await asyncio.to_thread(_delete)

    # ------------------------------- events -------------------------------

    async def log_event(self, device: str, kind: str, detail: str) -> None:
        """Audit trail. Every smart home action lands here, which is what makes
        "what did the robot actually do at 2am" an answerable question."""
        conn = self._require()

        def _write() -> None:
            conn.execute(
                "INSERT INTO events(device, ts, kind, detail) VALUES (?,?,?,?)",
                (device, time.time(), kind, detail),
            )
            conn.commit()

        async with self._lock:
            await asyncio.to_thread(_write)

    async def recent_events(self, limit: int = 50) -> list[dict]:
        conn = self._require()

        def _read() -> list[dict]:
            rows = conn.execute(
                "SELECT device, ts, kind, detail FROM events ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

        async with self._lock:
            return await asyncio.to_thread(_read)
