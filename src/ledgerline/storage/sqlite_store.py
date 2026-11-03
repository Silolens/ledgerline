"""Append-only SQLite persistence for completed Ledgerline run records.

This adapter is intended for local development, research and tests. SQLite files
on serverless/ephemeral filesystems are NOT durable production storage.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from ledgerline.lineage.lineage import canonical_json
from ledgerline.storage.models import LedgerRecord

__all__ = ["DuplicateRunError", "LedgerRecord", "SQLiteLedgerStore"]


class DuplicateRunError(ValueError):
    """A run id was reused for different content."""


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _record_hash(seq: int, run_id: str, payload_json: str, prev_hash: str) -> str:
    body = canonical_json(
        {"seq": seq, "run_id": run_id, "payload_json": payload_json, "prev_hash": prev_hash}
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class SQLiteLedgerStore:
    """Persist completed run snapshots in a transactional, hash-chained journal."""

    GENESIS_HASH = "0" * 64

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            if self.path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger_runs (
                    seq INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL UNIQUE
                );
                CREATE TRIGGER IF NOT EXISTS ledger_runs_no_update
                BEFORE UPDATE ON ledger_runs
                BEGIN SELECT RAISE(ABORT, 'ledger_runs is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS ledger_runs_no_delete
                BEFORE DELETE ON ledger_runs
                BEGIN SELECT RAISE(ABORT, 'ledger_runs is append-only'); END;
                """
            )

    @staticmethod
    def _to_record(row: sqlite3.Row) -> LedgerRecord:
        return LedgerRecord(
            seq=row["seq"], run_id=row["run_id"],
            payload=_freeze(json.loads(row["payload_json"])),
            prev_hash=row["prev_hash"], record_hash=row["record_hash"],
        )

    def append_run(self, run_id: str, payload: Mapping[str, Any]) -> LedgerRecord:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        payload_json = canonical_json(payload)
        if not isinstance(json.loads(payload_json), dict):
            raise TypeError("payload must serialize to a JSON object")

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM ledger_runs WHERE run_id = ?", (run_id,)).fetchone()
            if existing is not None:
                if existing["payload_json"] != payload_json:
                    raise DuplicateRunError(f"run_id {run_id!r} already exists with different content")
                conn.commit()
                return self._to_record(existing)
            previous = conn.execute(
                "SELECT seq, record_hash FROM ledger_runs ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            seq = 0 if previous is None else previous["seq"] + 1
            prev_hash = self.GENESIS_HASH if previous is None else previous["record_hash"]
            record_hash = _record_hash(seq, run_id, payload_json, prev_hash)
            conn.execute(
                "INSERT INTO ledger_runs (seq, run_id, payload_json, prev_hash, record_hash) VALUES (?, ?, ?, ?, ?)",
                (seq, run_id, payload_json, prev_hash, record_hash),
            )
            row = conn.execute("SELECT * FROM ledger_runs WHERE run_id = ?", (run_id,)).fetchone()
            conn.commit()
            return self._to_record(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_run(self, run_id: str) -> LedgerRecord | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM ledger_runs WHERE run_id = ?", (run_id,)).fetchone()
            return None if row is None else self._to_record(row)

    def records(self) -> tuple[LedgerRecord, ...]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM ledger_runs ORDER BY seq").fetchall()
            return tuple(self._to_record(row) for row in rows)

    def verify(self) -> bool:
        expected_prev = self.GENESIS_HASH
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM ledger_runs ORDER BY seq").fetchall()
        for expected_seq, row in enumerate(rows):
            if row["seq"] != expected_seq or row["prev_hash"] != expected_prev:
                return False
            if _record_hash(row["seq"], row["run_id"], row["payload_json"], row["prev_hash"]) != row["record_hash"]:
                return False
            expected_prev = row["record_hash"]
        return True
