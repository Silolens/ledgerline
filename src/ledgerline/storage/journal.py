"""Persist a completed demo result through the storage adapter contract."""

from __future__ import annotations

from typing import Any

from ledgerline.demo import DemoResult
from ledgerline.storage.protocol import RunStore
from ledgerline.storage.sqlite_store import LedgerRecord
from ledgerline.web.serialize import demo_to_dict


def persist_demo_result(
    result: DemoResult, *, run_id: str, store: RunStore
) -> LedgerRecord:
    """Append the exact JSON-safe report for a completed demo run.

    Run IDs are deliberately supplied by the caller. Retrying the same ID and
    payload is idempotent; reusing it for a different report is rejected by the
    adapter. This function never generates timestamps or identifiers.
    """
    if not isinstance(result, DemoResult):
        raise TypeError("result must be a completed DemoResult")
    payload: dict[str, Any] = demo_to_dict(result)
    return store.append_run(run_id, payload)
