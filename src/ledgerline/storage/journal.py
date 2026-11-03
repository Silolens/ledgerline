"""Persist a completed demo result through the storage adapter contract."""

from __future__ import annotations

from ledgerline.demo import DemoResult
from ledgerline.storage.models import LedgerRecord
from ledgerline.storage.protocol import RunStore
from ledgerline.web.serialize import demo_to_dict


def persist_demo_result(result: DemoResult, *, run_id: str, store: RunStore) -> LedgerRecord:
    """Append the exact JSON-safe report for a completed demo run.

    Run IDs are supplied by the caller. Identical retries are idempotent;
    reusing an ID for different content is rejected by the adapter.
    """
    if not isinstance(result, DemoResult):
        raise TypeError("result must be a completed DemoResult")
    return store.append_run(run_id, demo_to_dict(result))
