# Data Contracts

Canonical quote columns:
- symbol, asof_ts, expiration, option_type, strike, bid, ask, last,
  volume, open_interest, underlying_price, implied_vol_vendor, provider, snapshot_id.

Validation policy:
- Required columns must exist and be coercible.
- Rows with bid==0 and ask==0 are dropped.
- Zero volume rows are retained to preserve open-interest state.

Derived diagnostics contracts:
- Parity diagnostics:
  - symbol, expiration, winner_model, bjerksund_error, luba_error, bjerksund_rmse, luba_rmse,
    winner_gap, pairs, tau_years, asof_ts
- Parity detail diagnostics:
  - symbol, expiration, strike, model, parity_error, relative_error, call_eur, put_eur, parity_rhs,
    tau_years, asof_ts
- Dispatch diagnostics:
  - symbol, expiration, iv_engine, greeks_engine, contracts, avg_iv, min_iv, max_iv, asof_ts
