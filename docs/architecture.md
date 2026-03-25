# Architecture

Status: Canonical reference for current implemented architecture.

## Runtime Layers

- Launcher/runtime: `python -m flow_app` selects `ui_review`, `ui_live`, `headless_live`, or
  `snapshot_once`, persists last-used launch settings, and keeps legacy scripts as compatibility shims.
- Ingestion: provider adapters (currently `yfinance`) -> schema mapper -> canonical Polars frame.
- Quote-quality stage: canonical frame -> bid/ask corridor diagnostics -> de-Americanized per-expiry strips
  -> self-derived `iv_ref` / dual-delta reference -> calibration-eligible slice inputs.
- Storage: atomic in-memory state snapshots + buffered parquet flush + snapshot-catalog bootstrap and
  refresh metadata + DuckDB analytics.
- Quant: Black-Scholes baseline, SSVI calibration, expiry routing dispatch, parity diagnostics, and O4
  American-IV adapter, C++ log-space FDM Greeks path, and C++ weighted log-slice SSVI calibrator.
  Vendor IV is retained for comparison, but the active SSVI input path is now self-derived from cleaned strips.
  One de-Americanization path remains MVP-grade and is still under validation.
- UI: PySide6 receives coalesced state-version signals, applies latest snapshots only, and exposes
  a SPY short-expiry scanner landing page plus Run Config, drilldown, validation, calendar/density, and runtime-metrics workspaces.
- Compute bindings: C++20/pybind11 with explicit GIL release and strict-mode startup capability checks.

## Concurrency Topology

- Main thread: Qt event loop and rendering.
- Worker thread: asyncio ingestion and quant pipeline.
- Overlay prep: single-worker executor with latest-wins queue.
- Live cadence model: hot focused short-expiry polling plus slower full-surface refresh in the same worker thread.
- Cache consistency: one atomic publish per batch via `LiveStateStore`.

## State Model

- `BatchPayload` is built in worker thread and published atomically.
- `SymbolSnapshot` is immutable for readers; UI never stitches partial frames.
- Snapshot fields include raw, greeks, ssvi, dispatch, parity, parity_detail, calibration diagnostics,
  overlay payloads, latency metrics, and drop counters.
- History datasets also retain `surface_points`, `surface_diagnostics`, and `runtime_metrics` for
  validation charts, diagnostics tables, and persisted replay and debug workflows.
- History also retains `quote_quality_points`, which is the public contract for row-level cleaning flags,
  corridor metrics, strip-shape diagnostics, and calibration eligibility.
- History also retains `focus_expiry_summary`, `dealer_exposure_points`, `flow_proxy_points`, and
  `scanner_levels` for the short-expiry workstation landing page and drilldown entrypoints.
- Persisted derived storage also retains `snapshot_catalog` and `oi_refresh_deltas` so offline bootstrap
  and after-hours OI reconciliation can reconstruct a coherent final state.

## After-Hours Behavior

- The live path freezes at the configured market-close time and may emit one `eod_final` batch before stopping.
- UI startup hydrates from stored final data first, then offers explicit refresh actions instead of pretending
  the stream is still live after close.
- OI-only after-hours updates are persisted as deltas against the final batch rather than overwriting
  price-sensitive outputs.

## Memory Control

- Primary guard: in-memory estimated bytes (`DataFrame.estimated_size` + NumPy `nbytes`) per dataset.
- Secondary guard: process RSS for operational safety.
- Trim policy uses hysteresis and cooldown to avoid repeated trim loops.

## UI Rendering Contract

- Push plus coalesce updates with latest-wins per symbol and version.
- Heatmap uses persistent `pyqtgraph.ImageItem`.
- Default overlay mode is single-canvas selector (`log`, `strike`, `residual`).
- Side-by-side dual heatmap is debug-gated and auto-degrades off when apply p95 breaches threshold.
- Validation workspace extends the main UI rather than a separate tool, with slice explorer,
  surface explorer, calendar inspector, density view, and runtime metrics views fed from persisted history.
- The landing page is scanner-first: expiry cards for `0DTE`, `1DTE`, and `EOW`, with strike heatmap,
  scanner levels, proxy-flow table, and trust/freshness badges.
- Drilldown remains tab-based; scanner cards sync the detailed expiry selectors without changing the live polling selection.
- Line views keep call and put series separate when `option_type=all`; the UI does not merge them into a
  single curve.
- Prepared page-payload caching is batch-scoped and used for scanner, model-vs-market, validation,
  calendar/density, and runtime-metrics pages to avoid rebuilding large payloads on every control change.
- Chart semantics are explicit:
  - single-expiry validation and calendar views do not show meaningless expiry heatmaps
  - `option_type=all` keeps calls and puts as separate line series
  - price-fit views compare American `bid/ask/mid` against `american_model_price`
- Update path:
  - `setImage(..., autoLevels=False, autoDownsample=True)`
  - `setRect(...)`
  - `setLevels(...)`
- Overlay arrays are generated off UI thread as contiguous `float32` NumPy buffers.

## Contracts

The canonical quote schema is defined in `python/flow_core/contracts/schema.py`.
All upstream vendor data must be mapped to this schema before any quant processing.
