"""Persistence adapters and contracts for Ledgerline."""

from ledgerline.storage.journal import persist_demo_result
from ledgerline.storage.models import LedgerRecord
from ledgerline.storage.protocol import RunStore
from ledgerline.storage.sqlite_store import DuplicateRunError, SQLiteLedgerStore

__all__ = ["DuplicateRunError", "LedgerRecord", "RunStore", "SQLiteLedgerStore", "persist_demo_result"]
