# Operations

Status: Canonical reference for current runtime and operational behavior.

## Startup Modes

- `python -m flow_app` is the primary entrypoint and opens the launcher by default.
- `ui_review` opens the desktop UI, hydrates the latest stored snapshot when available, and does not start a live worker.
- `ui_live` opens the same UI and starts one asyncio live worker thread when launched before the market-close freeze time.
- `headless_live` runs the unified ingestion/persistence path without the UI.
- `snapshot_once` captures one full provider snapshot, runs the pipeline once, persists outputs, and prints the captured row count.
- Legacy `scripts/run_*.py` commands remain available as compatibility shims and log deprecation warnings.
- Launch-session settings persist to `%LOCALAPPDATA%\quant-pipeline-mvp\launch_config.json`.
- The UI is now scanner-first for SPY short-expiry review: landing page first, drilldown tabs second, validation tabs third.

## Live Mode

- Hot focused cadence defaults to 15 seconds for `0DTE`, `1DTE`, and `EOW`.
- Full-surface refresh cadence defaults to 300 seconds for validation context and broader surface state.
- If provider latency or repeated failures rise, the live worker may back off to 30-second hot cadence and 600-second full-surface cadence.
- Retry policy: bounded retries with linear backoff.
- Runtime strictness:
  - `runtime_mode=live_strict`: C++ core backends required at startup; no silent fallback for SSVI/FDM.
  - `runtime_mode=live_research|backtest`: Python fallback allowed with explicit telemetry tags.
- Runtime topology:
  - Qt main thread for rendering.
  - asyncio worker thread for ingestion + quant.
  - single overlay-prep executor with latest-wins queue.
- Raw writes are buffered and flushed to:
  `data/raw/year=YYYY/month=MM/day=DD/ticker=SYMBOL/`.
- Derived writes are buffered and flushed to:
  - `data/derived/dispatch/year=.../symbol=.../`
  - `data/derived/greeks/year=.../symbol=.../`
  - `data/derived/quote_quality_points/year=.../symbol=.../`
  - `data/derived/ssvi/year=.../symbol=.../`
  - `data/derived/parity/year=.../symbol=.../`
  - `data/derived/parity_detail/year=.../symbol=.../`
  - `data/derived/diagnostics/year=.../symbol=.../`
  - `data/derived/surface_points/year=.../symbol=.../`
  - `data/derived/surface_diagnostics/year=.../symbol=.../`
  - `data/derived/runtime_metrics/year=.../symbol=.../`
  - `data/derived/focus_expiry_summary/year=.../symbol=.../`
  - `data/derived/dealer_exposure_points/year=.../symbol=.../`
  - `data/derived/flow_proxy_points/year=.../symbol=.../`
  - `data/derived/scanner_levels/year=.../symbol=.../`
  - `data/derived/snapshot_catalog/year=.../symbol=.../`
  - `data/derived/oi_refresh_deltas/year=.../symbol=.../`

## Timeline And After-Hours Flow

- Live freeze defaults to `17:00 America/New_York`.
- When a live-owned session reaches freeze time, it may capture one `eod_final` batch and then stop live polling.
- `headless_live` launched after freeze time does not start a live session.
- `ui_live` launched after freeze time opens the UI but skips the live worker and behaves like review mode until the user refreshes.
- Between freeze time and final-price refresh time, manual refresh hydrates the latest stored snapshot and tells the user when the final-price window opens.
- Final-price refresh defaults to `17:30 America/New_York`; from then on, a manual UI refresh may produce `eod_final_refresh`.
- OI refresh defaults to `20:30 America/New_York`; when only OI and volume changed, the refresh path writes `eod_oi_refresh` deltas without recomputing price-sensitive outputs.
- Offline bootstrap mode defaults to `parquet_latest_final`: UI startup first tries `snapshot_catalog`, then overlays same-day `oi_refresh_deltas` when reopening a stored final batch.

## Memory Guardrails

- Primary metric: state-estimated bytes in `LiveStateStore`.
- Secondary metric: process RSS.
- Soft limit policy:
  - require 3 consecutive soft-limit breaches.
  - enforce cooldown before next trim.
- Hard limit policy:
  - immediate trim.
  - force `gc.collect()` after trim and re-measure.

## Backpressure

- UI consumes coalesced updates with latest-wins policy.
- At most one pending version per symbol is retained.
- Stale updates are dropped and counted.

