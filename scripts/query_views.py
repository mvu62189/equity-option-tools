from __future__ import annotations

import argparse

from flow_core.storage import DuckDBService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query default quote/diagnostics views from parquet datasets")
    parser.add_argument("--raw-root", default="data/raw")
    parser.add_argument("--derived-root", default="data/derived")
    parser.add_argument("--sql-file", default="sql/options_views.sql")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    duck = DuckDBService()
    duck.register_default_datasets(args.raw_root, args.derived_root)
    duck.execute_sql_file(args.sql_file, ignore_errors=True)

    print("option_quotes rows:")
    for row in duck.query_polars("SELECT COUNT(*) AS n FROM option_quotes").to_dicts():
        print(row)

    try:
        print("parity winners (top 10):")
        for row in duck.query_polars(
            "SELECT * FROM v_parity_winners ORDER BY asof_ts DESC, expiration LIMIT 10"
        ).to_dicts():
            print(row)
    except Exception as exc:
        print(f"parity view unavailable: {exc}")

    try:
        print("dispatch summary (top 10):")
        for row in duck.query_polars(
            "SELECT * FROM v_dispatch_summary ORDER BY asof_ts DESC, expiration LIMIT 10"
        ).to_dicts():
            print(row)
    except Exception as exc:
        print(f"dispatch view unavailable: {exc}")

    try:
        print("parity detail by strike (top 10):")
        for row in duck.query_polars(
            "SELECT * FROM v_parity_by_strike ORDER BY asof_ts DESC, expiration, strike, model LIMIT 10"
        ).to_dicts():
            print(row)
    except Exception as exc:
        print(f"parity detail view unavailable: {exc}")

    try:
        print("ssvi summary (top 10):")
        for row in duck.query_polars("SELECT * FROM v_ssvi_summary ORDER BY asof_ts DESC LIMIT 10").to_dicts():
            print(row)
    except Exception as exc:
        print(f"ssvi view unavailable: {exc}")

    try:
        print("calibration diagnostics (top 10):")
        for row in duck.query_polars(
            "SELECT * FROM v_calibration_diagnostics ORDER BY asof_ts DESC, expiration LIMIT 10"
        ).to_dicts():
            print(row)
    except Exception as exc:
        print(f"calibration diagnostics view unavailable: {exc}")

    try:
        print("routed greeks (top 10):")
        for row in duck.query_polars(
            "SELECT * FROM v_routed_greeks ORDER BY asof_ts DESC, expiration, strike LIMIT 10"
        ).to_dicts():
            print(row)
    except Exception as exc:
        print(f"routed greeks view unavailable: {exc}")

    duck.close()


if __name__ == "__main__":
    main()
