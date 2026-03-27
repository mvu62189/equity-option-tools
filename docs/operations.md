# Operations

Status: Canonical reference for current runtime behavior and operator workflow.

## Startup Modes

- `python -m flow_app` is the main entrypoint and opens the launcher by default.
- `ui_review` opens the desktop UI, loads the latest available saved snapshot when one exists, and does not start live polling.
- In `ui_review`, one selected saved snapshot drives the Option Chain, Greeks, overlay, model-vs-market, validation, calendar/density, scanner, and Processing Trace pages.
- `ui_review` shows the loaded saved option chain directly in the `Option Chain` tab, with expiry and option-type filters.
- `ui_review` also shows a saved-snapshot selector and `Pull Full Snapshot`, which captures a fresh full-surface snapshot, saves it, and switches the review screen to that new snapshot.
- `ui_live` opens the same UI and starts one live worker thread when launched before the freeze time.
- `headless_live` runs the same ingestion, calibration, and persistence flow without the UI.
- `snapshot_once` captures one full option surface, runs the pipeline once, saves the results, prints the row count, and exits.
- The older `scripts/run_*.py` commands still exist as compatibility shims and log deprecation warnings.
- Launch-session settings are stored in `%LOCALAPPDATA%\quant-pipeline-mvp\launch_config.json`.
- The UI is scanner-first for SPY short-expiry work: scanner landing page first, detailed review tabs second, validation tabs third.

## Live Mode

- The short-expiry refresh interval defaults to 15 seconds for `0DTE`, `1DTE`, and `EOW`.
- The full-surface refresh interval defaults to 300 seconds so the validation pages still have broader surface context.
- If provider latency rises or repeated fetch failures occur, the live worker can slow to a 30-second short-expiry refresh and a 600-second full-surface refresh.
- Retry policy uses bounded retries with linear backoff.
- Runtime strictness:
  - `runtime_mode=live_strict`: compiled core backends must be available at startup; there is no silent fallback for SSVI or finite-difference Greeks.
  - `runtime_mode=live_research|backtest`: Python fallback is allowed and is tagged in the saved telemetry.
- Runtime layout:
  - Qt main thread for rendering
  - one asyncio worker thread for ingestion, calibration, pricing, and persistence
  - one overlay-prep executor that drops stale work and keeps only the newest pending update

## Saved Data Layout

- Raw option quotes are written under:
  `data/raw/year=YYYY/month=MM/day=DD/ticker=SYMBOL/`
- Derived outputs are written under `data/derived/...`.
- Main derived datasets and what they mean:
  - `dispatch`: per-expiry routing summary
  - `greeks`: older routed contract-by-contract prices and Greeks
  - `model_greeks`: surface-based contract-by-contract Greeks computed from the fitted model
  - `quote_quality_points`: quote cleaning, bid/ask-range diagnostics, and calibration-eligibility results
  - `ssvi`: one fitted SSVI volatility summary per expiry
  - `surface_points`: per-contract fitted-volatility, repricing, and validation results
  - `surface_diagnostics`: batch-level fit-quality summary
  - `runtime_metrics`: pipeline timing and performance summary
  - `focus_expiry_summary`, `dealer_exposure_points`, `flow_proxy_points`, `scanner_levels`: short-expiry scanner outputs
  - `snapshot_catalog`: saved snapshot index
  - `oi_refresh_deltas`: after-hours open-interest-only updates

## Timeline And After-Hours Flow

- Live freeze defaults to `17:00 America/New_York`.
- When a live-owned session reaches freeze time, it may capture one final batch for the day and then stop live polling.
- `headless_live` launched after freeze time does not start a live session.
- `ui_live` launched after freeze time opens the UI but skips the live worker, so it behaves like review mode until the user manually refreshes.
- Between freeze time and the final-price refresh window, manual refresh loads the latest saved snapshot and tells the user when the final-price window opens.
- Final-price refresh defaults to `17:30 America/New_York`. From then on, a manual refresh may produce a final-price recompute.
- OI refresh defaults to `20:30 America/New_York`. If only OI and volume changed, the refresh path writes open-interest deltas without recomputing price-sensitive outputs.
- On startup, review mode tries to load the latest available saved snapshot from `snapshot_catalog`.
- Older and newer saved parquet files are allowed to have different columns. The loader reads them file by file, aligns columns by name, fills missing older columns with null, and records a visible warning if part of the saved history fails to read.

## Memory Guardrails

- Primary in-memory limit uses estimated bytes in `LiveStateStore`.
- Secondary limit uses process RSS.
- Soft-limit behavior:
  - requires 3 consecutive breaches
  - enforces a cooldown before another trim
- Hard-limit behavior:
  - trims immediately
  - forces `gc.collect()`
  - remeasures after trim

## Backpressure

- The UI drops stale updates and keeps only the newest complete update for each symbol.
- At most one pending version per symbol is retained.
- Dropped stale updates are counted in the saved runtime metrics.

## Lifecycle

- `python -m flow_app --mode ui_live --no-launcher` starts the UI and the live worker.
- `python -m flow_app --mode headless_live --no-launcher` runs the same ingestion and persistence flow without the UI.
- `python -m flow_app --mode snapshot_once --no-launcher` captures and saves one full snapshot.
- Stream locks live under `data/runlocks/<SYMBOL>.lock`.
- On shutdown, the app:
  - stops the ingestion loop
  - drains pending UI updates
  - flushes buffered parquet writers
  - emits shutdown summary logs

## SPY Short-Expiry Workstation