## Lifecycle

- `python -m flow_app --mode ui_live --no-launcher` starts the UI plus the live worker thread and attaches a stop event.
- `python -m flow_app --mode headless_live --no-launcher` is the unified headless ingestion and persistence path.
- `python -m flow_app --mode snapshot_once --no-launcher` captures and persists one full snapshot.
- Stream lock guard path: `data/runlocks/<SYMBOL>.lock`.
- On shutdown:
  - stop ingestion loop.
  - drain coalesced updates.
  - flush buffered parquet writers.
  - emit shutdown summary logs.

## SPY Short-Expiry Workstation

- The landing page targets one symbol in phase 1: `SPY`.
- Focus buckets are `0DTE`, `1DTE`, and `EOW`.
- Expiry cards show the selected focus expiry, trust status, trust score, and snapshot age.
- Scanner heatmap shows strike against focus bucket with gamma-OI exposure as the default color field.
- Scanner levels show strike hotspots and dealer-style exposure summaries.
- `flow_proxy_points` are explicit snapshot-to-snapshot heuristics. They are not true tape-derived order flow and must stay labeled as proxy analytics in the UI and docs.
- Clicking a scanner expiry card syncs the detailed expiry selectors used by overlay, model-vs-market, validation, calendar/density, and temporal tabs without altering the live polling selection.

## Telemetry

- Per-stage latency: `ingestion_ms`, `mapping_ms`, `routing_ms`, `calibration_ms`, `pricing_ms`,
  `ui_bridge_ms`, `persist_ms`, `total_ms` (legacy aliases retained for compatibility).
- Additional runtime metrics: `overlay_prep_ms`, `hydrate_ms`, row-count totals, state-byte totals,
  and drop counters for `raw`, `greeks`, `overlay`, and `surface_points`.
- Buffer metrics: `flush_rows`, `flush_files`, `flush_ms`, `buffer_depth`.
- Memory metrics: state bytes, RSS bytes, trim reason (`soft` or `hard`).
- Validation metrics persist as `surface_points`, `surface_diagnostics`, and `runtime_metrics` so
  chart and debug views can be replayed from parquet and queried through DuckDB.
- Scanner datasets persist as `focus_expiry_summary`, `dealer_exposure_points`, `flow_proxy_points`,
  and `scanner_levels`.

## Quote Quality And Calibration Eligibility

- Canonical raw mapping still preserves most provider rows so storage and replay stay faithful to the source feed.
- Calibration eligibility is stricter and now runs through `quote_quality_points` before SSVI:
  - one-sided markets (`bid <= 0` or `ask <= 0`) are excluded
  - crossed markets (`ask < bid`) are excluded
  - exact duplicate rows are collapsed
  - conflicting duplicates are retained but flagged and excluded from calibration
  - strip-shape failures are retained for diagnostics but excluded from calibration
- Bid/ask strips are de-Americanized per expiry and option type, then converted into:
  - `iv_bid`
  - `iv_ask`
  - `iv_ref`
  - `dual_delta_bid`
  - `dual_delta_ask`
  - `dual_delta_ref`
- If a strip reference cannot produce a finite `iv_ref` for a node, the temporary fallback is the self-derived European mid-price IV for that node. Vendor IV is not the intended calibration fallback.
- `ssvi` summaries are now emitted per expiry, option type, and weight mode. The default production path uses `atm_only`; research summaries also persist `uniform` and `atm_x_corridor_tightness`.

## Validation Workspace

- Price slices now compare quoted American `bid`/`ask`/`market_mid` against American model price.
- Volatility slices can show `iv_bid`, `iv_ask`, `iv_ref`, `vendor_iv_ref`, and `model_implied_vol`.
- Dual-delta and convexity diagnostics persist to `surface_points` and are available in the Validation Workspace as line and heatmap metrics.
- When `option_type=all`, line plots keep calls and puts as separate series rather than merging them into one line. Heatmaps still collapse to one option type when the selected view cannot render both simultaneously.
- Single-expiry validation and calendar contexts hide expiry heatmaps instead of rendering redundant one-row axes.

## Local Testing

- Local pytest scratch data defaults to `%TEMP%\equity-option-tools-pytest\...`.
- CI continues to use runner-owned temp directories.
- The old repo-local `.tmp` test tree is scratch data only and can be deleted safely if stale or corrupted.
