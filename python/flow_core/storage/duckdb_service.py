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

    def register_default_datasets(self, raw_root: str | Path, derived_root: str | Path) -> None:
        self.register_hive_dataset("option_quotes", raw_root)
        parity_root = Path(derived_root) / "parity"
        parity_detail_root = Path(derived_root) / "parity_detail"
        dispatch_root = Path(derived_root) / "dispatch"
        ssvi_root = Path(derived_root) / "ssvi"
        calibration_root = Path(derived_root) / "diagnostics"
        greeks_root = Path(derived_root) / "greeks"
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
        if catalog_root.exists():
            self.register_hive_dataset("snapshot_catalog", catalog_root)
        else:
            self.register_empty_view(
                "snapshot_catalog",
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
        if oi_refresh_root.exists():
            self.register_hive_dataset("oi_refresh_deltas", oi_refresh_root)
        else:
            self.register_empty_view(
                "oi_refresh_deltas",
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
