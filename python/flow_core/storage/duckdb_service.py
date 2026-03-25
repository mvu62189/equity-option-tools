from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl


class DuckDBService:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = duckdb.connect(db_path)

    def register_empty_view(self, name: str, columns_sql: str) -> None:
        self._conn.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT {columns_sql} WHERE FALSE")

    def register_hive_dataset(self, name: str, root: str | Path) -> None:
        glob_path = Path(root).as_posix() + "/**/*.parquet"
        self._conn.execute(
            f"CREATE OR REPLACE VIEW {name} AS "
            f"SELECT * FROM read_parquet('{glob_path}', hive_partitioning=true, union_by_name=true)"
        )

    def register_dataset_or_empty(self, name: str, root: str | Path, columns_sql: str) -> None:
        if Path(root).exists():
            self.register_hive_dataset(name, root)
            return
        self.register_empty_view(name, columns_sql)

    def register_default_datasets(self, raw_root: str | Path, derived_root: str | Path) -> None:
        self.register_hive_dataset("option_quotes", raw_root)
        parity_root = Path(derived_root) / "parity"
        parity_detail_root = Path(derived_root) / "parity_detail"
        dispatch_root = Path(derived_root) / "dispatch"
        ssvi_root = Path(derived_root) / "ssvi"
        calibration_root = Path(derived_root) / "diagnostics"
        greeks_root = Path(derived_root) / "greeks"
        quote_quality_root = Path(derived_root) / "quote_quality_points"
        surface_points_root = Path(derived_root) / "surface_points"
        surface_diagnostics_root = Path(derived_root) / "surface_diagnostics"
        focus_summary_root = Path(derived_root) / "focus_expiry_summary"
        dealer_exposure_root = Path(derived_root) / "dealer_exposure_points"
        flow_proxy_root = Path(derived_root) / "flow_proxy_points"
        scanner_levels_root = Path(derived_root) / "scanner_levels"
        runtime_metrics_root = Path(derived_root) / "runtime_metrics"
        catalog_root = Path(derived_root) / "snapshot_catalog"
        oi_refresh_root = Path(derived_root) / "oi_refresh_deltas"
        if parity_root.exists():
            self.register_hive_dataset("parity_diagnostics", parity_root)
        if parity_detail_root.exists():
            self.register_hive_dataset("parity_detail_diagnostics", parity_detail_root)
        if dispatch_root.exists():
            self.register_hive_dataset("dispatch_diagnostics", dispatch_root)
        if ssvi_root.exists():
            self.register_hive_dataset("ssvi_diagnostics", ssvi_root)
        if calibration_root.exists():
            self.register_hive_dataset("calibration_diagnostics", calibration_root)
        if greeks_root.exists():
            self.register_hive_dataset("routed_greeks", greeks_root)
        self.register_dataset_or_empty(
            "quote_quality_points",
            quote_quality_root,
            """
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS VARCHAR) AS contract_symbol,
            CAST(NULL AS TIMESTAMPTZ) AS asof_ts,
            CAST(NULL AS VARCHAR) AS batch_id,
            CAST(NULL AS VARCHAR) AS trading_date,
            CAST(NULL AS VARCHAR) AS snapshot_kind,
            CAST(NULL AS VARCHAR) AS source_mode,
            CAST(NULL AS DATE) AS expiration,
            CAST(NULL AS VARCHAR) AS option_type,
            CAST(NULL AS DOUBLE) AS strike,
            CAST(NULL AS DOUBLE) AS bid,
            CAST(NULL AS DOUBLE) AS ask,
            CAST(NULL AS DOUBLE) AS market_mid,
            CAST(NULL AS DOUBLE) AS iv_bid,
            CAST(NULL AS DOUBLE) AS iv_ask,
            CAST(NULL AS DOUBLE) AS iv_ref,
            CAST(NULL AS DOUBLE) AS vendor_iv_ref,
            CAST(NULL AS DOUBLE) AS dual_delta_bid,
            CAST(NULL AS DOUBLE) AS dual_delta_ask,
            CAST(NULL AS DOUBLE) AS dual_delta_ref,
            CAST(NULL AS DOUBLE) AS price_second_derivative_ref,
            CAST(NULL AS BOOLEAN) AS eligible,
            CAST(NULL AS VARCHAR) AS drop_reason,
            CAST(NULL AS BOOLEAN) AS one_sided_market,
            CAST(NULL AS BOOLEAN) AS duplicate_conflict,
            CAST(NULL AS BOOLEAN) AS strip_shape_fail
            """,
        )
        self.register_dataset_or_empty(
            "surface_points",
            surface_points_root,
            """
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS VARCHAR) AS contract_symbol,
            CAST(NULL AS TIMESTAMPTZ) AS asof_ts,
            CAST(NULL AS VARCHAR) AS batch_id,
            CAST(NULL AS VARCHAR) AS trading_date,
            CAST(NULL AS VARCHAR) AS snapshot_kind,
            CAST(NULL AS VARCHAR) AS source_mode,
            CAST(NULL AS TIMESTAMPTZ) AS expiration,
            CAST(NULL AS VARCHAR) AS option_type,
            CAST(NULL AS DOUBLE) AS strike,
            CAST(NULL AS BIGINT) AS days_to_expiry,
            CAST(NULL AS DOUBLE) AS underlying_price,
            CAST(NULL AS DOUBLE) AS implied_vol,
            CAST(NULL AS DOUBLE) AS iv_bid,
            CAST(NULL AS DOUBLE) AS iv_ask,
            CAST(NULL AS DOUBLE) AS iv_ref,
            CAST(NULL AS DOUBLE) AS vendor_iv_ref,
            CAST(NULL AS DOUBLE) AS market_mid,
            CAST(NULL AS DOUBLE) AS model_price,
            CAST(NULL AS DOUBLE) AS american_model_price,
            CAST(NULL AS DOUBLE) AS delta,
            CAST(NULL AS DOUBLE) AS gamma,
            CAST(NULL AS DOUBLE) AS theta,
            CAST(NULL AS DOUBLE) AS vega,
            CAST(NULL AS DOUBLE) AS rho,
            CAST(NULL AS DOUBLE) AS dual_delta_bid,
            CAST(NULL AS DOUBLE) AS dual_delta_ask,
            CAST(NULL AS DOUBLE) AS dual_delta_ref,
            CAST(NULL AS DOUBLE) AS price_second_derivative_ref,
            CAST(NULL AS BOOLEAN) AS eligible,
            CAST(NULL AS VARCHAR) AS drop_reason,
            CAST(NULL AS BOOLEAN) AS one_sided_market,
            CAST(NULL AS BOOLEAN) AS duplicate_conflict,
            CAST(NULL AS BOOLEAN) AS strip_shape_fail,
            CAST(NULL AS DOUBLE) AS model_implied_vol,
            CAST(NULL AS DOUBLE) AS price_error_abs,
            CAST(NULL AS DOUBLE) AS price_error_rel,
            CAST(NULL AS DOUBLE) AS vol_error_abs,
            CAST(NULL AS DOUBLE) AS vol_error_rel,
            CAST(NULL AS BOOLEAN) AS within_bid_ask,
            CAST(NULL AS DOUBLE) AS bid_ask_width,
            CAST(NULL AS DOUBLE) AS log_moneyness,
            CAST(NULL AS DOUBLE) AS atm_distance,
            CAST(NULL AS BOOLEAN) AS is_negative_gamma,
            CAST(NULL AS BOOLEAN) AS delta_smoothness_violation,
            CAST(NULL AS DOUBLE) AS calendar_total_variance,
            CAST(NULL AS BOOLEAN) AS calendar_violation
            """,
        )
        self.register_dataset_or_empty(
            "surface_diagnostics",
            surface_diagnostics_root,
            """
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS TIMESTAMPTZ) AS asof_ts,
            CAST(NULL AS VARCHAR) AS batch_id,
            CAST(NULL AS VARCHAR) AS trading_date,
            CAST(NULL AS VARCHAR) AS snapshot_kind,
            CAST(NULL AS VARCHAR) AS source_mode,
            CAST(NULL AS VARCHAR) AS runtime_mode,
            CAST(NULL AS VARCHAR) AS ssvi_backend,
            CAST(NULL AS VARCHAR) AS fdm_backend,
            CAST(NULL AS BIGINT) AS rows,
            CAST(NULL AS BIGINT) AS groups,
            CAST(NULL AS BIGINT) AS expiry_count,
            CAST(NULL AS BIGINT) AS strike_count,
            CAST(NULL AS BIGINT) AS failure_count,
            CAST(NULL AS DOUBLE) AS model_implied_vol_coverage,
            CAST(NULL AS DOUBLE) AS price_rmse,
            CAST(NULL AS DOUBLE) AS vol_rmse,
            CAST(NULL AS DOUBLE) AS atm_mae,
            CAST(NULL AS DOUBLE) AS wing_rmse,
            CAST(NULL AS BIGINT) AS within_bid_ask_count,
            CAST(NULL AS DOUBLE) AS within_bid_ask_ratio,
            CAST(NULL AS DOUBLE) AS american_within_bid_ask_ratio,
            CAST(NULL AS BIGINT) AS negative_gamma_count,
            CAST(NULL AS DOUBLE) AS negative_gamma_ratio,
            CAST(NULL AS BIGINT) AS delta_smoothness_violation_count,
            CAST(NULL AS DOUBLE) AS delta_smoothness_violation_ratio,
            CAST(NULL AS BIGINT) AS calendar_violation_count,
            CAST(NULL AS DOUBLE) AS calendar_violation_ratio,
            CAST(NULL AS BIGINT) AS calendar_groups_checked,
            CAST(NULL AS BIGINT) AS one_sided_drop_count,
            CAST(NULL AS BIGINT) AS duplicate_conflict_count,
            CAST(NULL AS BIGINT) AS strip_shape_fail_count,
            CAST(NULL AS BIGINT) AS core_eligible_rows,
            CAST(NULL AS BIGINT) AS density_negative_count
            """,
        )
        self.register_dataset_or_empty(
            "focus_expiry_summary",
            focus_summary_root,
            """
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS TIMESTAMPTZ) AS asof_ts,
            CAST(NULL AS VARCHAR) AS batch_id,
            CAST(NULL AS VARCHAR) AS trading_date,
            CAST(NULL AS VARCHAR) AS snapshot_kind,
            CAST(NULL AS VARCHAR) AS source_mode,
            CAST(NULL AS VARCHAR) AS focus_label,
            CAST(NULL AS BIGINT) AS focus_order,
            CAST(NULL AS DATE) AS expiration,
            CAST(NULL AS BIGINT) AS days_to_expiry,
            CAST(NULL AS BIGINT) AS row_count,
            CAST(NULL AS BIGINT) AS eligible_rows,
            CAST(NULL AS DOUBLE) AS eligible_ratio,
            CAST(NULL AS DOUBLE) AS within_bid_ask_ratio,
            CAST(NULL AS DOUBLE) AS one_sided_ratio,
            CAST(NULL AS DOUBLE) AS strip_shape_fail_ratio,
            CAST(NULL AS DOUBLE) AS atm_iv_ref,
            CAST(NULL AS DOUBLE) AS atm_market_mid,
            CAST(NULL AS DOUBLE) AS iv_skew_wing_diff,
            CAST(NULL AS BIGINT) AS volume_sum,
            CAST(NULL AS BIGINT) AS open_interest_sum,
            CAST(NULL AS DOUBLE) AS trust_score,
            CAST(NULL AS VARCHAR) AS trust_status,
            CAST(NULL AS DOUBLE) AS snapshot_age_sec
            """,
        )
        self.register_dataset_or_empty(
            "dealer_exposure_points",
            dealer_exposure_root,
            """
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS TIMESTAMPTZ) AS asof_ts,
            CAST(NULL AS VARCHAR) AS batch_id,
            CAST(NULL AS VARCHAR) AS trading_date,
            CAST(NULL AS VARCHAR) AS snapshot_kind,
            CAST(NULL AS VARCHAR) AS source_mode,
            CAST(NULL AS VARCHAR) AS focus_label,
            CAST(NULL AS BIGINT) AS focus_order,
            CAST(NULL AS DATE) AS expiration,
            CAST(NULL AS BIGINT) AS days_to_expiry,
            CAST(NULL AS VARCHAR) AS option_type,
            CAST(NULL AS DOUBLE) AS strike,
            CAST(NULL AS DOUBLE) AS underlying_price,
            CAST(NULL AS BIGINT) AS volume,
            CAST(NULL AS BIGINT) AS open_interest,
            CAST(NULL AS DOUBLE) AS eligible_ratio,
            CAST(NULL AS DOUBLE) AS within_bid_ask_ratio,
            CAST(NULL AS DOUBLE) AS avg_market_mid,
            CAST(NULL AS DOUBLE) AS avg_iv_ref,
            CAST(NULL AS DOUBLE) AS delta_exposure_oi,
            CAST(NULL AS DOUBLE) AS gamma_exposure_oi,
            CAST(NULL AS DOUBLE) AS vega_exposure_oi,
            CAST(NULL AS DOUBLE) AS delta_exposure_volume_proxy,
            CAST(NULL AS DOUBLE) AS gamma_exposure_volume_proxy,
            CAST(NULL AS DOUBLE) AS vega_exposure_volume_proxy
            """,
        )
        self.register_dataset_or_empty(
            "flow_proxy_points",
            flow_proxy_root,
            """
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS TIMESTAMPTZ) AS asof_ts,
            CAST(NULL AS VARCHAR) AS batch_id,
            CAST(NULL AS VARCHAR) AS trading_date,
            CAST(NULL AS VARCHAR) AS snapshot_kind,
            CAST(NULL AS VARCHAR) AS source_mode,
            CAST(NULL AS VARCHAR) AS focus_label,
            CAST(NULL AS BIGINT) AS focus_order,
            CAST(NULL AS DATE) AS expiration,
            CAST(NULL AS BIGINT) AS days_to_expiry,
            CAST(NULL AS VARCHAR) AS option_type,
            CAST(NULL AS DOUBLE) AS strike,
            CAST(NULL AS BIGINT) AS volume,
            CAST(NULL AS BIGINT) AS open_interest,
            CAST(NULL AS BIGINT) AS delta_volume,
            CAST(NULL AS BIGINT) AS delta_open_interest,
            CAST(NULL AS DOUBLE) AS delta_avg_market_mid,
            CAST(NULL AS DOUBLE) AS delta_avg_iv_ref,
            CAST(NULL AS DOUBLE) AS delta_delta_exposure_oi,
            CAST(NULL AS DOUBLE) AS delta_gamma_exposure_oi,
            CAST(NULL AS DOUBLE) AS delta_vega_exposure_oi,
            CAST(NULL AS DOUBLE) AS proxy_confidence,
            CAST(NULL AS VARCHAR) AS proxy_reason
            """,
        )
        self.register_dataset_or_empty(
            "scanner_levels",
            scanner_levels_root,
            """
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS TIMESTAMPTZ) AS asof_ts,
            CAST(NULL AS VARCHAR) AS batch_id,
            CAST(NULL AS VARCHAR) AS trading_date,
            CAST(NULL AS VARCHAR) AS snapshot_kind,
            CAST(NULL AS VARCHAR) AS source_mode,
            CAST(NULL AS VARCHAR) AS focus_label,
            CAST(NULL AS BIGINT) AS focus_order,
            CAST(NULL AS DATE) AS expiration,
            CAST(NULL AS BIGINT) AS days_to_expiry,
            CAST(NULL AS DOUBLE) AS strike,
            CAST(NULL AS BIGINT) AS total_volume,
            CAST(NULL AS BIGINT) AS total_open_interest,
            CAST(NULL AS BIGINT) AS call_volume,
            CAST(NULL AS BIGINT) AS put_volume,
            CAST(NULL AS BIGINT) AS call_open_interest,
            CAST(NULL AS BIGINT) AS put_open_interest,
            CAST(NULL AS DOUBLE) AS avg_market_mid,
            CAST(NULL AS DOUBLE) AS avg_iv_ref,
            CAST(NULL AS DOUBLE) AS eligible_ratio,
            CAST(NULL AS DOUBLE) AS within_bid_ask_ratio,
            CAST(NULL AS DOUBLE) AS one_sided_ratio,
            CAST(NULL AS DOUBLE) AS strip_shape_fail_ratio,
            CAST(NULL AS DOUBLE) AS net_delta_exposure_oi,
            CAST(NULL AS DOUBLE) AS net_gamma_exposure_oi,
            CAST(NULL AS DOUBLE) AS net_vega_exposure_oi,
            CAST(NULL AS DOUBLE) AS abs_gamma_exposure_oi,
            CAST(NULL AS DOUBLE) AS hotspot_score
            """,
        )
        self.register_dataset_or_empty(
            "runtime_metrics",
            runtime_metrics_root,
            """
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS TIMESTAMPTZ) AS asof_ts,
            CAST(NULL AS VARCHAR) AS batch_id,
            CAST(NULL AS BIGINT) AS version,
            CAST(NULL AS VARCHAR) AS snapshot_kind,
            CAST(NULL AS VARCHAR) AS source_mode,
            CAST(NULL AS VARCHAR) AS trading_date,
            CAST(NULL AS VARCHAR) AS runtime_mode,
            CAST(NULL AS DOUBLE) AS ingestion_ms,
            CAST(NULL AS DOUBLE) AS mapping_ms,
            CAST(NULL AS DOUBLE) AS routing_ms,
            CAST(NULL AS DOUBLE) AS calibration_ms,
            CAST(NULL AS DOUBLE) AS pricing_ms,
            CAST(NULL AS DOUBLE) AS ui_bridge_ms,
            CAST(NULL AS DOUBLE) AS persist_ms,
            CAST(NULL AS DOUBLE) AS total_ms,
            CAST(NULL AS DOUBLE) AS overlay_prep_ms,
            CAST(NULL AS DOUBLE) AS hydrate_ms,
            CAST(NULL AS BIGINT) AS raw_rows,
            CAST(NULL AS BIGINT) AS greeks_rows,
            CAST(NULL AS BIGINT) AS surface_rows,
            CAST(NULL AS BIGINT) AS surface_summary_rows,
            CAST(NULL AS BIGINT) AS diagnostics_rows,
            CAST(NULL AS BIGINT) AS state_bytes_total,
            CAST(NULL AS BIGINT) AS state_bytes_raw,
            CAST(NULL AS BIGINT) AS state_bytes_greeks,
            CAST(NULL AS BIGINT) AS drop_raw,
            CAST(NULL AS BIGINT) AS drop_greeks,
            CAST(NULL AS BIGINT) AS drop_overlay,
            CAST(NULL AS BIGINT) AS drop_surface_points
            """,
        )
        self.register_dataset_or_empty(
            "snapshot_catalog",
            catalog_root,
            """
            CAST(NULL AS VARCHAR) AS batch_id,
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS TIMESTAMPTZ) AS asof_ts,
            CAST(NULL AS TIMESTAMPTZ) AS updated_at_utc,
            CAST(NULL AS VARCHAR) AS trading_date,
            CAST(NULL AS VARCHAR) AS snapshot_kind,
            CAST(NULL AS VARCHAR) AS source_mode,
            CAST(NULL AS BOOLEAN) AS is_final_for_day,
            CAST(NULL AS VARCHAR) AS parent_batch_id,
            CAST(NULL AS BIGINT) AS raw_rows,
            CAST(NULL AS BIGINT) AS greeks_rows,
            CAST(NULL AS BIGINT) AS diagnostics_rows
            """,
        )
        self.register_dataset_or_empty(
            "oi_refresh_deltas",
            oi_refresh_root,
            """
            CAST(NULL AS VARCHAR) AS batch_id,
            CAST(NULL AS VARCHAR) AS symbol,
            CAST(NULL AS TIMESTAMPTZ) AS asof_ts,
            CAST(NULL AS VARCHAR) AS trading_date,
            CAST(NULL AS VARCHAR) AS snapshot_kind,
            CAST(NULL AS VARCHAR) AS source_mode,
            CAST(NULL AS VARCHAR) AS parent_batch_id,
            CAST(NULL AS DATE) AS expiration,
            CAST(NULL AS VARCHAR) AS option_type,
            CAST(NULL AS DOUBLE) AS strike,
            CAST(NULL AS BIGINT) AS volume,
            CAST(NULL AS BIGINT) AS open_interest
            """,
        )

    def execute_sql_file(self, path: str | Path, ignore_errors: bool = False) -> None:
        text = Path(path).read_text(encoding="utf-8")
        for statement in text.split(";"):
            stmt = statement.strip()
            if stmt:
                try:
                    self._conn.execute(stmt)
                except Exception:
                    if not ignore_errors:
                        raise

    def query_polars(self, sql: str) -> pl.DataFrame:
        return self._conn.sql(sql).pl()

    def close(self) -> None:
        self._conn.close()
