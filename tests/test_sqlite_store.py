"""Tests for the append-only SQLite run journal."""

import sqlite3
from decimal import Decimal

import pytest

from ledgerline.storage.sqlite_store import DuplicateRunError, SQLiteLedgerStore


def test_append_and_read_back_after_reopen(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    first = SQLiteLedgerStore(path)
    saved = first.append_run("run-001", {"cash": Decimal("100.25"), "clean": True})

    reopened = SQLiteLedgerStore(path)
    loaded = reopened.get_run("run-001")

    assert loaded == saved
    assert loaded.payload["cash"] == "100.25"
    assert reopened.verify() is True


def test_records_form_a_contiguous_hash_chain(tmp_path):
    store = SQLiteLedgerStore(tmp_path / "ledger.db")
    a = store.append_run("a", {"n": 1})
    b = store.append_run("b", {"n": 2})

    assert (a.seq, b.seq) == (0, 1)
    assert a.prev_hash == store.GENESIS_HASH
    assert b.prev_hash == a.record_hash
    assert store.records() == (a, b)
    assert store.verify() is True


def test_identical_run_retry_is_idempotent(tmp_path):
    store = SQLiteLedgerStore(tmp_path / "ledger.db")
    first = store.append_run("same-id", {"orders": 2})
    retry = store.append_run("same-id", {"orders": 2})

    assert retry == first
    assert len(store.records()) == 1


def test_reusing_run_id_with_different_content_is_rejected(tmp_path):
    store = SQLiteLedgerStore(tmp_path / "ledger.db")
    store.append_run("same-id", {"orders": 2})

    with pytest.raises(DuplicateRunError):
        store.append_run("same-id", {"orders": 3})


def test_payload_is_immutable_to_the_reader(tmp_path):
    store = SQLiteLedgerStore(tmp_path / "ledger.db")
    record = store.append_run("immutable", {"nested": {"items": [1, 2]}})

    with pytest.raises(TypeError):
        record.payload["nested"]["new"] = True
    assert record.payload["nested"]["items"] == (1, 2)


def test_database_rejects_update_and_delete(tmp_path):
    path = tmp_path / "ledger.db"
    store = SQLiteLedgerStore(path)
    store.append_run("protected", {"clean": True})

    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE ledger_runs SET run_id = 'changed' WHERE seq = 0")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM ledger_runs WHERE seq = 0")


def test_invalid_run_id_and_payload_are_rejected(tmp_path):
    store = SQLiteLedgerStore(tmp_path / "ledger.db")

    with pytest.raises(ValueError):
        store.append_run(" ", {"ok": True})
    with pytest.raises(TypeError):
        store.append_run("bad-payload", ["not", "a", "mapping"])


def test_tampering_is_detected(tmp_path):
    path = tmp_path / "ledger.db"
    store = SQLiteLedgerStore(path)
    store.append_run("tamper-me", {"balance": "100"})

    # Simulate an attacker bypassing application-level append-only APIs.
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER ledger_runs_no_update")
        conn.execute("UPDATE ledger_runs SET payload_json = '{\"balance\":\"1\"}'")
    assert store.verify() is False


def test_empty_store_verifies(tmp_path):
    assert SQLiteLedgerStore(tmp_path / "empty.db").verify() is True
