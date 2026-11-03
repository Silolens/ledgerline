"""Integration tests for completed demo-run persistence."""

import pytest

from ledgerline.storage import DuplicateRunError, SQLiteLedgerStore, persist_demo_result
from ledgerline.demo import run_demo


def test_completed_demo_is_persisted_with_full_report(tmp_path):
    store = SQLiteLedgerStore(tmp_path / "runs.sqlite3")
    result = run_demo()

    record = persist_demo_result(result, run_id="demo-001", store=store)

    assert record.run_id == "demo-001"
    assert record.payload["summary"] == {"orders": 2, "filled": 2}
    assert len(record.payload["orders"]) == 2
    assert record.payload["reconciliation"]["clean"] is True
    assert record.payload["lineage_intact"] is True
    assert len(record.payload["orders"][0]["lineage_ids"]) > 0
    assert store.verify() is True


def test_demo_persistence_retry_is_idempotent(tmp_path):
    store = SQLiteLedgerStore(tmp_path / "runs.sqlite3")
    result = run_demo()

    first = persist_demo_result(result, run_id="retry-001", store=store)
    second = persist_demo_result(result, run_id="retry-001", store=store)

    assert first.record_hash == second.record_hash
    assert len(store.records()) == 1


def test_demo_persistence_rejects_run_id_reuse_for_different_result(tmp_path):
    store = SQLiteLedgerStore(tmp_path / "runs.sqlite3")
    persist_demo_result(run_demo(), run_id="same-id", store=store)
    changed = run_demo(quantity="5")

    with pytest.raises(DuplicateRunError):
        persist_demo_result(changed, run_id="same-id", store=store)


def test_demo_persistence_requires_explicit_nonempty_run_id(tmp_path):
    store = SQLiteLedgerStore(tmp_path / "runs.sqlite3")
    with pytest.raises(ValueError):
        persist_demo_result(run_demo(), run_id="", store=store)
