from __future__ import annotations

from datetime import datetime, timezone

import polars as pl

from flow_core.orchestration.state_store import BatchPayload, LiveStateStore


def _payload(symbol: str, rows: int) -> BatchPayload:
    raw = pl.DataFrame({"symbol": [symbol] * rows, "strike": list(range(rows))})
    return BatchPayload(
        symbol=symbol,
        batch_id=f"{symbol}-b",
        version_hint=None,
        updated_at_utc=datetime.now(timezone.utc),
        raw=raw,
        greeks=pl.DataFrame(),
        ssvi=pl.DataFrame(),
        dispatch=pl.DataFrame(),
        parity=pl.DataFrame(),
        parity_detail=pl.DataFrame(),
        calibration_diag=pl.DataFrame(),
        latency_ms={},
        status={},
    )


def test_publish_is_monotonic_and_snapshot_consistent() -> None:
    store = LiveStateStore()
    v1 = store.publish(_payload("SPY", 3))
    v2 = store.publish(_payload("SPY", 4))
    assert v1 == 1
    assert v2 == 2
    snap = store.get_snapshot("SPY")
    assert snap is not None
    assert snap.version == 2
    assert snap.raw.height == 4


def test_trim_to_budget_respects_row_cap() -> None:
    store = LiveStateStore(dataset_row_caps={"raw": 2}, dataset_budgets_mb={"raw": 1})
    store.publish(_payload("SPY", 8))
    dropped = store.trim_to_budget("SPY")
    snap = store.get_snapshot("SPY")
    assert snap is not None
    assert snap.raw.height <= 2
    assert dropped.get("raw", 0) >= 1


def test_overlay_publish_updates_memory_bytes() -> None:
    store = LiveStateStore()
    version = store.publish(_payload("SPY", 3))
    ok = store.publish_overlay_payloads("SPY", version, {"overlay": {"heat_image": __import__("numpy").zeros((4, 4))}})
    assert ok
    snap = store.get_snapshot("SPY")
    assert snap is not None
    assert snap.memory_bytes.get("overlay", 0) > 0

