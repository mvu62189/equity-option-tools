from __future__ import annotations

from typing import Dict, List, Literal

from pydantic import BaseModel, Field


class ProviderMap(BaseModel):
    provider: str
    required_fields: List[str]
    field_map: Dict[str, str]


class PipelineConfig(BaseModel):
    runtime_mode: Literal["live_strict", "live_research", "backtest"] = "live_strict"
    live_poll_seconds: int = Field(default=5, ge=1)
    live_expiry_scope: Literal["nearest", "0-7d", "0-30d", "selected", "all"] = "0-30d"
    live_selected_expiries: List[str] = Field(default_factory=list)
    snapshot_eod_time: str = "20:00"
    snapshot_timezone: str = "America/New_York"
    market_close_freeze_time: str = "17:00"
    final_prices_refresh_time: str = "17:30"
    oi_refresh_time: str = "20:30"
    after_hours_refresh_policy: Literal["oi_merge_only"] = "oi_merge_only"
    offline_bootstrap_mode: Literal["parquet_latest_final"] = "parquet_latest_final"
    price_change_abs_tol: float = Field(default=1e-4, ge=0.0)
    parquet_root: str = "data/raw"
    derived_parquet_root: str = "data/derived"
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0)
    ssvi_warm_start: bool = True
    ssvi_fit_space: Literal["log", "strike"] = "log"
    ssvi_enable_space_compare: bool = True
    ssvi_compare_fit_space: Literal["log", "strike"] = "strike"
    ssvi_backend: Literal["cpp", "python", "auto"] = "cpp"
    parity_rate: float = 0.04
    parity_dividend: float = 0.0
    parity_eep_mode: str = "hybrid"
    parity_max_pairs: int = Field(default=40, ge=1)
    parity_tree_steps: int = Field(default=120, ge=20)
    parity_luba_method: str = "luba_2pt"
    parity_rim_nodes: int = Field(default=100, ge=16)
    fdm_backend: Literal["cpp", "python", "auto"] = "cpp"
    use_yfinance_rate_curve: bool = True
    rate_curve_refresh_seconds: int = Field(default=900, ge=30)
    use_projected_dividends: bool = True
    dividend_projection_horizon_years: float = Field(default=3.0, ge=0.25)
    dividend_lookback_events: int = Field(default=8, ge=3)
    state_max_symbols: int = Field(default=3, ge=1)
    state_budget_raw_mb: int = Field(default=128, ge=8)
    state_budget_greeks_mb: int = Field(default=256, ge=16)
    state_budget_ssvi_mb: int = Field(default=32, ge=4)
    state_budget_dispatch_mb: int = Field(default=16, ge=4)
    state_budget_parity_mb: int = Field(default=64, ge=4)
    state_budget_parity_detail_mb: int = Field(default=128, ge=8)
    state_budget_diagnostics_mb: int = Field(default=64, ge=4)
    state_budget_overlay_mb: int = Field(default=128, ge=8)
    state_max_rows_raw: int = Field(default=10_000, ge=100)
    state_max_rows_greeks: int = Field(default=10_000, ge=100)
    state_max_rows_ssvi: int = Field(default=2_000, ge=50)
    state_max_rows_diagnostics: int = Field(default=2_000, ge=50)
    ui_apply_interval_ms: int = Field(default=50, ge=10)
    ui_max_pending_per_symbol: int = Field(default=1, ge=1)
    ui_overlay_default_mode: Literal["log", "strike", "residual"] = "residual"
    ui_overlay_dual_mode_enabled: bool = False
    ui_apply_p95_limit_ms: float = Field(default=50.0, ge=5.0)
    ui_auto_degrade_enabled: bool = True
    stream_lock_enforced: bool = True
    nonconvergence_alert_threshold: int = Field(default=5, ge=1)
    telemetry_emit_interval_sec: int = Field(default=30, ge=1)
    memory_soft_limit_mb: int = Field(default=1300, ge=128)
    memory_hard_limit_mb: int = Field(default=1536, ge=256)
    memory_check_interval_sec: int = Field(default=5, ge=1)
    memory_trim_cooldown_sec: int = Field(default=30, ge=1)
    diag_flush_interval_sec: int = Field(default=120, ge=5)
    parquet_flush_interval_sec: int = Field(default=5, ge=1)
    parquet_flush_max_rows: int = Field(default=5000, ge=100)


class UIConfig(BaseModel):
    window_title: str = "Quant Pipeline MVP"
    refresh_ms: int = Field(default=1000, ge=100)
    default_ticker: str = "SPY"
