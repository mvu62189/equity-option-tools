from __future__ import annotations

from pathlib import Path

import polars as pl

from flow_core.storage.duckdb_service import DuckDBService
from flow_core.storage.parquet_store import ParquetStore


def test_parquet_to_duckdb_roundtrip(tmp_path: Path) -> None:
    store = ParquetStore(str(tmp_path / "raw"))
    derived_store = ParquetStore(str(tmp_path / "derived"))
    frame = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": ["2026-01-01T00:00:00Z"],
            "expiration": ["2026-01-31"],
            "option_type": ["call"],
            "strike": [450.0],
            "bid": [1.0],
            "ask": [1.2],
            "last": [1.1],
            "volume": [0],
            "open_interest": [10],
            "underlying_price": [449.0],
            "implied_vol_vendor": [0.2],
            "provider": ["yfinance"],
            "snapshot_id": ["abc"],
        }
    ).with_columns(
        pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
        pl.col("expiration").str.to_date(format="%Y-%m-%d"),
    )

    paths = store._append_sync(frame)
    assert paths

    parity = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "expiration": ["2026-01-31"],
            "winner_model": ["luba"],
            "bjerksund_error": [0.02],
            "luba_error": [0.01],
            "bjerksund_rmse": [0.03],
            "luba_rmse": [0.015],
            "winner_gap": [0.01],
            "pairs": [4],
            "tau_years": [0.1],
            "asof_ts": ["2026-01-01T00:00:00Z"],
        }
    ).with_columns(
        pl.col("expiration").str.to_date(format="%Y-%m-%d"),
        pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
    )
    parity_paths = derived_store._append_dataset_sync(parity, dataset="parity")
    assert parity_paths

    parity_detail = pl.DataFrame(
        {
            "symbol": ["SPY", "SPY"],
            "expiration": ["2026-01-31", "2026-01-31"],
            "strike": [450.0, 450.0],
            "model": ["bjerksund_stensland", "luba"],
            "parity_error": [0.02, 0.01],
            "relative_error": [0.002, 0.001],
            "call_eur": [5.0, 5.01],
            "put_eur": [4.0, 4.01],
            "parity_rhs": [1.0, 1.0],
            "tau_years": [0.1, 0.1],
            "asof_ts": ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"],
        }
    ).with_columns(
        pl.col("expiration").str.to_date(format="%Y-%m-%d"),
        pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
    )
    parity_detail_paths = derived_store._append_dataset_sync(parity_detail, dataset="parity_detail")
    assert parity_detail_paths

    dispatch = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "expiration": ["2026-01-31"],
            "iv_engine": ["bjerksund_stensland"],
            "greeks_engine": ["binomial_richardson"],
            "contracts": [25],
            "avg_iv": [0.21],
            "min_iv": [0.18],
            "max_iv": [0.26],
            "asof_ts": ["2026-01-01T00:00:00Z"],
        }
    ).with_columns(
        pl.col("expiration").str.to_date(format="%Y-%m-%d"),
        pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
    )
    dispatch_paths = derived_store._append_dataset_sync(dispatch, dataset="dispatch")
    assert dispatch_paths

    ssvi = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "fit_space": ["log"],
            "objective": [0.001],
            "iterations": [80],
            "success": [True],
            "compare_fit_space": ["strike"],
            "compare_objective": [0.0015],
            "compare_iterations": [90],
            "compare_success": [True],
            "asof_ts": ["2026-01-01T00:00:00Z"],
        }
    ).with_columns(pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"))
    ssvi_paths = derived_store._append_dataset_sync(ssvi, dataset="ssvi")
    assert ssvi_paths

    calibration = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": ["2026-01-01T00:00:00Z"],
            "expiration": ["2026-01-31T16:00:00Z"],
            "model_id": ["ssvi_primary_log"],
            "converged": [True],
            "iterations": [80],
            "sse_final": [0.001],
            "durrleman_pass": [True],
            "params": [{"a": 0.01, "b": 0.1, "rho": -0.2, "m": 0.0, "sigma": 0.25}],
        }
    ).with_columns(
        pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
        pl.col("expiration").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
        pl.col("iterations").cast(pl.UInt32),
    )
    calibration_paths = derived_store._append_dataset_sync(calibration, dataset="diagnostics")
    assert calibration_paths

    greeks = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": ["2026-01-01T00:00:00Z"],
            "expiration": ["2026-01-31T16:00:00Z"],
            "option_type": ["call"],
            "strike": [450.0],
            "underlying_price": [449.0],
            "implied_vol": [0.2],
            "days_to_expiry": [30],
            "greeks_engine": ["binomial_richardson"],
            "engine_used": ["binomial_richardson"],
            "price": [4.2],
            "delta": [0.5],
            "gamma": [0.03],
            "theta": [-0.02],
            "vega": [None],
            "rho": [None],
            "success": [True],
            "error": [""],
        }
    ).with_columns(
        pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
        pl.col("expiration").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
    )
    greeks_paths = derived_store._append_dataset_sync(greeks, dataset="greeks")
    assert greeks_paths

    duck = DuckDBService()
    duck.register_default_datasets(tmp_path / "raw", tmp_path / "derived")
    out = duck.query_polars("SELECT COUNT(*) AS n FROM option_quotes")
    assert out["n"][0] == 1
    out_parity = duck.query_polars("SELECT COUNT(*) AS n FROM parity_diagnostics")
    assert out_parity["n"][0] == 1
    out_parity_detail = duck.query_polars("SELECT COUNT(*) AS n FROM parity_detail_diagnostics")
    assert out_parity_detail["n"][0] == 2
    out_dispatch = duck.query_polars("SELECT COUNT(*) AS n FROM dispatch_diagnostics")
    assert out_dispatch["n"][0] == 1
    out_ssvi = duck.query_polars("SELECT COUNT(*) AS n FROM ssvi_diagnostics")
    assert out_ssvi["n"][0] == 1
    out_calibration = duck.query_polars("SELECT COUNT(*) AS n FROM calibration_diagnostics")
    assert out_calibration["n"][0] == 1
    out_greeks = duck.query_polars("SELECT COUNT(*) AS n FROM routed_greeks")
    assert out_greeks["n"][0] == 1

    duck.execute_sql_file(Path(__file__).parents[3] / "sql" / "options_views.sql")
    winners = duck.query_polars("SELECT COUNT(*) AS n FROM v_parity_winners")
    assert winners["n"][0] == 1
    by_strike = duck.query_polars("SELECT COUNT(*) AS n FROM v_parity_by_strike")
    assert by_strike["n"][0] == 2
    ssvi_view = duck.query_polars("SELECT COUNT(*) AS n FROM v_ssvi_summary")
    assert ssvi_view["n"][0] == 1
    calibration_view = duck.query_polars("SELECT COUNT(*) AS n FROM v_calibration_diagnostics")
    assert calibration_view["n"][0] == 1
    greeks_view = duck.query_polars("SELECT COUNT(*) AS n FROM v_routed_greeks")
    assert greeks_view["n"][0] == 1
    duck.close()
