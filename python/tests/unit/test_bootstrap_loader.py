from __future__ import annotations

from datetime import datetime, timezone

import polars as pl

from flow_core.storage.bootstrap import SnapshotBootstrapLoader


def test_loader_merges_mixed_schema_parquet_history(tmp_path) -> None:
    raw_root = tmp_path / "raw"
    derived_root = tmp_path / "derived"
    greeks_root = derived_root / "greeks"
    raw_root.mkdir(parents=True, exist_ok=True)
    greeks_root.mkdir(parents=True, exist_ok=True)

    older = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "expiration": ["2026-03-20"],
            "option_type": ["call"],
            "strike": [100.0],
            "delta": [0.52],
            "asof_ts": [datetime(2026, 3, 25, 14, 0, tzinfo=timezone.utc)],
        }
    )
    newer = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "expiration": ["2026-03-20"],
            "option_type": ["call"],
            "strike": [105.0],
            "delta": [0.47],
            "batch_id": ["b2"],
            "asof_ts": [datetime(2026, 3, 25, 14, 5, tzinfo=timezone.utc)],
        }
    )
    older.write_parquet(greeks_root / "older.parquet")
    newer.write_parquet(greeks_root / "newer.parquet")

    loader = SnapshotBootstrapLoader(raw_root=str(raw_root), derived_root=str(derived_root))
    history = loader.load_symbol_dataset_history("SPY", "greeks")

    assert history.height == 2
    assert "batch_id" in history.columns
    assert history["batch_id"].null_count() == 1
    assert loader.get_last_read_error("greeks") is None


def test_loader_returns_partial_history_and_records_read_error(tmp_path) -> None:
    raw_root = tmp_path / "raw"
    derived_root = tmp_path / "derived"
    greeks_root = derived_root / "greeks"
    raw_root.mkdir(parents=True, exist_ok=True)
    greeks_root.mkdir(parents=True, exist_ok=True)

    valid = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "expiration": ["2026-03-20"],
            "option_type": ["call"],
            "strike": [100.0],
            "delta": [0.5],
            "asof_ts": [datetime(2026, 3, 25, 14, 0, tzinfo=timezone.utc)],
        }
    )
    valid.write_parquet(greeks_root / "valid.parquet")
    (greeks_root / "corrupt.parquet").write_text("not a parquet file", encoding="utf-8")

    loader = SnapshotBootstrapLoader(raw_root=str(raw_root), derived_root=str(derived_root))
    history = loader.load_symbol_dataset_history("SPY", "greeks")

    assert history.height == 1
    message = loader.get_last_read_error("greeks")
    assert message is not None
    assert "partial parquet read failure" in message
