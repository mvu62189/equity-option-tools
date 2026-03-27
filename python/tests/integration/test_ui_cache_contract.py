from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import polars as pl
import pytest

pytest.importorskip("PySide6")

from flow_core.orchestration.cache import InMemoryQuoteCache
from flow_core.orchestration.state_store import BatchPayload
from flow_ui.main import MainWindow


def test_ui_uses_cache_contract(qtbot) -> None:
    cache = InMemoryQuoteCache()
    window = MainWindow(cache=cache, refresh_ms=1000, symbol="SPY")
    qtbot.addWidget(window)
    assert window.windowTitle() == "Quant Pipeline MVP"


def test_ui_review_option_chain_table_uses_hydrated_snapshot(qtbot) -> None:
    cache = InMemoryQuoteCache()
    window = MainWindow(cache=cache, refresh_ms=1000, symbol="SPY")
    qtbot.addWidget(window)
    raw = pl.DataFrame(
        {
            "symbol": ["SPY", "SPY"],
            "expiration": ["2026-03-27", "2026-03-27"],
            "option_type": ["call", "put"],
            "contract_symbol": ["SPY260327C00570000", "SPY260327P00570000"],
            "strike": [570.0, 570.0],
            "bid": [4.9, 4.7],
            "ask": [5.1, 4.9],
            "last": [5.0, 4.8],
            "volume": [120.0, 95.0],
            "open_interest": [800.0, 760.0],
            "underlying_price": [571.2, 571.2],
            "batch_id": ["b1", "b1"],
            "asof_ts": [datetime(2026, 3, 25, 14, 5, tzinfo=timezone.utc)] * 2,
        }
    )
    asyncio.run(
        cache.publish_batch(
            BatchPayload(
                symbol="SPY",
                batch_id="b1",
                version_hint=1,
                updated_at_utc=datetime.now(timezone.utc),
                raw=raw,
                greeks=pl.DataFrame(),
                ssvi=pl.DataFrame(),
                dispatch=pl.DataFrame(),
                parity=pl.DataFrame(),
                parity_detail=pl.DataFrame(),
                calibration_diag=pl.DataFrame(),
                trading_date="2026-03-25",
                snapshot_kind="offline_bootstrap",
                source_mode="ui_review",
            )
        )
    )
    snapshot = cache.get_snapshot_nowait("SPY")
    assert snapshot is not None
    window._apply_snapshot(snapshot)

    assert window._option_chain_model.rowCount() == 2
    assert "batch=b1" in window._option_chain_status.text()


def test_ui_surfaces_dataset_read_error_from_history_service(qtbot) -> None:
    class FakeHistoryService:
        def load_chart_history(self, symbol: str, dataset: str) -> pl.DataFrame:
            return pl.DataFrame()

        def get_dataset_read_error(self, dataset: str) -> str | None:
            if dataset == "surface_points":
                return "partial parquet read failure under surface_points"
            return None

    cache = InMemoryQuoteCache()
    service = FakeHistoryService()
    window = MainWindow(
        cache=cache,
        refresh_ms=1000,
        symbol="SPY",
        history_callback=service.load_chart_history,
    )
    qtbot.addWidget(window)

    window._refresh_validation_view()

    assert "read_error=surface_points:" in window._validation_status.text()
