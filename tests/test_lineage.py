"""Tests for the Lineage hash chain: determinism, linkage, tamper detection."""

from dataclasses import replace
from decimal import Decimal

from ledgerline.core.types import Side
from ledgerline.lineage.lineage import (
    GENESIS_PREV_ID,
    LineageLog,
    LineageRecord,
    canonical_json,
)

TS = "2026-07-29T14:30:00Z"


# --- Canonicalization -------------------------------------------------------

def test_canonical_json_is_key_order_independent():
    a = canonical_json({"b": 1, "a": 2})
    b = canonical_json({"a": 2, "b": 1})
    assert a == b


def test_canonical_json_handles_decimal_and_enum():
    s = canonical_json({"price": Decimal("150.25"), "side": Side.BUY})
    # Decimal -> exact string, enum -> its value; no float artifacts.
    assert '"price":"150.25"' in s
    assert '"side":"BUY"' in s
    assert "150.25" in s and "e" not in s.split("150.25")[0][-3:]


# --- IDs and chaining -------------------------------------------------------

def test_first_record_links_to_genesis():
    log = LineageLog()
    rec = log.append("order_intent", {"symbol": "AAPL"}, timestamp=TS)
    assert rec.seq == 0
    assert rec.prev_id == GENESIS_PREV_ID
    assert len(rec.id) == 64


def test_chain_links_prev_to_head():
    log = LineageLog()
    r0 = log.append("a", {"n": 1}, timestamp=TS)
    r1 = log.append("b", {"n": 2}, timestamp=TS)
    assert r1.prev_id == r0.id
    assert log.head_id == r1.id
    assert len(log) == 2


def test_ids_are_deterministic_across_runs():
    def build():
        log = LineageLog()
        log.append("order_intent", {"symbol": "AAPL", "qty": Decimal("10")}, timestamp=TS)
        log.append("fill", {"symbol": "AAPL", "price": Decimal("150.25")}, timestamp=TS)
        return [r.id for r in log.records]

    assert build() == build()


def test_same_content_different_position_differs():
    log = LineageLog()
    r0 = log.append("x", {"n": 1}, timestamp=TS)
    r1 = log.append("x", {"n": 1}, timestamp=TS)
    # Identical kind+payload+timestamp, but the chain position differs.
    assert r0.id != r1.id


# --- Verification / tamper detection ----------------------------------------

def test_clean_chain_verifies():
    log = LineageLog()
    log.append("a", {"n": 1}, timestamp=TS)
    log.append("b", {"n": 2}, timestamp=TS)
    log.append("c", {"n": 3}, timestamp=TS)
    assert log.verify() is True


def test_tampered_payload_breaks_verification():
    log = LineageLog()
    log.append("a", {"n": 1}, timestamp=TS)
    log.append("b", {"n": 2}, timestamp=TS)
    # Forge the payload of record 0 while keeping its (now-stale) id.
    forged = replace(log._records[0], payload={"n": 999})
    log._records[0] = forged
    assert log.verify() is False


def test_reordered_records_break_verification():
    log = LineageLog()
    log.append("a", {"n": 1}, timestamp=TS)
    log.append("b", {"n": 2}, timestamp=TS)
    log._records[0], log._records[1] = log._records[1], log._records[0]
    assert log.verify() is False


def test_recompute_id_matches_sealed_id():
    log = LineageLog()
    rec = log.append("a", {"n": 1}, timestamp=TS)
    assert rec.recompute_id() == rec.id


# --- Payload immutability ---------------------------------------------------

def test_appended_payload_is_frozen_snapshot():
    log = LineageLog()
    payload = {"symbol": "AAPL", "qty": 10}
    rec = log.append("order_intent", payload, timestamp=TS)
    # Mutating the caller's dict after append must not change the record.
    payload["qty"] = 999
    assert rec.payload["qty"] == 10
    assert log.verify() is True


def test_empty_log_head_is_genesis():
    log = LineageLog()
    assert log.head_id == GENESIS_PREV_ID
    assert len(log) == 0
    assert log.verify() is True

def test_nested_payload_is_deeply_immutable():
    log = LineageLog()
    source = {"outer": {"items": [1, {"value": "original"}]}}
    rec = log.append("nested", source, timestamp=TS)
    source["outer"]["items"][1]["value"] = "changed"
    assert rec.payload["outer"]["items"][1]["value"] == "original"
    try:
        rec.payload["outer"]["items"][1]["value"] = "tampered"
        assert False, "nested lineage mapping should be immutable"
    except TypeError:
        pass
    try:
        rec.payload["outer"]["items"][0] = 99
        assert False, "lineage sequences should be immutable"
    except TypeError:
        pass
    assert log.verify() is True
