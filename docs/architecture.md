# Architecture

Status: Canonical reference for the current implemented architecture.

## Runtime Layers

- Launcher and runtime control:
  `python -m flow_app` selects `ui_review`, `ui_live`, `headless_live`, or `snapshot_once`, saves the last-used launch settings, and keeps the older scripts as compatibility shims.
- Market-data ingestion:
  provider adapters, currently `yfinance`, are mapped into one canonical options-quote schema before any pricing or calibration runs.
- Quote cleaning and European conversion:
  raw American bid/ask quotes are checked for unusable rows, converted into European-equivalent bid/ask prices, and then converted into implied-volatility bid/ask ranges that can be used by the volatility model.
- Storage and replay:
  each completed run is published as one complete in-memory snapshot, saved to parquet, indexed in the saved-snapshot catalog, and exposed through DuckDB views.
- Quant layer:
  Black-Scholes baseline tools, SSVI volatility calibration, American-option pricing adapters, parity checks, model-surface Greeks, and compiled `quantcore` bindings for the latency-sensitive paths.
- UI layer:
  PySide6 shows the SPY short-expiry scanner, option-chain review, calibration diagnostics, Processing Trace, calendar/density views, and runtime metrics.
- Compiled bindings:
  C++20 and pybind11 provide the high-speed backends, with explicit GIL release and startup capability checks in strict mode.

## Concurrency Topology

- Main thread:
  Qt event loop and rendering.
- Worker thread:
  asyncio ingestion, calibration, pricing, persistence, and batch assembly.
- Overlay preparation:
  one worker with a newest-update-wins queue.
- Live refresh model:
  short-expiry polling and slower full-surface refresh both run in the same worker thread.
- Snapshot publication:
  the UI receives one complete batch at a time from `LiveStateStore` rather than partial frames stitched together in the UI.

## State Model

- `BatchPayload` is built on the worker thread and published as one complete unit.
- `SymbolSnapshot` is immutable once published, so readers never see half-updated data.
- A snapshot can include:
  - raw option quotes
  - fitted-volatility summaries by expiry
  - older routed Greeks
  - surface-based model Greeks
  - parity checks
  - calibration quality checks
  - per-contract repricing and smoothness diagnostics
  - runtime latency and memory metrics
  - short-expiry scanner outputs
- The active review-time Greeks frame prefers surface-based model Greeks when available and falls back to older routed Greeks only when needed.
- Saved history also keeps:
  - per-contract quote-cleaning and calibration-eligibility results
  - per-contract fitted-volatility and repricing results
  - batch-level fit-quality summaries
  - saved runtime metrics
  - short-expiry scanner summaries
  - saved snapshot index
  - after-hours open-interest deltas
- The saved-history loader can read older and newer parquet files together even when saved columns changed over time. It aligns columns by name, fills missing older columns with null, and keeps readable warnings when part of the saved history fails to load.

## After-Hours Behavior

- The live path freezes at the configured market-close time and may emit one final batch for the day before stopping.
- UI startup after close loads saved data instead of pretending the stream is still live.
- Open-interest-only after-hours changes are saved as deltas against the final price-sensitive batch rather than overwriting the original final batch.

## Memory Control

- Primary guard:
  estimated bytes across saved in-memory data structures.
- Secondary guard:
  process RSS.
- Trim policy uses hysteresis and cooldown so the app does not oscillate between trim states.

## UI Rendering Contract

- The UI drops stale updates and applies only the newest complete update for a symbol.
- Heatmaps use persistent `pyqtgraph.ImageItem` objects so redraws stay cheap.
- The default overlay mode is single-canvas selection across log-moneyness, strike, and residual views.
- Side-by-side dual heatmap is still debug-gated and can disable itself if apply latency rises too far.
- Validation stays inside the main workstation rather than a separate app. The validation area includes slice views, heatmaps, calendar checks, density views, and runtime metrics.
- In `ui_review`, one selected saved snapshot drives the Option Chain, Greeks, overlay, model-vs-market, validation, calendar/density, runtime views, short-expiry scanner, and Processing Trace.
- The review-mode `Option Chain` tab shows the saved raw option chain directly so the user can inspect market quotes before interpreting downstream model outputs.
- `Pull Full Snapshot` captures a new full-surface snapshot, saves it, and switches the UI to that new saved snapshot without restart.
- The landing page is scanner-first:
  `0DTE`, `1DTE`, and `EOW` expiry cards, strike heatmap, scanner levels, activity estimates from snapshot changes, and data-quality/freshness badges.
- Detailed review remains tab-based. Scanner cards sync the detailed expiry selectors without changing the live polling selection.
- When `option_type=all`, line plots keep calls and puts as separate curves.
- `Processing Trace` is the step-by-step review page for one expiry. It shows raw American quotes, cleaned American quotes, European-equivalent prices, implied-volatility bid/ask ranges, fitted SSVI volatility, European repricing, and American repricing.
- Cached prebuilt chart and table data is kept for the selected saved snapshot so the scanner, validation, calendar/density, Processing Trace, model-vs-market, and runtime-metrics pages do not rebuild large chart inputs on every control change.
- Chart rules are explicit:
  - single-expiry views do not show meaningless expiry heatmaps
  - `option_type=all` keeps calls and puts separate
  - price views compare market American bid/ask/mid with the American model price
  - line plots auto-range to current data and expose separate `Auto X`, `Auto Y`, and `Auto Both` resets

## Contracts

The canonical quote schema is defined in `python/flow_core/contracts/schema.py`. Every provider feed must be mapped into that schema before any calibration, pricing, or Greeks processing begins.

## Calibration Contract

- Calibration is organized by expiry.
- The starting point is cleaned American bid/ask quotes.
- Those quotes are converted into European-equivalent bid/ask prices.
- The European-equivalent bid and ask prices are then converted into an implied-volatility bid/ask range at each strike.
- The main fitted object is one SSVI implied-volatility curve per expiry.
- When both calls and puts are present:
  - OTM puts supply the left wing
  - OTM calls supply the right wing
  - only the ATM strike, or the two strikes bracketing forward, blends call-side and put-side implied-volatility ranges into one ATM input
- ITM quotes remain in the saved diagnostics and plots, but they do not drive the main fit when both OTM wings are available.
- If one side of the surface is completely missing for an expiry, the current implementation can temporarily fit the available side so review and diagnostics still produce output.
- The main calibration penalty is for fitted SSVI volatility moving outside the market-implied volatility bid/ask range.
- Midpoint or reference implied volatility is only a light guide inside that bid/ask range.
- European and American price comparisons are validation layers after calibration, not the main fitting target.
- Production review Greeks are derived from American prices via bump-and-reprice under sticky delta in moneyness space.
- Python and compiled `quantcore` now use the same SSVI fitting process and the same fit rules for the implied-volatility bid/ask range.
