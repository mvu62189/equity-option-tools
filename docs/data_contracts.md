# Data Contracts

Status: Canonical reference for current raw and derived data contracts.

## Canonical Quote Schema

Every upstream feed is first mapped into one canonical option-quote table.

Canonical quote columns:

- `symbol`: underlying ticker
- `contract_symbol`: provider contract identifier for the specific option
- `asof_ts`: quote timestamp in UTC
- `expiration`: option expiry date
- `option_type`: `call` or `put`
- `strike`: option strike
- `bid`: market bid price
- `ask`: market ask price
- `last`: most recent trade price carried by the provider
- `volume`: provider volume field
- `open_interest`: provider open-interest field
- `underlying_price`: underlying spot price used by the provider row
- `implied_vol_vendor`: provider-supplied implied volatility, kept only as a reference
- `provider`: data-source identifier
- `snapshot_id`: provider snapshot identifier when available

Canonical type rules:

- `asof_ts`: UTC timestamp
- `expiration`: date
- `strike`, `bid`, `ask`, `last`, `underlying_price`, `implied_vol_vendor`: float
- `volume`, `open_interest`: integer

Validation policy:

- Required columns must exist and be coercible.
- The raw mapper only drops rows when both `bid` and `ask` are zero.
- Zero-volume rows are retained because they can still carry useful open-interest state.
- `symbol` is the underlying ticker in the normal runtime path; `contract_symbol` preserves the provider’s contract identifier.

## Persisted Batch Metadata

Most saved raw and derived datasets also carry batch metadata describing the saved run:

- `batch_id`: unique saved snapshot identifier
- `symbol`
- `asof_ts`
- `trading_date`
- `snapshot_kind`
- `source_mode`

Some datasets also carry:

- `parent_batch_id`: link back to the final batch that an after-hours update depends on
- `updated_at_utc`
- `is_final_for_day`
- `runtime_mode`

## Persisted History Read Behavior

- Saved parquet history is allowed to evolve by column over time.
- Review-mode readers load parquet files one by one, align columns by name, and fill missing older columns with null rather than failing the whole dataset.
- If a dataset still fails to read, the UI shows a warning so the operator can tell the difference between:
  - no saved history
  - saved history exists, but part of it failed to load

## Runtime Config Contract

Launch-session settings are saved separately from repo-tracked YAML defaults.

Launch-session fields:

- `mode`
- `ticker`
- `expiration`
- `refresh_ms`
- `allow_shared`
- `provider_config`
- `pipeline_config`

Pipeline config fields also include the short-expiry refresh model:

- `live_focus_labels`
- `live_hot_poll_seconds`
- `live_full_snapshot_poll_seconds`

## Snapshot Kind Semantics

- `live_batch`: normal live worker output
- `manual_snapshot`: one-shot snapshot or user-triggered recompute before the final-price window
- `eod_final`: final batch captured by a live-owned session after freeze time
- `eod_final_refresh`: after-hours final-price recompute triggered from the UI
- `eod_oi_refresh`: open-interest-only reconciliation without price-sensitive recompute
- `offline_bootstrap`: saved snapshot reloaded for review mode

## Derived Dataset Contracts

- `dispatch`:
  saved per-expiry routing summary. Main fields include expiry, implied-volatility engine, Greeks engine, contract count, and average/min/max implied volatility.

- `greeks`:
  saved legacy contract-by-contract prices and Greeks from the older routed engine path. This dataset still includes pricing outputs, Greeks, engine choice, backend choice, and fallback provenance.

- `model_greeks`:
  saved surface-based contract-by-contract prices and Greeks. This is the default review-time Greeks source. It is produced from American prices using bump-and-reprice under sticky delta in moneyness space. Important provenance fields include `greeks_source`, `sticky_mode`, and `ssvi_fit_space`.

- `quote_quality_points`:
  saved per-contract quote cleaning and calibration-eligibility results.
  Important field groups:
  - market quote context:
    `contract_symbol`, `market_mid`, `log_moneyness`, `atm_distance`
  - quote-quality flags:
    `one_sided_market`, `crossed_market`, `duplicate_conflict`, `exact_duplicate`, `strip_shape_fail`, `strip_shape_reason`, `drop_reason`
  - eligibility fields:
    `eligible_prestrip`, `eligible`, `eligible_for_fit`, `excluded_from_fit_reason`
  - European-equivalent prices:
    `euro_price_bid`, `euro_price_ask`, `euro_price_ref`
  - implied-volatility range:
    implied volatility from the de-Americanized bid price (`iv_bid`), implied volatility from the de-Americanized ask price (`iv_ask`), reference implied volatility from the cleaned European input (`iv_ref`), and vendor-supplied implied volatility kept for comparison (`vendor_iv_ref`)
  - strike-shape diagnostics:
    `dual_delta_bid`, `dual_delta_ask`, `dual_delta_ref`, `price_second_derivative_ref`
  - weighting and fit-use metadata:
    `corridor_tightness`, `corridor_width`, `weight_uniform`, `weight_atm`, `weight_corridor_tightness`, `weight_atm_corridor_tightness`, `fit_region`, `is_atm_blend`, `blend_source`, `surface_source`

- `ssvi`:
  saved per-expiry SSVI calibration summaries. This dataset describes one fitted implied-volatility curve per expiry and records which quotes were used, which fit space was used, whether the fit converged, the fitted parameters, and which backend was used. `backend_used` can now be `python` or `cpp` for the same fitted process.

