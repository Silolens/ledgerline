"""Managed PostgreSQL adapter for the append-only run journal.

Requires the optional ``postgres`` extra (psycopg 3). Supply a connection
factory so serverless pooling/credentials remain configured by the host.
"""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ledgerline.lineage.lineage import canonical_json
from ledgerline.storage.models import LedgerRecord
from ledgerline.storage.sqlite_store import DuplicateRunError, SQLiteLedgerStore

ConnectionFactory = Callable[[], Any]


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _record_hash(seq: int, run_id: str, payload_json: str, prev_hash: str) -> str:
    envelope = canonical_json({
        "seq": seq,
        "run_id": run_id,
        "payload_json": payload_json,
        "prev_hash": prev_hash,
    })
    import hashlib
    return hashlib.sha256(envelope.encode("utf-8")).hexdigest()


class PostgresLedgerStore:
    """PostgreSQL implementation of ``RunStore`` with serialized appends.

    The supplied factory must return a fresh psycopg connection configured for
    TLS and bounded serverless/pooled connections. Schema is provisioned by
    migrations; this class never creates or alters database objects.
    """

    GENESIS_HASH = SQLiteLedgerStore.GENESIS_HASH
    # A fixed signed 64-bit advisory-lock key dedicated to the Ledgerline chain.
    ADVISORY_LOCK_KEY = 0x4C45444745524C

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connect = connection_factory

    @staticmethod
    def _record(row: Any) -> LedgerRecord:
        payload = row["payload_canonical"]
        return LedgerRecord(
            seq=int(row["seq"]),
            run_id=row["run_id"],
            payload=_freeze(json.loads(payload)),
            prev_hash=row["prev_hash"],
            record_hash=row["record_hash"],
        )

    @staticmethod
    def _validate(run_id: str, payload: Mapping[str, Any]) -> str:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        payload_json = canonical_json(payload)
        if not isinstance(json.loads(payload_json), dict):
            raise TypeError("payload must serialize to a JSON object")
        return payload_json

    def append_run(self, run_id: str, payload: Mapping[str, Any]) -> LedgerRecord:
        payload_json = self._validate(run_id, payload)
        conn = self._connect()
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    # Transaction-level lock serializes appends across processes/regions.
                    cur.execute("SELECT pg_advisory_xact_lock(%s)", (self.ADVISORY_LOCK_KEY,))
                    cur.execute(
                        "SELECT seq, run_id, payload_canonical, prev_hash, record_hash "
                        "FROM ledger_runs WHERE run_id = %s",
                        (run_id,),
                    )
                    existing = cur.fetchone()
                    if existing is not None:
                        if existing["payload_canonical"] != payload_json:
                            raise DuplicateRunError(
                                f"run_id {run_id!r} already exists with different content"
                            )
                        return self._record(existing)

                    cur.execute(
                        "SELECT last_seq, last_hash FROM ledger_chain_head WHERE singleton = TRUE FOR UPDATE"
                    )
                    head = cur.fetchone()
                    seq = 0 if head is None else int(head["last_seq"]) + 1
                    prev_hash = self.GENESIS_HASH if head is None else head["last_hash"]
                    record_hash = _record_hash(seq, run_id, payload_json, prev_hash)
                    cur.execute(
                        "INSERT INTO ledger_runs "
                        "(seq, run_id, payload, payload_canonical, prev_hash, record_hash) "
                        "VALUES (%s, %s, %s::jsonb, %s, %s, %s) "
                        "RETURNING seq, run_id, payload_canonical, prev_hash, record_hash",
                        (seq, run_id, payload_json, payload_json, prev_hash, record_hash),
                    )
                    row = cur.fetchone()
                    cur.execute(
                        "INSERT INTO ledger_chain_head (singleton, last_seq, last_hash) "
                        "VALUES (TRUE, %s, %s) "
                        "ON CONFLICT (singleton) DO UPDATE "
                        "SET last_seq = EXCLUDED.last_seq, last_hash = EXCLUDED.last_hash",
                        (seq, record_hash),
                    )
                    return self._record(row)
        finally:
            conn.close()

    def get_run(self, run_id: str) -> LedgerRecord | None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT seq, run_id, payload_canonical, prev_hash, record_hash "
                    "FROM ledger_runs WHERE run_id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
                return None if row is None else self._record(row)
        finally:
            conn.close()

    def records(self) -> tuple[LedgerRecord, ...]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT seq, run_id, payload_canonical, prev_hash, record_hash "
                    "FROM ledger_runs ORDER BY seq"
                )
                return tuple(self._record(row) for row in cur.fetchall())
        finally:
            conn.close()

    def verify(self) -> bool:
        expected_prev = self.GENESIS_HASH
        for expected_seq, record in enumerate(self.records()):
            if record.seq != expected_seq or record.prev_hash != expected_prev:
                return False
            payload_json = canonical_json(record.payload)
            # canonical_json(payload) must match the exact persisted canonical text.
            if _record_hash(record.seq, record.run_id, payload_json, record.prev_hash) != record.record_hash:
                return False
            expected_prev = record.record_hash
        return True
