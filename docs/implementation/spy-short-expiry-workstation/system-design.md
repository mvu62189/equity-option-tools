# System Design

## Goal

Turn the app into a scanner-first SPY workstation without discarding the existing calibration and validation stack.

## Shipped Shape

- Landing page: `Short Expiry Scanner`
- Drilldown: existing overlay, model-vs-market, validation, calendar/density, temporal, and routing tabs
- Trust layer: quote quality, surface diagnostics, runtime metrics, and existing calibration validation

## Runtime Model

- `ui_live` now has two cadences:
  - hot focused fetches for `0DTE`, `1DTE`, and `EOW`
  - slower full-surface refreshes for validation context
- The worker stays single-threaded on the async side. The change is in fetch scope and cadence, not in the concurrency topology.

## Storage Model

The feature adds four persisted derived datasets:

- `focus_expiry_summary`
- `dealer_exposure_points`
- `flow_proxy_points`
- `scanner_levels`

These sit beside the existing validation datasets:

- `quote_quality_points`
- `surface_points`
- `surface_diagnostics`
- `runtime_metrics`

## UI Payload Strategy

The UI now uses a batch-scoped `PagePayloadCache` for scanner, model-vs-market, validation, calendar/density, and runtime-metrics pages.

This keeps the rendering contract simple:

- one payload cache per window
- cache invalidated on batch change
- reused across control changes inside the same batch

## Trust Model

The scanner does not invent a new trust stack. It reads the same validation backbone already used elsewhere:

- quote eligibility and corridor behavior
- within-bid/ask checks
- strip-shape diagnostics
- surface diagnostics
- runtime freshness and latency

## Known Limits

- Phase 1 is SPY-only.
- `flow_proxy_points` are snapshot deltas, not tape-derived flow.
- The short-expiry scanner is optimized for current `yfinance` polling reality, not for exchange-tape semantics.
