"""SQLite persistence tuned for long-running concurrent reads/writes."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from threading import Lock


class EventStore:
    def __init__(self, db_path):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.lock = Lock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    node_id TEXT,
                    track_id INTEGER,
                    details TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS csi_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    node_id TEXT NOT NULL,
                    amplitude TEXT NOT NULL,
                    phase TEXT,
                    rssi REAL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS node_positions (
                    node_id TEXT PRIMARY KEY,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    z REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, timestamp);
                CREATE INDEX IF NOT EXISTS idx_csi_time ON csi_snapshots(timestamp);
                """
            )

    def log_event(self, event_type, node_id="", track_id=None, details=None, timestamp=None):
        ts = float(time.time() if timestamp is None else timestamp)
        payload = json.dumps(details, separators=(",", ":"), default=str) if details is not None else None
        with self.lock, self._connection() as conn:
            conn.execute(
                "INSERT INTO events (timestamp, event_type, node_id, track_id, details) VALUES (?, ?, ?, ?, ?)",
                (ts, str(event_type), str(node_id or ""), track_id, payload),
            )

    def log_csi(self, node_id, amplitude, phase, rssi, timestamp=None):
        ts = float(time.time() if timestamp is None else timestamp)
        with self.lock, self._connection() as conn:
            conn.execute(
                "INSERT INTO csi_snapshots (timestamp, node_id, amplitude, phase, rssi) VALUES (?, ?, ?, ?, ?)",
                (
                    ts,
                    str(node_id),
                    json.dumps(amplitude, separators=(",", ":")),
                    json.dumps(phase, separators=(",", ":")) if phase is not None else None,
                    rssi,
                ),
            )

    def get_recent_events(self, limit=100, event_type=None):
        limit = max(1, min(int(limit), 1000))
        with self._connection() as conn:
            conn.row_factory = sqlite3.Row
            if event_type:
                rows = conn.execute(
                    "SELECT * FROM events WHERE event_type = ? ORDER BY timestamp DESC LIMIT ?",
                    (str(event_type), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if item.get("details"):
                try:
                    item["details"] = json.loads(item["details"])
                except json.JSONDecodeError:
                    pass
            out.append(item)
        return out

    def save_node_position(self, node_id, x, y, z=0.0):
        with self.lock, self._connection() as conn:
            conn.execute(
                """INSERT INTO node_positions(node_id,x,y,z,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(node_id) DO UPDATE SET
                     x=excluded.x,y=excluded.y,z=excluded.z,updated_at=excluded.updated_at""",
                (str(node_id), float(x), float(y), float(z), time.time()),
            )

    def load_node_positions(self):
        with self._connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT node_id,x,y,z,updated_at FROM node_positions").fetchall()
        return [dict(r) for r in rows]

    def prune(self, event_days=90, csi_days=7):
        now = time.time()
        event_cutoff = now - max(0, int(event_days)) * 86400
        csi_cutoff = now - max(0, int(csi_days)) * 86400
        with self.lock, self._connection() as conn:
            e = conn.execute("DELETE FROM events WHERE timestamp < ?", (event_cutoff,)).rowcount if event_days else 0
            c = conn.execute("DELETE FROM csi_snapshots WHERE timestamp < ?", (csi_cutoff,)).rowcount if csi_days else 0
        return {"events_deleted": e, "csi_deleted": c}
