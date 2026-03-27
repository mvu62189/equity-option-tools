# System Design

## Goal

Turn the app into a scanner-first SPY workstation without discarding the existing calibration and validation stack.

## Shipped Shape

- Landing page: `Short Expiry Scanner`
- Detailed review: existing overlay, model-vs-market, validation, calendar/density, temporal, and routing tabs
- Data-quality layer: quote cleaning, fitted-surface checks, and runtime metrics already used elsewhere in the app

## Runtime Model

- `ui_live` now uses two refresh intervals:
  - fast focused refreshes for `0DTE`, `1DTE`, and `EOW`
  - slower full-surface refreshes so validation pages still have full-surface context
- The async side still runs in one worker thread. The change is in what gets refreshed and how often, not in the overall concurrency design.

## Storage Model

The feature adds four saved derived datasets:

- `focus_expiry_summary`
- `dealer_exposure_points`
- `flow_proxy_points`
- `scanner_levels`

These sit beside the existing saved validation datasets:

- `quote_quality_points`
- `surface_points`
- `surface_diagnostics`
- `runtime_metrics`

## Reused Chart And Table Data

The UI now keeps cached prebuilt chart and table data for the selected saved snapshot on the scanner, model-vs-market, validation, calendar/density, and runtime-metrics pages.

This keeps the rendering rules simple:

- one chart/table cache per window
- clear the cache when the selected saved snapshot changes
- reuse the same prepared chart data while the user changes controls inside that saved snapshot

## Data-Quality Model

The scanner does not invent a separate quality stack. It reads the same validation backbone already used elsewhere:

- quote eligibility and market bid/ask range behavior
- checks that model prices stay inside bid/ask
- strip-shape diagnostics
- fitted-surface diagnostics
- runtime freshness and latency

## Known Limits

- Phase 1 is SPY-only.
- `flow_proxy_points` are changes between saved snapshots, not tape-derived flow.
- The short-expiry scanner is optimized for current `yfinance` polling reality, not for exchange-tape semantics.
