"""Lineage: an immutable, tamper-evident record of every decision.

Regulators, post-mortems and the self-improvement loop all need the same thing:
a faithful, ordered, unforgeable account of *what the system decided and why*.
This module provides it as an append-only hash chain.

Design
------
* **Deterministic IDs.** A record's ``id`` is ``sha256`` over its canonical
  content plus the previous record's id. Same content in the same position
  always yields the same id — replays reproduce the chain exactly.
* **Hash chain.** Each record carries ``prev_id`` (the genesis predecessor is
  64 zeros). Because every id folds in the predecessor's id, editing any past
  record invalidates every id after it — tampering is detectable by re-walking.
* **No wall-clock, no randomness.** Timestamps and sequence numbers are supplied
  by the caller, never read from the clock here, so the chain is reproducible.
* **Decimal / enum safe.** Canonicalization turns Decimal into its exact string
  and enums into their value, so money and Side survive hashing without float.

The record ``payload`` is an arbitrary JSON-like mapping (order intent, gate
verdicts, fill, rejection reason). This module does not care what a decision
*means*; it only guarantees the account of it cannot be quietly rewritten.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping
from types import MappingProxyType

__all__ = ["LineageRecord", "LineageLog", "GENESIS_PREV_ID", "canonical_json"]

#: Predecessor id of the very first record in a chain.
GENESIS_PREV_ID = "0" * 64

def _canonicalize(value: Any) -> Any:
    """Recursively convert a value into a JSON-safe, deterministic form.

    Decimals become their exact string (no float rounding); enums become their
    ``.value``; mappings are rebuilt with string keys; sequences are walked.
    Anything else must already be JSON-serializable (str/int/bool/None).
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if isinstance(value, Mapping):
        return {str(k): _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    return value


def _freeze(value: Any) -> Any:
    """Recursively freeze canonical JSON data before it enters the hash chain."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialize a payload to a canonical string: sorted keys, no whitespace.

    Two payloads that are equal as data serialize to the identical string, so
    hashing is stable across dict ordering and Decimal/enum representation.
    """
    return json.dumps(
        _canonicalize(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


@dataclass(frozen=True, slots=True)
class LineageRecord:
    """One immutable link in the decision chain.

    ``seq`` is the position (0-based), ``timestamp`` an ISO-8601 string supplied
    by the caller, ``kind`` a short tag (e.g. ``"order_intent"``, ``"reject"``),
    ``payload`` the decision data. ``prev_id`` and ``id`` form the chain.
    """

    seq: int
    timestamp: str
    kind: str
    payload: Mapping[str, Any]
    prev_id: str
    id: str

    @staticmethod
    def compute_id(
        seq: int, timestamp: str, kind: str, payload: Mapping[str, Any], prev_id: str
    ) -> str:
        """Deterministic sha256 id over canonical content + predecessor id."""
        body = canonical_json(
            {
                "seq": seq,
                "timestamp": timestamp,
                "kind": kind,
                "payload": payload,
                "prev_id": prev_id,
            }
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def recompute_id(self) -> str:
        """Re-derive this record's id from its own fields (for verification)."""
        return self.compute_id(
            self.seq, self.timestamp, self.kind, self.payload, self.prev_id
        )


class LineageLog:
    """Append-only hash chain of :class:`LineageRecord`.

    Callers ``append`` decisions; the log assigns the sequence number, links the
    previous id, and computes the new id. ``verify`` re-walks the chain to prove
    it has not been tampered with.
    """

    def __init__(self) -> None:
        self._records: list[LineageRecord] = []

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[LineageRecord, ...]:
        return tuple(self._records)

    @property
    def head_id(self) -> str:
        """Id of the most recent record, or the genesis predecessor if empty."""
        return self._records[-1].id if self._records else GENESIS_PREV_ID

    def append(
        self, kind: str, payload: Mapping[str, Any], *, timestamp: str
    ) -> LineageRecord:
        """Append a decision and return its sealed record."""
        seq = len(self._records)
        prev_id = self.head_id
        # Freeze the payload as a canonical, immutable structure so a caller
        # can't mutate the dict after it has been hashed into the chain.
        frozen_payload = _freeze(json.loads(canonical_json(payload)))
        record_id = LineageRecord.compute_id(
            seq, timestamp, kind, frozen_payload, prev_id
        )
        record = LineageRecord(
            seq=seq,
            timestamp=timestamp,
            kind=kind,
            payload=frozen_payload,
            prev_id=prev_id,
            id=record_id,
        )
        self._records.append(record)
        return record

    def verify(self) -> bool:
        """Re-walk the chain: every id must recompute and every link must join."""
        expected_prev = GENESIS_PREV_ID
        for i, rec in enumerate(self._records):
            if rec.seq != i:
                return False
            if rec.prev_id != expected_prev:
                return False
            if rec.recompute_id() != rec.id:
                return False
            expected_prev = rec.id
        return True