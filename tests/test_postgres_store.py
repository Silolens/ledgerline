"""PostgreSQL adapter integration tests; enabled with LEDGERLINE_TEST_DATABASE_URL."""

import os

import pytest

from ledgerline.storage import DuplicateRunError
from ledgerline.storage.postgres_store import PostgresLedgerStore

DATABASE_URL = os.getenv("LEDGERLINE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires disposable PostgreSQL database")


@pytest.fixture
def store():
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    def connect():
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=5)

    instance = PostgresLedgerStore(connect)
    # This test database must be disposable; truncate is performed by the test owner.
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE ledger_runs, ledger_chain_head")
        conn.commit()
    finally:
        conn.close()
    return instance


def test_append_retry_conflict_and_chain_verification(store):
    first = store.append_run("pg-run-1", {"orders": 2, "cash": "100.00"})
    assert store.append_run("pg-run-1", {"cash": "100.00", "orders": 2}) == first
    with pytest.raises(DuplicateRunError):
        store.append_run("pg-run-1", {"orders": 3})
    second = store.append_run("pg-run-2", {"orders": 1})
    assert second.seq == first.seq + 1
    assert second.prev_hash == first.record_hash
    assert store.verify()


def test_concurrent_appends_keep_one_contiguous_chain(store):
    from concurrent.futures import ThreadPoolExecutor

    def append(index):
        return store.append_run(f"parallel-{index}", {"index": index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(24)))
    records = store.records()
    assert len(records) == 24
    assert [record.seq for record in records] == list(range(24))
    assert store.verify()
