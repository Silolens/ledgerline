"""Provider-neutral immutable journal record types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    """One immutable persisted run snapshot in the store's hash chain."""

    seq: int
    run_id: str
    payload: Mapping[str, Any]
    prev_hash: str
    record_hash: str
