from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl
import pytest

from flow_core.config.models import PipelineConfig, ProviderMap
from flow_core.orchestration.cache import InMemoryQuoteCache
from flow_core.orchestration.pipeline import QuantPipelineService
from flow_core.quant.models import SSVIResult
from flow_core.storage.parquet_store import ParquetStore


@pytest.mark.asyncio
async def test_primary_log_ssvi_failure_marks_status_and_caches_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["SPY", "SPY", "SPY"],
            "asof_ts": [datetime(2026, 1, 1, tzinfo=timezone.utc)] * 3,
            "expiration": [date(2026, 1, 31)] * 3,
            "option_type": ["call", "call", "call"],
            "strike": [440.0, 450.0, 460.0],
            "bid": [12.0, 8.5, 5.5],
            "ask": [12.4, 8.9, 5.9],
            "last": [12.2, 8.7, 5.7],
            "volume": [10, 10, 10],
            "open_interest": [100, 100, 100],
            "underlying_price": [449.0, 449.0, 449.0],
            "implied_vol_vendor": [0.2, 0.2, 0.2],
            "provider": ["test", "test", "test"],
            "snapshot_id": ["x", "x", "x"],
        }
    )

    def _fail_ssvi(*_args, **_kwargs) -> SSVIResult:
        return SSVIResult(
            a=0.01,
            b=0.10,
            rho=-0.2,
            m=0.0,
            sigma=0.25,
            objective=1.0,
            success=False,
            iterations=120,
            durrleman_pass=False,
        )

    monkeypatch.setattr("flow_core.orchestration.pipeline.calibrate_ssvi", _fail_ssvi)

    service = QuantPipelineService(
        adapter=None,  # type: ignore[arg-type]
        provider_map=ProviderMap(provider="test", required_fields=[], field_map={}),
        config=PipelineConfig(ssvi_fit_space="log", ssvi_enable_space_compare=False, ssvi_backend="python"),
        cache=InMemoryQuoteCache(),
        parquet_store=ParquetStore(str(tmp_path / "raw")),
        derived_store=None,
    )

    await service._on_live_batch(frame)

    snapshot = service.cache.get_snapshot_nowait("SPY")
    assert snapshot is not None
    assert bool(snapshot.status.get("ssvi_fail")) is True
    diagnostics = service.cache.get_calibration_diagnostics_nowait("SPY")
    assert diagnostics.height >= 1
    assert any(str(mid).startswith("ssvi_call_") for mid in diagnostics["model_id"].to_list())


@pytest.mark.asyncio
async def test_parity_path_appends_luba_rim_solver_diagnostics(tmp_path) -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["SPY", "SPY", "SPY", "SPY"],
            "asof_ts": [
                datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
            ],
            "expiration": [date(2026, 3, 20), date(2026, 3, 20), date(2026, 3, 20), date(2026, 3, 20)],
            "option_type": ["call", "put", "call", "put"],
            "strike": [500.0, 500.0, 510.0, 510.0],
            "bid": [11.8, 1.5, 5.9, 4.7],
            "ask": [12.2, 1.7, 6.1, 4.9],
            "last": [12.0, 1.6, 6.0, 4.8],
            "volume": [0, 0, 0, 0],
            "open_interest": [1, 1, 1, 1],
            "underlying_price": [505.0, 505.0, 505.0, 505.0],
            "implied_vol_vendor": [0.2, 0.2, 0.2, 0.2],
            "provider": ["yfinance", "yfinance", "yfinance", "yfinance"],
            "snapshot_id": ["s1", "s1", "s1", "s1"],
        }
    )

    service = QuantPipelineService(
        adapter=None,  # type: ignore[arg-type]
        provider_map=ProviderMap(provider="test", required_fields=[], field_map={}),
        config=PipelineConfig(parity_eep_mode="hybrid", parity_luba_method="rim"),
        cache=InMemoryQuoteCache(),
        parquet_store=ParquetStore(str(tmp_path / "raw")),
        derived_store=None,
    )
    await service._compute_and_store_diagnostics(symbol="SPY", frame=frame, flush_calibration_diagnostics=False)
    diagnostics = service.cache.get_calibration_diagnostics_nowait("SPY")
    assert diagnostics.height >= 1
    assert any(str(mid).startswith("rim_") for mid in diagnostics["model_id"].to_list())