- `parity`:
  saved expiry-level put-call-parity model comparison summary. It records the winning model, parity errors, RMSE values, gap between models, number of pairs checked, and expiry maturity in years.

- `parity_detail`:
  saved strike-level parity comparison detail, including parity error, relative error, European call/put prices, right-hand-side parity value, and maturity in years.

- `diagnostics`:
  saved calibration and convergence summary by expiry and model. It includes model id, backend used, runtime mode, convergence flag, iteration count, final SSE, butterfly no-arbitrage pass/fail, failure reason, interpolation mode, and fitted parameter record.

- `surface_points`:
  saved per-contract fitted-volatility, repricing, and validation results.
  Important field groups:
  - implied-volatility inputs:
    `iv_bid`, `iv_ask`, `iv_ref`, `vendor_iv_ref`
  - fitted model volatility:
    fitted SSVI volatility (`ssvi_vol`), fitted lower/upper comparison bounds (`ssvi_vol_lower`, `ssvi_vol_upper`), and a flag showing whether fitted SSVI volatility sits outside the implied-volatility bid/ask range (`ssvi_vol_outside_band`)
  - prices:
    market midpoint (`market_mid`), legacy model price (`model_price`), American model price (`american_model_price`), European model price implied by fitted SSVI volatility (`ssvi_euro_price`), and American model price after re-Americanizing the fitted surface (`ssvi_american_price`)
  - price-range checks:
    `euro_price_inside_band`, `american_price_inside_band`, `within_bid_ask`, `bid_ask_width`, `price_error_abs`, `price_error_rel`
  - volatility-range checks:
    `model_implied_vol`, `vol_error_abs`, `vol_error_rel`
  - strike-shape and smoothness diagnostics:
    `dual_delta_bid`, `dual_delta_ask`, `dual_delta_ref`, `price_second_derivative_ref`, `is_negative_gamma`, `delta_smoothness_violation`
  - calendar diagnostics:
    `calendar_total_variance`, `calendar_violation`
  - fit provenance:
    `fit_region`, `is_atm_blend`, `blend_source`, `eligible`, `eligible_for_fit`, `excluded_from_fit_reason`, `greeks_source`, `model_batch_role`, `one_sided_market`, `duplicate_conflict`, `strip_shape_fail`

- `surface_diagnostics`:
  saved batch-level calibration quality summary.
  Important groups:
  - coverage and size:
    `rows`, `groups`, `expiry_count`, `strike_count`, `failure_count`, `model_implied_vol_coverage`
  - fit error:
    `price_rmse`, `vol_rmse`, `atm_mae`, `wing_rmse`
  - bid/ask range checks:
    `within_bid_ask_count`, `within_bid_ask_ratio`, `american_within_bid_ask_ratio`
  - smoothness and arbitrage checks:
    `negative_gamma_count`, `negative_gamma_ratio`, `delta_smoothness_violation_count`, `delta_smoothness_violation_ratio`, `calendar_violation_count`, `calendar_violation_ratio`, `calendar_groups_checked`
  - quote-cleaning summary:
    `one_sided_drop_count`, `duplicate_conflict_count`, `strip_shape_fail_count`, `core_eligible_rows`, `density_negative_count`

- `runtime_metrics`:
  saved pipeline timing and performance summary.
  Important fields:
  - stage timings:
    `ingestion_ms`, `mapping_ms`, `routing_ms`, `calibration_ms`, `pricing_ms`, `ui_bridge_ms`, `persist_ms`, `total_ms`
  - extra timings:
    `overlay_prep_ms`, `hydrate_ms`
  - row counts:
    `raw_rows`, `greeks_rows`, `surface_rows`, `surface_summary_rows`, `diagnostics_rows`
  - memory and state size:
    `state_bytes_total`, `state_bytes_raw`, `state_bytes_greeks`
  - stale-update drops:
    `drop_raw`, `drop_greeks`, `drop_overlay`, `drop_surface_points`

- `focus_expiry_summary`:
  saved one-row summary for each short-expiry focus bucket such as `0DTE`, `1DTE`, and `EOW`. It includes row counts, usable-row ratio, ATM implied volatility, skew, volume, open interest, data-quality score/status, and snapshot age.

- `dealer_exposure_points`:
  saved strike-by-expiry dealer-style exposure estimates. It includes strike, option type, volume, open interest, average market midpoint, average reference implied volatility, and delta/gamma/vega exposure estimates from both open interest and volume proxies.

- `flow_proxy_points`:
  saved activity estimates inferred from changes between snapshots. It includes changes in volume, open interest, market midpoint, reference implied volatility, and exposure estimates, together with `proxy_confidence` and `proxy_reason`. These are not tape-derived trades.

- `scanner_levels`:
  saved key strike levels for the short-expiry scanner. It includes total and side-specific volume/open interest, average market midpoint, average reference implied volatility, usability ratios, and net/absolute exposure measures.

- `snapshot_catalog`:
  saved snapshot index. There is one row per coherent saved batch, with `batch_id`, symbol, timestamp, trading date, snapshot kind, source mode, final-for-day flag, parent linkage, and row counts.

- `oi_refresh_deltas`:
  saved after-hours open-interest reconciliation rows keyed by parent snapshot, expiry, option type, strike, volume, and open interest.

## DuckDB Default Views

The default DuckDB registration layer exposes:

- `option_quotes`
- `routed_greeks`
- `model_greeks`
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
