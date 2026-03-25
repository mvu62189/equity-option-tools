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

    surface_points = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": ["2026-01-01T00:00:00Z"],
            "batch_id": ["b1"],
            "expiration": ["2026-01-31T16:00:00Z"],
            "option_type": ["call"],
            "strike": [450.0],
            "days_to_expiry": [30],
            "implied_vol": [0.2],
            "underlying_price": [449.0],
            "market_mid": [4.1],
            "model_price": [4.2],
            "delta": [0.5],
            "gamma": [0.03],
            "theta": [-0.02],
            "vega": [0.11],
            "rho": [0.04],
            "model_implied_vol": [0.21],
            "price_error_abs": [0.1],
            "price_error_rel": [0.024],
            "vol_error_abs": [0.01],
            "vol_error_rel": [0.05],
            "within_bid_ask": [True],
            "bid_ask_width": [0.2],
            "log_moneyness": [0.002],
            "atm_distance": [0.002],
            "is_negative_gamma": [False],
            "delta_smoothness_violation": [False],
            "calendar_total_variance": [0.04],
            "calendar_violation": [False],
        }
    ).with_columns(
        pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
        pl.col("expiration").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
    )
    surface_points_paths = derived_store._append_dataset_sync(surface_points, dataset="surface_points")
    assert surface_points_paths

    surface_diagnostics = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": ["2026-01-01T00:00:00Z"],
            "batch_id": ["b1"],
            "rows": [1],
            "groups": [1],
            "expiry_count": [1],
            "strike_count": [1],
            "failure_count": [0],
            "model_implied_vol_coverage": [1.0],
            "price_rmse": [0.1],
            "vol_rmse": [0.01],
            "atm_mae": [0.01],
            "wing_rmse": [0.01],
            "within_bid_ask_count": [1],
            "within_bid_ask_ratio": [1.0],
            "negative_gamma_count": [0],
            "negative_gamma_ratio": [0.0],
            "delta_smoothness_violation_count": [0],
            "delta_smoothness_violation_ratio": [0.0],
            "calendar_violation_count": [0],
            "calendar_violation_ratio": [0.0],
            "calendar_groups_checked": [1],
            "runtime_mode": ["live_strict"],
            "ssvi_backend": ["cpp"],
            "fdm_backend": ["cpp"],
        }
    ).with_columns(pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"))
    surface_diagnostics_paths = derived_store._append_dataset_sync(
        surface_diagnostics,
        dataset="surface_diagnostics",
    )
    assert surface_diagnostics_paths

    runtime_metrics = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": ["2026-01-01T00:00:00Z"],
            "batch_id": ["b1"],
            "version": [1],
            "snapshot_kind": ["manual_snapshot"],
            "source_mode": ["ui_refresh_manual"],
            "trading_date": ["2026-01-01"],
            "runtime_mode": ["live_strict"],
            "ingestion_ms": [1.0],
            "mapping_ms": [2.0],
            "routing_ms": [3.0],
            "calibration_ms": [4.0],
            "pricing_ms": [5.0],
            "ui_bridge_ms": [6.0],
            "persist_ms": [7.0],
            "total_ms": [8.0],
            "overlay_prep_ms": [0.0],
            "hydrate_ms": [0.0],
            "raw_rows": [1],
            "greeks_rows": [1],
            "surface_rows": [1],
            "surface_summary_rows": [1],
            "diagnostics_rows": [1],
            "state_bytes_total": [100],
            "state_bytes_raw": [10],
            "state_bytes_greeks": [20],
            "drop_raw": [0],
            "drop_greeks": [0],
            "drop_overlay": [0],
            "drop_surface_points": [0],
        }
    ).with_columns(pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"))
    runtime_metrics_paths = derived_store._append_dataset_sync(runtime_metrics, dataset="runtime_metrics")
    assert runtime_metrics_paths

    focus_expiry_summary = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": ["2026-01-01T00:00:00Z"],
            "batch_id": ["b1"],
            "trading_date": ["2026-01-01"],
            "snapshot_kind": ["manual_snapshot"],
            "source_mode": ["ui_review"],
            "focus_label": ["0DTE"],
            "focus_order": [0],
            "expiration": ["2026-01-01"],
            "days_to_expiry": [0],
            "row_count": [2],
            "eligible_rows": [2],
            "eligible_ratio": [1.0],
            "within_bid_ask_ratio": [1.0],
            "one_sided_ratio": [0.0],
            "strip_shape_fail_ratio": [0.0],
            "atm_iv_ref": [0.2],
            "atm_market_mid": [4.1],
            "iv_skew_wing_diff": [0.01],
            "volume_sum": [100],
            "open_interest_sum": [200],
            "trust_score": [90.0],
            "trust_status": ["trusted"],
            "snapshot_age_sec": [2.0],
        }
    ).with_columns(
        pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
        pl.col("expiration").str.to_date(format="%Y-%m-%d"),
    )
    focus_expiry_summary_paths = derived_store._append_dataset_sync(
        focus_expiry_summary,
        dataset="focus_expiry_summary",
    )
    assert focus_expiry_summary_paths

    dealer_exposure_points = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": ["2026-01-01T00:00:00Z"],
            "batch_id": ["b1"],
            "trading_date": ["2026-01-01"],
            "snapshot_kind": ["manual_snapshot"],
            "source_mode": ["ui_review"],
            "focus_label": ["0DTE"],
            "focus_order": [0],
            "expiration": ["2026-01-01"],
            "days_to_expiry": [0],
            "option_type": ["call"],
            "strike": [450.0],
            "underlying_price": [449.0],
            "volume": [100],
            "open_interest": [200],
            "eligible_ratio": [1.0],
            "within_bid_ask_ratio": [1.0],
            "avg_market_mid": [4.1],
            "avg_iv_ref": [0.2],
            "delta_exposure_oi": [10000.0],
            "gamma_exposure_oi": [30000.0],
            "vega_exposure_oi": [500.0],
            "delta_exposure_volume_proxy": [5000.0],
            "gamma_exposure_volume_proxy": [15000.0],
            "vega_exposure_volume_proxy": [250.0],
        }
    ).with_columns(
        pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
        pl.col("expiration").str.to_date(format="%Y-%m-%d"),
    )
    dealer_exposure_paths = derived_store._append_dataset_sync(
        dealer_exposure_points,
        dataset="dealer_exposure_points",
    )
    assert dealer_exposure_paths

    flow_proxy_points = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": ["2026-01-01T00:00:00Z"],
            "batch_id": ["b1"],
            "trading_date": ["2026-01-01"],
            "snapshot_kind": ["manual_snapshot"],
            "source_mode": ["ui_review"],
            "focus_label": ["0DTE"],
            "focus_order": [0],
            "expiration": ["2026-01-01"],
            "days_to_expiry": [0],
            "option_type": ["call"],
            "strike": [450.0],
            "volume": [100],
            "open_interest": [200],
            "delta_volume": [10],
            "delta_open_interest": [20],
            "delta_avg_market_mid": [0.1],
            "delta_avg_iv_ref": [0.01],
            "delta_delta_exposure_oi": [1000.0],
            "delta_gamma_exposure_oi": [2000.0],
            "delta_vega_exposure_oi": [100.0],
            "proxy_confidence": [0.6],
            "proxy_reason": ["snapshot_delta_proxy_not_trade_tape"],
        }
    ).with_columns(
        pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
        pl.col("expiration").str.to_date(format="%Y-%m-%d"),
    )
    flow_proxy_paths = derived_store._append_dataset_sync(flow_proxy_points, dataset="flow_proxy_points")
    assert flow_proxy_paths

    scanner_levels = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": ["2026-01-01T00:00:00Z"],
            "batch_id": ["b1"],
            "trading_date": ["2026-01-01"],
            "snapshot_kind": ["manual_snapshot"],
            "source_mode": ["ui_review"],
            "focus_label": ["0DTE"],
            "focus_order": [0],
            "expiration": ["2026-01-01"],
            "days_to_expiry": [0],
            "strike": [450.0],
            "total_volume": [100],
            "total_open_interest": [200],
            "call_volume": [60],
            "put_volume": [40],
            "call_open_interest": [120],
            "put_open_interest": [80],
            "avg_market_mid": [4.1],
            "avg_iv_ref": [0.2],
            "eligible_ratio": [1.0],
            "within_bid_ask_ratio": [1.0],
            "one_sided_ratio": [0.0],
            "strip_shape_fail_ratio": [0.0],
            "net_delta_exposure_oi": [10000.0],
            "net_gamma_exposure_oi": [30000.0],
            "net_vega_exposure_oi": [500.0],
            "abs_gamma_exposure_oi": [30000.0],
            "hotspot_score": [10.0],
        }
    ).with_columns(
        pl.col("asof_ts").str.to_datetime(format="%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
        pl.col("expiration").str.to_date(format="%Y-%m-%d"),
    )
    scanner_levels_paths = derived_store._append_dataset_sync(scanner_levels, dataset="scanner_levels")
    assert scanner_levels_paths

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
    out_surface_points = duck.query_polars("SELECT COUNT(*) AS n FROM surface_points")
    assert out_surface_points["n"][0] == 1
    out_surface_diag = duck.query_polars("SELECT COUNT(*) AS n FROM surface_diagnostics")
    assert out_surface_diag["n"][0] == 1
    out_runtime_metrics = duck.query_polars("SELECT COUNT(*) AS n FROM runtime_metrics")
    assert out_runtime_metrics["n"][0] == 1
    out_focus_summary = duck.query_polars("SELECT COUNT(*) AS n FROM focus_expiry_summary")
    assert out_focus_summary["n"][0] == 1
    out_dealer_exposure = duck.query_polars("SELECT COUNT(*) AS n FROM dealer_exposure_points")
    assert out_dealer_exposure["n"][0] == 1
    out_flow_proxy = duck.query_polars("SELECT COUNT(*) AS n FROM flow_proxy_points")
    assert out_flow_proxy["n"][0] == 1
    out_scanner_levels = duck.query_polars("SELECT COUNT(*) AS n FROM scanner_levels")
    assert out_scanner_levels["n"][0] == 1

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


