"""Persistence adapters and contracts for Ledgerline."""

from ledgerline.storage.journal import persist_demo_result
from ledgerline.storage.models import LedgerRecord
from ledgerline.storage.protocol import RunStore
from ledgerline.storage.sqlite_store import DuplicateRunError, SQLiteLedgerStore

__all__ = ["DuplicateRunError", "LedgerRecord", "RunStore", "SQLiteLedgerStore", "persist_demo_result"]

# Import the optional adapter without importing psycopg itself. The driver is
# loaded only by the caller's connection factory when PostgreSQL is selected.
from ledgerline.storage.postgres_store import PostgresLedgerStore

__all__.append("PostgresLedgerStore")
