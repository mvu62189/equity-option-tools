# Quant Pipeline MVP

Windows-first desktop workstation for live and snapshot SPY options calibration and short-expiry analytics. The current app centers on a unified launcher, a SPY short-expiry scanner, persisted batch history, validation charts, and compiled `quantcore` paths for the most latency-sensitive models.

## Start The App
```powershell
python -m flow_app
```

- This is the current primary entrypoint.
- With no flags, it opens the launcher and persists the next-launch session to `%LOCALAPPDATA%\quant-pipeline-mvp\launch_config.json`.
- Direct mode selection is also supported:

```powershell
python -m flow_app --mode ui_review --ticker SPY --no-launcher
python -m flow_app --mode ui_live --ticker SPY --no-launcher
python -m flow_app --mode headless_live --ticker SPY --no-launcher
python -m flow_app --mode snapshot_once --ticker SPY --no-launcher
```

- `scripts\run_ui.py`, `scripts\run_live.py`, `scripts\run_daemon.py`, and `scripts\run_snapshot.py` still work as compatibility shims, but they now forward into `flow_app` and log deprecation.

## Working Today

- Unified startup and persisted session config for `ui_review`, `ui_live`, `headless_live`, and `snapshot_once`.
- PySide6 desktop UI with a `Short Expiry Scanner` landing page plus Run Config, Live Chain, SSVI vs Baseline, Routed Greeks, Greeks Overlay, Model vs Market, Validation Workspace, Calendar / Density, Runtime Metrics, Temporal Greeks, Arbitrage Scanner, and Routing & Parity tabs.
- SPY-focused short-expiry workflow with `0DTE`, `1DTE`, and `EOW` expiry cards, strike heatmap, ladder view, trust/freshness badges, and click-through into the drilldown tabs.
- Offline bootstrap from the latest stored final snapshot, plus explicit final-price and OI refresh flows after market close.
- Live polling through the shipped `yfinance` adapter, canonical schema mapping, raw Parquet storage, derived Parquet storage, and DuckDB query views over raw plus derived datasets.
- `ui_live` now runs a cadence-aware short-expiry profile: focused hot polling for `0DTE`, `1DTE`, and `EOW`, plus a slower full-surface refresh loop for validation context.
- Quant pipeline with routed Greeks, quote-quality diagnostics, bid/ask-bounded strip cleaning, self-derived IV inputs for SSVI, parity diagnostics, calibration diagnostics, surface diagnostics, runtime telemetry, and C++ `quantcore` bindings for weighted log-slice SSVI, log-space FDM Greeks, BS2002 pricing/Greeks, and Laplace pricing/Greeks.
- Scanner-derived datasets now persist beside the validation stack: `focus_expiry_summary`, `dealer_exposure_points`, `flow_proxy_points`, and `scanner_levels`.
- Atomic `LiveStateStore` snapshots, latest-wins UI backpressure, persisted validation history, and memory guardrails with state-byte plus RSS tracking.
- Prepared page-payload caching in the UI so scanner, validation, calendar/density, model-vs-market, and runtime-metrics pages stop rebuilding large payloads from raw history on every control change.
- Windows-first CI in GitHub Actions for Ruff, Mypy, unit/integration tests, quantcore build/import smoke, and report-only perf artifacts.

## Working But Still Provisional

- Runtime/provider abstraction exists, but the only shipped provider today is `yfinance`.
- `runtime_mode=live_strict` is the production-safe path. `live_research` and `backtest` still permit explicit Python fallback behavior for configured backends.
- Raw provider mapping is still intentionally permissive; stricter quote cleaning now happens in the downstream `quote_quality_points` stage, where one-sided, crossed, duplicate-conflict, and strip-shape-invalid rows are excluded from calibration eligibility rather than silently removed from raw storage.
- The SSVI path now targets self-derived `iv_ref` inputs built from cleaned American bid/ask strips. Vendor IV is persisted as `vendor_iv_ref` for comparison only and is no longer the intended calibration target.
- Parity and de-Americanization diagnostics are implemented, but one early-exercise-premium estimation path still uses MVP heuristic coefficients and should be treated as under validation rather than final production math.
- The short-expiry scanner is intentionally SPY-first and snapshot-oriented. It is designed around `yfinance` polling rather than a true options trade tape.
- `flow_proxy_points` are explicit snapshot-to-snapshot heuristics, not true sweep, trade-print, or order-flow classification.
- Validation plots now keep calls and puts as separate line series when `option_type=all`, and line visibility toggles are stable, but the chart system is still a workstation/debug UI rather than a polished production charting package.
- Dual heatmap overlay remains debug-gated and may auto-degrade off when UI apply latency rises.

## Not Implemented Yet

- Manual hybrid surface construction and stitched multi-engine surface management are still spec-only.
- ArcticDB integration is not part of the current implementation.
- Packaged desktop releases, installers, and release CD are not set up yet.

## Quickstart
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
python -m pytest -q -p no:cacheprovider python/tests/unit python/tests/integration
```

## Useful Commands
```powershell
python -m flow_app
python scripts\query_views.py
python scripts\benchmark_calibration.py 50
python scripts\benchmark_laplace.py --runs 400
```

## Current Runtime Notes

- `ui_live` short-expiry hot cadence defaults to 15 seconds.
- Full-surface refresh cadence defaults to 300 seconds.
- The live worker may back off to 30-second hot cadence and 600-second full-surface cadence if latency or repeated fetch failures rise.
- Live freeze time defaults to `17:00 America/New_York`.
- Final-price refresh window opens at `17:30 America/New_York`.
- OI refresh window opens at `20:30 America/New_York`.
- Stream lock guard prevents concurrent `headless_live` and `ui_live` sessions from attaching to the same symbol by default.
- Local pytest scratch data defaults to `%TEMP%\equity-option-tools-pytest\...`; the old repo-local `.tmp` tree is disposable and can be removed safely when stale.

## Docs

- Canonical current-state docs live under `docs/`.
- Detailed build records for shipped features live under `docs/implementation/`.
- Historical and forward-looking material lives under `docs/historical/`.
- The current forward-looking inventory is tracked in `docs/historical/review/ahead_of_development.md`.
