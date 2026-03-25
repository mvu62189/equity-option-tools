# Data Contracts

Status: Canonical reference for current raw and derived data contracts.

## Canonical Quote Schema

Canonical quote columns:

- `symbol`
- `contract_symbol`
- `asof_ts`
- `expiration`
- `option_type`
- `strike`
- `bid`
- `ask`
- `last`
- `volume`
- `open_interest`
- `underlying_price`
- `implied_vol_vendor`
- `provider`
- `snapshot_id`

Canonical type rules:

- `asof_ts`: UTC timestamp
- `expiration`: date
- `strike`, `bid`, `ask`, `last`, `underlying_price`, `implied_vol_vendor`: float
- `volume`, `open_interest`: integer

Validation policy:

- Required columns must exist and be coercible.
- Raw mapper drops rows only when `bid == 0 && ask == 0`.
- Zero volume rows are retained to preserve open-interest state.
- `symbol` is the underlying ticker in normal runtime paths; `contract_symbol` preserves the provider contract identifier.

## Persisted Batch Metadata

Most persisted raw and derived datasets also carry batch metadata:

- `batch_id`: unique pipeline batch identifier
- `symbol`
- `asof_ts`
- `trading_date`
- `snapshot_kind`
- `source_mode`

Some datasets also carry:

- `parent_batch_id`: linkage back to the final batch an OI refresh or bootstrap depends on
- `updated_at_utc`
- `is_final_for_day`
- `runtime_mode`

## Runtime Config Contract

The current runtime/session layer persists launch config separately from repo-tracked YAML defaults.

- Launch/session fields:
  - `mode`
  - `ticker`
  - `expiration`
  - `refresh_ms`
  - `allow_shared`
  - `provider_config`
  - `pipeline_config`
- Pipeline config fields now include short-expiry workstation cadence settings:
  - `live_focus_labels`
  - `live_hot_poll_seconds`
  - `live_full_snapshot_poll_seconds`

## Snapshot Kind Semantics

- `live_batch`: normal live worker output
- `manual_snapshot`: manual one-shot snapshot or user-triggered recompute before final-price window
- `eod_final`: final computed batch captured by a live-owned session after freeze time
- `eod_final_refresh`: after-hours final-price recompute triggered from the UI
- `eod_oi_refresh`: OI-only reconciliation without price-sensitive recompute
- `offline_bootstrap`: stored batch reloaded for UI review mode

## Derived Dataset Contracts

- `dispatch`:
  - `expiration`, `iv_engine`, `greeks_engine`, `contracts`, `avg_iv`, `min_iv`, `max_iv`
- `greeks`:
  - contract-level routed output used by the UI and DuckDB, including price and Greek values plus routing and fallback provenance such as engine choice, backend choice, and fallback reason
- `quote_quality_points`:
  - row-level quote-cleaning and corridor dataset with `contract_symbol`, `market_mid`, `log_moneyness`, `atm_distance`, `one_sided_market`, `crossed_market`, `duplicate_conflict`, `exact_duplicate`, `eligible_prestrip`, `eligible`, `drop_reason`, `strip_shape_fail`, `iv_bid`, `iv_ask`, `iv_ref`, `vendor_iv_ref`, `euro_price_bid`, `euro_price_ask`, `euro_price_ref`, `dual_delta_bid`, `dual_delta_ask`, `dual_delta_ref`, `price_second_derivative_ref`, `corridor_tightness`, `weight_uniform`, `weight_atm`, `weight_corridor_tightness`, `weight_atm_corridor_tightness`
- `ssvi`:
  - per-expiry and per-option-type calibration summaries consumed by the SSVI and validation views; includes `option_type`, `weight_mode`, `fit_space`, objective/iteration/success fields, parameters, backend provenance, and optional compare-space results
- `parity`:
  - `symbol`, `expiration`, `winner_model`, `bjerksund_error`, `luba_error`, `bjerksund_rmse`, `luba_rmse`, `winner_gap`, `pairs`, `tau_years`, `asof_ts`
- `parity_detail`:
  - `symbol`, `expiration`, `strike`, `model`, `parity_error`, `relative_error`, `call_eur`, `put_eur`, `parity_rhs`, `tau_years`, `asof_ts`
- `diagnostics`:
  - `symbol`, `asof_ts`, `expiration`, `batch_id`, `snapshot_kind`, `source_mode`, `trading_date`, `model_id`, `backend_used`, `runtime_mode`, `converged`, `iterations`, `sse_final`, `durrleman_pass`, `failure_reason`, `jump_interp_mode`, `params`
- `surface_points`:
  - per-contract validation dataset with routed Greeks plus quote-quality enrichment; important fields include `contract_symbol`, `implied_vol`, `iv_bid`, `iv_ask`, `iv_ref`, `vendor_iv_ref`, `market_mid`, `model_price`, `american_model_price`, `dual_delta_bid`, `dual_delta_ask`, `dual_delta_ref`, `price_second_derivative_ref`, `model_implied_vol`, `price_error_abs`, `price_error_rel`, `vol_error_abs`, `vol_error_rel`, `within_bid_ask`, `bid_ask_width`, `log_moneyness`, `atm_distance`, `eligible`, `drop_reason`, `one_sided_market`, `duplicate_conflict`, `strip_shape_fail`, `is_negative_gamma`, `delta_smoothness_violation`, `calendar_total_variance`, `calendar_violation`