- Phase 1 is intentionally focused on one symbol: `SPY`.
- The main focus buckets are `0DTE`, `1DTE`, and `EOW`.
- Expiry cards show the focused expiry, data-quality status, data-quality score, and snapshot age.
- The scanner heatmap uses strike on one axis and focus bucket on the other, with gamma-OI exposure as the default color field.
- Scanner levels highlight important strikes by volume, open interest, and dealer-style exposure.
- The snapshot-to-snapshot activity view is only a rough activity estimate inferred from changes between saved snapshots. It is not true order flow.
- Clicking a scanner expiry card syncs the detailed expiry selectors used by overlay, model-vs-market, validation, calendar/density, and temporal pages without changing the live polling selection.

## Telemetry

- Saved stage timings include ingestion, schema mapping, routing, calibration, pricing, UI bridge, persistence, and total pipeline time.
- Additional runtime metrics include overlay preparation time, saved-snapshot load time, row counts, state-byte totals, and stale-update drop counters.
- Buffer metrics include flush rows, files, duration, and buffer depth.
- Memory metrics include state bytes, RSS bytes, and trim reason.
- These metrics are persisted so they can be replayed from parquet and queried through DuckDB.

## Quote Cleaning And Calibration Inputs

- The raw quote mapper remains intentionally permissive so saved raw history stays faithful to the source feed.
- The stricter filtering happens later in the quote-cleaning and calibration-eligibility step.
- Quotes are excluded from calibration when:
  - bid is zero or negative
  - ask is zero or negative
  - ask is below bid
  - the row is an exact duplicate of another row already kept
  - the row conflicts with another row for the same contract and cannot be trusted
  - the strike-by-strike strip fails the shape checks used for calibration eligibility
- Conflicting or unusable rows are still kept in saved diagnostics so the user can see what was rejected and why.
- For each expiry and option side, the surviving American bid and ask prices are converted into European-equivalent bid and ask prices.
- Those European-equivalent bid and ask prices are then converted into:
  - implied volatility from the de-Americanized bid price (`iv_bid`)
  - implied volatility from the de-Americanized ask price (`iv_ask`)
  - a reference implied volatility from the cleaned European reference price (`iv_ref`)
- Vendor-supplied implied volatility is still saved as a comparison reference, but it is not the intended SSVI calibration target.

## SSVI Calibration Process

- The main calibration is done in implied-volatility space, not price space.
- Each expiry gets one fitted SSVI volatility curve.
- Forward moneyness is used to decide which quotes belong to the left wing, right wing, and ATM join point.
- When both puts and calls are available:
  - OTM puts supply the left wing
  - OTM calls supply the right wing
  - at the ATM strike, or the two strikes bracketing forward, call-side and put-side implied-volatility bid/ask ranges are blended into one ATM input
- ITM quotes remain visible in diagnostics and plots, but they do not drive the main fit when both OTM wings are present.
- If one side is entirely missing for an expiry, the current implementation can fall back to the available side so the slice still produces diagnostics and an interim fit.
- The main penalty is for fitted SSVI volatility moving outside the market-implied volatility bid/ask range.
- Midpoint or reference implied volatility is used only as a light guide inside the bid/ask range.
- Python and compiled `quantcore` now follow the same SSVI fitting process. In strict live mode, the compiled slice-calibration entrypoint is required.

## Greeks And Review Validation

- `model_greeks` is the main review-time Greeks dataset.
- `greeks` remains available as the legacy routed comparison dataset.
- The UI defaults to surface-based model Greeks, but the user can switch between model and legacy sources from the review header.
- Surface-based model Greeks are computed from American prices with bump-and-reprice under sticky delta in moneyness space, then mapped back to contract strikes for display and analysis.
- European price validation checks whether the European model price implied by fitted SSVI volatility stays inside the European bid/ask range.
- American price validation checks whether the American model price implied by the fitted surface stays inside the cleaned American bid/ask range.
- Fitted SSVI volatility is also checked directly against the implied-volatility bid/ask range.
- Points are flagged when the fitted SSVI volatility or model price leaves the market range, but they are not automatically dropped from diagnostics just because they fail the check.

## Validation Workspace

- Price slices compare market American `bid`, `ask`, and `market_mid` with the American model price.
- Volatility slices can show:
  - implied volatility from de-Americanized bid price
  - implied volatility from de-Americanized ask price
  - reference implied volatility from the cleaned European input
  - vendor-supplied implied volatility for comparison
  - fitted SSVI volatility
- Dual-delta and convexity diagnostics are persisted and can be plotted as line charts or heatmaps.
- When `option_type=all`, line plots keep calls and puts as separate series instead of merging them into one line.
- Single-expiry validation and calendar views suppress meaningless expiry heatmaps.
- `Processing Trace` is the step-by-step audit page for one expiry. It shows:
  - raw American bid/ask versus cleaned American bid/ask
  - cleaned American bid/ask versus European-equivalent bid/ask/reference prices
  - implied-volatility bid/ask range versus fitted SSVI volatility
  - full call/put volatility diagnostics including excluded nodes
  - European model price versus the European bid/ask range
  - American model price versus the cleaned American bid/ask range
- Every Processing Trace series includes point markers so sparse or missing nodes are visible.
- Line plots across overlay, model-vs-market, validation, density, runtime metrics, and Processing Trace auto-range to the current data and expose separate `Auto X`, `Auto Y`, and `Auto Both` controls.

## Local Testing

- Local pytest scratch data defaults to `%TEMP%\equity-option-tools-pytest\...`.
- CI uses runner-owned temp directories.
- The old repo-local `.tmp` tree is scratch data only and can be deleted safely if stale or corrupted.
