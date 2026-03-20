# Source Copy

- Original: $rel
- Copied: 2026-03-16
- Note: Original remains unchanged; this copy exists for manager review.

---

# Quant Pipeline MVP

Greenfield Python-first quantitative options pipeline with C++20/pybind11 compute skeleton.

## Highlights
- Provider-agnostic ingestion with YAML schema mapping.
- Live polling and snapshot ingestion to Polars + Hive-partitioned Parquet.
- DuckDB query layer over raw partitions.
- Quant slice: Black-Scholes baseline + SSVI calibration with butterfly constraint checks.
- PySide6 desktop UI with push/coalesced state updates and persistent zero-flicker heatmap rendering.
- Atomic versioned in-memory snapshots (`LiveStateStore`) to avoid mixed-generation UI reads.
- Memory guardrails with dual metrics (state-estimated bytes + RSS), hysteresis, and cooldown trimming.
- C++ pybind module skeleton with explicit GIL release.

## Quickstart
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest
```

## Run
```powershell
python scripts\run_live.py --ticker SPY
python scripts\run_snapshot.py --ticker SPY
python scripts\run_ui.py --with-live --ticker SPY
python scripts\run_daemon.py --ticker SPY
python scripts\query_views.py
python scripts\benchmark_laplace.py --runs 400
```

## Notes
- EOD snapshot schedule defaults to 20:00 America/New_York.
- `run_ui --with-live` uses a dedicated asyncio worker thread and latest-wins UI backpressure.
- `runtime_mode=live_strict` requires compiled `quantcore` for configured `ssvi_backend`/`fdm_backend`.
- Stream lock guard prevents `run_daemon` and `run_ui --with-live` from attaching to the same symbol by default.
- Overlay defaults to single-canvas mode selector (`log`, `strike`, `residual`); dual heatmap is debug-gated.
- ArcticDB is intentionally out of MVP scope and planned for phase 2.

