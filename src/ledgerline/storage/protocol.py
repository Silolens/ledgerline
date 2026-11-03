"""Structural contract shared by Ledgerline persistence adapters."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from ledgerline.storage.models import LedgerRecord


class RunStore(Protocol):
    """Append-only store for completed, JSON-safe run snapshots."""

    def append_run(self, run_id: str, payload: Mapping[str, Any]) -> LedgerRecord: ...

    def get_run(self, run_id: str) -> LedgerRecord | None: ...

    def records(self) -> tuple[LedgerRecord, ...]: ...

    def verify(self) -> bool: ...
