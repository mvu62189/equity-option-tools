# Architecture

## Runtime Layers
- Ingestion: provider adapters -> schema mapper -> canonical Polars frame.
- Storage: atomic in-memory state snapshots + buffered parquet flush + DuckDB analytics.
- Quant: Black-Scholes baseline, SSVI calibration, expiry routing dispatch, parity diagnostics, and O4
  American-IV adapter, C++ log-space FDM Greeks path, and C++ log-slice SSVI calibrator.
- UI: PySide6 receives coalesced state-version signals and applies latest snapshots only.
- Compute bindings: C++20/pybind11 with explicit GIL release and strict-mode startup capability checks.

## Concurrency Topology
- Main thread: Qt event loop and rendering.
- Worker thread: asyncio ingestion/quant pipeline.
- Overlay prep: single-worker executor with latest-wins queue.
- Cache consistency: one atomic publish per batch via `LiveStateStore`.

## State Model
- `BatchPayload` is built in worker thread and published atomically.
- `SymbolSnapshot` is immutable for readers; UI never stitches partial frames.
- Snapshot fields include raw, greeks, ssvi, dispatch, parity, parity_detail, calibration diagnostics,
  overlay payloads, latency metrics, and drop counters.

## Memory Control
- Primary guard: in-memory estimated bytes (`DataFrame.estimated_size` + NumPy `nbytes`) per dataset.
- Secondary guard: process RSS for operational safety.
- Trim policy uses hysteresis and cooldown to avoid repeated trim loops.

## UI Rendering Contract
- Push + coalesce updates with latest-wins per symbol/version.
- Heatmap uses persistent `pyqtgraph.ImageItem`.
- Default overlay mode is single-canvas selector (`log`, `strike`, `residual`).
- Side-by-side dual heatmap is debug-gated and auto-degrades off when apply p95 breaches threshold.
- Update path:
  - `setImage(..., autoLevels=False, autoDownsample=True)`
  - `setRect(...)`
  - `setLevels(...)`
- Overlay arrays are generated off UI thread as contiguous `float32` NumPy buffers.

## Contracts
The canonical quote schema is defined in `python/flow_core/contracts/schema.py`.
All upstream vendor data must be mapped to this schema before any quant processing.
