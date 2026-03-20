CREATE OR REPLACE VIEW v_option_quotes AS
SELECT
    symbol,
    asof_ts,
    batch_id,
    trading_date,
    snapshot_kind,
    source_mode,
    expiration,
    option_type,
    strike,
    bid,
    ask,
    last,
    volume,
    open_interest,
    underlying_price,
    implied_vol_vendor,
    provider,
    snapshot_id
FROM option_quotes;

CREATE OR REPLACE VIEW v_quote_quality AS
SELECT
    symbol,
    expiration,
    option_type,
    strike,
    asof_ts,
    CASE WHEN bid = 0 AND ask = 0 THEN 1 ELSE 0 END AS is_cross_zero,
    CASE WHEN bid > ask AND ask > 0 THEN 1 ELSE 0 END AS inverted_market,
    CASE WHEN asof_ts < NOW() - INTERVAL '5 minutes' THEN 1 ELSE 0 END AS stale_quote
FROM option_quotes;

CREATE OR REPLACE VIEW v_duplicate_contracts AS
SELECT
    symbol,
    expiration,
    option_type,
    strike,
    asof_ts,
    COUNT(*) AS row_count
FROM option_quotes
GROUP BY 1,2,3,4,5
HAVING COUNT(*) > 1;

CREATE OR REPLACE VIEW v_parity_winners AS
SELECT
    symbol,
    expiration,
    winner_model,
    bjerksund_error,
    luba_error,
    bjerksund_rmse,
    luba_rmse,
    winner_gap,
    pairs,
    tau_years,
    asof_ts
FROM parity_diagnostics;

CREATE OR REPLACE VIEW v_dispatch_summary AS
SELECT
    symbol,
    expiration,
    iv_engine,
    greeks_engine,
    contracts,
    avg_iv,
    min_iv,
    max_iv,
    asof_ts
FROM dispatch_diagnostics;

CREATE OR REPLACE VIEW v_parity_by_strike AS
SELECT
    symbol,
    expiration,
    strike,
    model,
    parity_error,
    relative_error,
    call_eur,
    put_eur,
    parity_rhs,
    tau_years,
    asof_ts
FROM parity_detail_diagnostics;

CREATE OR REPLACE VIEW v_ssvi_summary AS
SELECT
    symbol,
    fit_space,
    objective,
    iterations,
    success,
    compare_fit_space,
    compare_objective,
    compare_iterations,
    compare_success,
    asof_ts
FROM ssvi_diagnostics;

CREATE OR REPLACE VIEW v_calibration_diagnostics AS
SELECT
    symbol,
    asof_ts,
    expiration,
    batch_id,
    snapshot_kind,
    source_mode,
    trading_date,
    model_id,
    converged,
    iterations,
    sse_final,
    durrleman_pass,
    params
FROM calibration_diagnostics;

CREATE OR REPLACE VIEW v_routed_greeks AS
SELECT
    symbol,
    asof_ts,
    batch_id,
    input_snapshot_kind,
    expiration,
    option_type,
    strike,
    greeks_engine,
    engine_used,
    market_bid,
    market_ask,
    market_last,
    market_mid,
    rate_used,
    dividend_used,
    tau_years,
    price,
    model_price,
    display_price,
    display_price_source,
    delta,
    gamma,
    theta,
    vega,
    rho,
    success,
    error
FROM routed_greeks;

CREATE OR REPLACE VIEW v_snapshot_catalog AS
SELECT
    batch_id,
    symbol,
    asof_ts,
    updated_at_utc,
    trading_date,
    snapshot_kind,
    source_mode,
    is_final_for_day,
    parent_batch_id,
    raw_rows,
    greeks_rows,
    diagnostics_rows
FROM snapshot_catalog;

CREATE OR REPLACE VIEW v_latest_final_snapshot AS
SELECT *
FROM (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY asof_ts DESC, updated_at_utc DESC) AS rn
    FROM snapshot_catalog
    WHERE is_final_for_day = TRUE
) t
WHERE rn = 1;

CREATE OR REPLACE VIEW v_oi_refresh_deltas AS
SELECT
    batch_id,
    symbol,
    asof_ts,
    trading_date,
    snapshot_kind,
    source_mode,
    parent_batch_id,
    expiration,
    option_type,
    strike,
    volume,
    open_interest
FROM oi_refresh_deltas;
