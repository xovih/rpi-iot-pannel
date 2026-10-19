from __future__ import annotations

import csv
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LocalStore:
    """Durable outbox + state recovery; aman dipanggil dari callback GPIO."""

    def __init__(self, database_path: Path, csv_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path = csv_path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(database_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS counter_state (
              slot_code TEXT PRIMARY KEY,
              counter_total INTEGER NOT NULL DEFAULT 0,
              last_pulse_at TEXT
            );
            CREATE TABLE IF NOT EXISTS outbox (
              event_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS runtime_state (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )
        self._db.commit()

    def increment(self, slot_code: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO counter_state(slot_code, counter_total, last_pulse_at)
                   VALUES (?, 1, ?)
                   ON CONFLICT(slot_code) DO UPDATE SET
                     counter_total = counter_total + 1, last_pulse_at = excluded.last_pulse_at""",
                (slot_code, now),
            )
            row = self._db.execute(
                "SELECT counter_total FROM counter_state WHERE slot_code = ?", (slot_code,)
            ).fetchone()
        total = int(row["counter_total"])
        self._append_csv(slot_code, total, now)
        return total

    def counter(self, slot_code: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT counter_total FROM counter_state WHERE slot_code = ?", (slot_code,)
            ).fetchone()
        return int(row["counter_total"]) if row else 0

    def last_pulse(self, slot_code: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT last_pulse_at FROM counter_state WHERE slot_code = ?", (slot_code,)
            ).fetchone()
        return str(row["last_pulse_at"]) if row and row["last_pulse_at"] else None

    def start_session(self, slot_codes: list[str], carry: dict[str, int] | None = None) -> None:
        """Simpan baseline tanpa mereset total counter monotonik.

        `carry` (revisi 19 Okt 2026): pulsa yang sudah terlanjur masuk setelah
        peti sebelumnya penuh dibawa ke sesi baru — baseline dimundurkan
        sebanyak itu per spur."""
        with self._lock, self._db:
            for code in slot_codes:
                total = max(0, self.counter(code) - max(0, (carry or {}).get(code, 0)))
                self._db.execute(
                    """INSERT INTO runtime_state(key, value) VALUES (?, ?)
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                    (f"baseline:{code}", str(total)),
                )

    def session_counter(self, slot_code: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM runtime_state WHERE key = ?", (f"baseline:{slot_code}",)
            ).fetchone()
        baseline = int(row["value"]) if row else self.counter(slot_code)
        return max(0, self.counter(slot_code) - baseline)

    def get_state(self, key: str, default: Any = None) -> Any:
        """State UI panel (operator terinput, tab terakhir) yang harus selamat
        dari listrik padam; disimpan JSON di tabel runtime_state."""
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM runtime_state WHERE key = ?", (f"state:{key}",)
            ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set_state(self, key: str, value: Any) -> None:
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO runtime_state(key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (f"state:{key}", json.dumps(value)),
            )

    def begin_run(self) -> bool:
        """Tandai panel sedang berjalan. True bila proses sebelumnya tidak
        sempat ditutup normal (listrik padam, ditutup paksa, crash)."""
        previous = bool(self.get_state("app_running", False))
        self.set_state("app_running", True)
        return previous

    def end_run(self) -> None:
        self.set_state("app_running", False)

    def enqueue(self, event_id: str, kind: str, payload: dict[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO outbox(event_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                (event_id, kind, json.dumps(payload), datetime.now(timezone.utc).isoformat()),
            )

    def pending(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute("SELECT * FROM outbox ORDER BY created_at LIMIT ?", (limit,)))

    def acknowledge(self, event_ids: list[str]) -> None:
        if not event_ids:
            return
        with self._lock, self._db:
            self._db.executemany("DELETE FROM outbox WHERE event_id = ?", [(item,) for item in event_ids])

    def failed_attempt(self, event_ids: list[str]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "UPDATE outbox SET attempts = attempts + 1 WHERE event_id = ?",
                [(item,) for item in event_ids],
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _append_csv(self, slot_code: str, total: int, recorded_at: str) -> None:
        new_file = not self.csv_path.exists()
        with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if new_file:
                writer.writerow(["recorded_at", "logical_slot", "counter_total"])
            writer.writerow([recorded_at, slot_code, total])
