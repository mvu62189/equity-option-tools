# Quant Pipeline MVP

Legacy desktop product notice: this repository is being preserved as the frozen desktop workstation line. New product development is intended to move into the separate webapp repository, while this repo remains available for historical reference, replay, and optional emergency fixes.

Windows-first desktop workstation for SPY options review, calibration, and short-expiry analytics. The app is built around saved full-chain snapshots, live short-expiry monitoring, step-by-step calibration checks, and surface-based Greeks for hedging and position analysis.

## Start The App
```powershell
python -m flow_app
```

- This is the main entrypoint.
- With no flags, it opens the launcher and saves the next-launch session to `%LOCALAPPDATA%\quant-pipeline-mvp\launch_config.json`.
- You can also start a mode directly:

```powershell
python -m flow_app --mode ui_review --ticker SPY --no-launcher
python -m flow_app --mode ui_live --ticker SPY --no-launcher
python -m flow_app --mode headless_live --ticker SPY --no-launcher
python -m flow_app --mode snapshot_once --ticker SPY --no-launcher
```

- `scripts\run_ui.py`, `scripts\run_live.py`, `scripts\run_daemon.py`, and `scripts\run_snapshot.py` still work, but they now forward into `flow_app` and log deprecation warnings.

## Working Today

- One launcher starts `ui_review`, `ui_live`, `headless_live`, and `snapshot_once`.
- The desktop UI includes a SPY short-expiry scanner, option-chain review, model-vs-market checks, calibration diagnostics, Processing Trace, runtime metrics, temporal Greeks, and arbitrage checks.
- `ui_review` is built around one selected saved snapshot. That one saved snapshot drives the Option Chain, Greeks, overlay, model-vs-market, validation, calendar/density, scanner, and Processing Trace pages.
- `ui_review` includes a saved-snapshot selector and `Pull Full Snapshot`, so you can capture a fresh full-surface snapshot and immediately switch the review session to it without restarting the app.
- `ui_review` shows the saved raw option chain in the `Option Chain` tab, with expiry and option-type filters.
- `snapshot_once` captures a full option surface for the selected ticker, runs the pipeline once, saves the results, and exits headlessly.
- `ui_live` focuses on `0DTE`, `1DTE`, and `EOW` expiries for faster short-expiry monitoring, while also refreshing the full surface more slowly for validation context.
- The current pricing workflow is:
  market American bid/ask quotes -> quote cleaning and filtering -> European-equivalent bid/ask prices -> implied volatility bid/ask range -> one fitted SSVI volatility curve per expiry -> European repricing check -> American repricing check -> surface-based Greeks
- The fitted SSVI volatility curve for each expiry is built mainly from OTM puts on the left wing and OTM calls on the right wing. At ATM, the call-side and put-side implied-volatility bid/ask ranges are blended only at the ATM strike, or at the two strikes bracketing forward when forward sits between strikes.
- ITM quotes are kept for diagnostics, but they do not drive the main SSVI fit when both OTM wings are available.
- The main calibration target is no longer vendor-supplied implied volatility. Vendor IV is kept only as a comparison reference.
- Review-time Greeks now default to surface-based model Greeks computed from American prices with bump-and-reprice under sticky delta. Older routed Greeks are still available beside them for comparison.
- Python and compiled `quantcore` now use the same SSVI fitting process. Both follow the same implied-volatility bid/ask range logic and ATM/OTM fit rules.
- The compiled `quantcore` module is used for the latency-sensitive pricing and calibration paths, including SSVI slice calibration, finite-difference Greeks, Bjerksund-Stensland pricing/Greeks, and Laplace pricing/Greeks.
- Saved history can be replayed even when older and newer parquet files have different columns. The review loader aligns old and new saved columns by column name and shows a visible warning if part of the saved history fails to read.
- Processing Trace shows the full pricing workflow for one expiry:
  raw American bid/ask -> cleaned American bid/ask -> European-equivalent prices -> implied-volatility bid/ask range -> fitted SSVI volatility -> European model price -> American model price
- The app includes persisted runtime latency and performance measurements, saved validation data, and a GitHub Actions CI workflow for linting, typing, tests, and `quantcore` build/import checks.

## Working But Still Provisional

- The runtime is provider-ready, but the only shipped data source today is `yfinance`.
- `runtime_mode=live_strict` is the production-safe path. Research and backtest modes still allow explicit Python fallback when the compiled backend is unavailable or intentionally disabled.
- The raw quote mapper still stores most provider rows so replay stays faithful to the source feed. The stricter filtering happens later in the quote-cleaning and calibration-eligibility step.
- One early-exercise-premium path used during American-to-European conversion is still MVP-grade and should be treated as under validation rather than final production math.
- The short-expiry scanner is intentionally SPY-first and snapshot-oriented. It is not driven by true options trade tape.
- Snapshot-to-snapshot activity views are only rough activity estimates inferred from changes between saved snapshots. They are not sweeps, prints, or true order-flow classification.
- The charting stack is now much more usable for diagnostics, but it is still a workstation/debug UI rather than a polished production charting package.

## Not Implemented Yet

- Manual hybrid surface construction and stitched multi-model surface management.
- ArcticDB integration.
- Packaged desktop releases, installers, and release CD.

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

- `ui_live` short-expiry refresh interval defaults to 15 seconds.
- Full-surface refresh interval defaults to 300 seconds.
- The live worker may slow down to 30-second short-expiry refresh and 600-second full-surface refresh when latency rises or the provider repeatedly fails.
- Live freeze time defaults to `17:00 America/New_York`.
- Final-price refresh window opens at `17:30 America/New_York`.
- OI refresh window opens at `20:30 America/New_York`.
- A stream lock prevents two live sessions from attaching to the same ticker by default.
- Local pytest scratch data defaults to `%TEMP%\equity-option-tools-pytest\...`; the old repo-local `.tmp` tree is disposable scratch data only.

## Docs

- Canonical current-state docs live under `docs/`.
- Detailed implementation records for shipped features live under `docs/implementation/`.
- Historical and forward-looking material lives under `docs/historical/`.
- The current forward-looking inventory is tracked in `docs/historical/review/ahead_of_development.md`.