- `surface_diagnostics`:
  - batch-summary validation dataset with `rows`, `groups`, `expiry_count`, `strike_count`, `failure_count`, `model_implied_vol_coverage`, `price_rmse`, `vol_rmse`, `atm_mae`, `wing_rmse`, `within_bid_ask_count`, `within_bid_ask_ratio`, `american_within_bid_ask_ratio`, `negative_gamma_count`, `negative_gamma_ratio`, `delta_smoothness_violation_count`, `delta_smoothness_violation_ratio`, `calendar_violation_count`, `calendar_violation_ratio`, `calendar_groups_checked`, `one_sided_drop_count`, `duplicate_conflict_count`, `strip_shape_fail_count`, `core_eligible_rows`, `density_negative_count`
- `runtime_metrics`:
  - batch runtime dataset with `version`, `runtime_mode`, `ingestion_ms`, `mapping_ms`, `routing_ms`, `calibration_ms`, `pricing_ms`, `ui_bridge_ms`, `persist_ms`, `total_ms`, `overlay_prep_ms`, `hydrate_ms`, `raw_rows`, `greeks_rows`, `surface_rows`, `surface_summary_rows`, `diagnostics_rows`, `state_bytes_total`, `state_bytes_raw`, `state_bytes_greeks`, `drop_raw`, `drop_greeks`, `drop_overlay`, `drop_surface_points`
- `focus_expiry_summary`:
  - one row per focused expiry bucket with `focus_label`, `focus_order`, `expiration`, `days_to_expiry`, `row_count`, `eligible_rows`, `eligible_ratio`, `within_bid_ask_ratio`, `one_sided_ratio`, `strip_shape_fail_ratio`, `atm_iv_ref`, `atm_market_mid`, `iv_skew_wing_diff`, `volume_sum`, `open_interest_sum`, `trust_score`, `trust_status`, `snapshot_age_sec`
- `dealer_exposure_points`:
  - strike-by-expiry scanner dataset with `focus_label`, `expiration`, `days_to_expiry`, `option_type`, `strike`, `underlying_price`, `volume`, `open_interest`, `eligible_ratio`, `within_bid_ask_ratio`, `avg_market_mid`, `avg_iv_ref`, `delta_exposure_oi`, `gamma_exposure_oi`, `vega_exposure_oi`, `delta_exposure_volume_proxy`, `gamma_exposure_volume_proxy`, `vega_exposure_volume_proxy`
- `flow_proxy_points`:
  - batch-over-batch proxy analytics with `focus_label`, `expiration`, `option_type`, `strike`, `volume`, `open_interest`, `delta_volume`, `delta_open_interest`, `delta_avg_market_mid`, `delta_avg_iv_ref`, `delta_delta_exposure_oi`, `delta_gamma_exposure_oi`, `delta_vega_exposure_oi`, `proxy_confidence`, `proxy_reason`
- `scanner_levels`:
  - strike-ladder hotspot dataset with `focus_label`, `expiration`, `days_to_expiry`, `strike`, `total_volume`, `total_open_interest`, `call_volume`, `put_volume`, `call_open_interest`, `put_open_interest`, `avg_market_mid`, `avg_iv_ref`, `eligible_ratio`, `within_bid_ask_ratio`, `one_sided_ratio`, `strip_shape_fail_ratio`, `net_delta_exposure_oi`, `net_gamma_exposure_oi`, `net_vega_exposure_oi`, `abs_gamma_exposure_oi`, `hotspot_score`
- `snapshot_catalog`:
  - one row per coherent persisted batch with `batch_id`, `symbol`, `asof_ts`, `updated_at_utc`, `trading_date`, `snapshot_kind`, `source_mode`, `is_final_for_day`, `parent_batch_id`, `raw_rows`, `greeks_rows`, `diagnostics_rows`
- `oi_refresh_deltas`:
  - after-hours OI reconciliation rows keyed by `parent_batch_id`, `expiration`, `option_type`, `strike`, `volume`, and `open_interest`

## DuckDB Default Views

The default DuckDB registration layer exposes:

- `option_quotes`
- `routed_greeks`
- `quote_quality_points`
- `ssvi_diagnostics`
- `dispatch_diagnostics`
- `parity_diagnostics`
- `parity_detail_diagnostics`
- `calibration_diagnostics`
- `surface_points`
- `surface_diagnostics`
- `runtime_metrics`
- `focus_expiry_summary`
- `dealer_exposure_points`
- `flow_proxy_points`
- `scanner_levels`
- `snapshot_catalog`
- `oi_refresh_deltas`
