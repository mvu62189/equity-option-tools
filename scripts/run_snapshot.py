from __future__ import annotations

import argparse
import asyncio

from flow_core.config import PipelineConfig, ProviderMap, load_yaml
from flow_core.ingestion.providers import YFinanceAdapter
from flow_core.orchestration import InMemoryQuoteCache, QuantPipelineService
from flow_core.orchestration.state_store import LiveStateStore
from flow_core.quant.market_inputs import HybridDividendSource, TBillRateCurve
from flow_core.storage import ParquetStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture full option snapshot")
    parser.add_argument("--ticker", default="SPY")
    parser.add_argument("--provider-config", default="configs/providers/yfinance.yaml")
    parser.add_argument("--pipeline-config", default="configs/pipeline/default.yaml")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    provider_map = load_yaml(args.provider_config, ProviderMap)
    config = load_yaml(args.pipeline_config, PipelineConfig)
    rate_curve = (
        TBillRateCurve(refresh_seconds=config.rate_curve_refresh_seconds) if config.use_yfinance_rate_curve else None
    )
    dividend_source = (
        HybridDividendSource(
            projection_horizon_years=config.dividend_projection_horizon_years,
            lookback_events=config.dividend_lookback_events,
        )
        if config.use_projected_dividends
        else None
    )

    service = QuantPipelineService(
        adapter=YFinanceAdapter(),
        provider_map=provider_map,
        config=config,
        cache=InMemoryQuoteCache(
            _state_store=LiveStateStore(
                dataset_budgets_mb={
                    "raw": config.state_budget_raw_mb,
                    "greeks": config.state_budget_greeks_mb,
                    "ssvi": config.state_budget_ssvi_mb,
                    "dispatch": config.state_budget_dispatch_mb,
                    "parity": config.state_budget_parity_mb,
                    "parity_detail": config.state_budget_parity_detail_mb,
                    "diagnostics": config.state_budget_diagnostics_mb,
                    "overlay": config.state_budget_overlay_mb,
                },
                dataset_row_caps={
                    "raw": config.state_max_rows_raw,
                    "greeks": config.state_max_rows_greeks,
                    "ssvi": config.state_max_rows_ssvi,
                    "diagnostics": config.state_max_rows_diagnostics,
                },
                max_symbols=config.state_max_symbols,
            )
        ),
        parquet_store=ParquetStore(config.parquet_root),
        derived_store=ParquetStore(config.derived_parquet_root),
        rate_curve=rate_curve,
        dividend_source=dividend_source,
    )

    frame = await service.capture_snapshot(args.ticker)
    print(f"snapshot rows={frame.height}")


if __name__ == "__main__":
    asyncio.run(main())
