# Operations

## Live Mode
- Default cadence: 5 seconds.
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
  - `data/derived/ssvi/year=.../symbol=.../`
  - `data/derived/parity/year=.../symbol=.../`
  - `data/derived/parity_detail/year=.../symbol=.../`
  - `data/derived/diagnostics/year=.../symbol=.../`

## Snapshot Mode
- EOD schedule defaults to `20:00 America/New_York`.
- Snapshot writes to Hive partitions:
  `year=YYYY/month=MM/day=DD/ticker=SYMBOL/`.

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
- `run_ui --with-live` starts worker thread and attaches stop event.
- `run_daemon.py` is headless ingestion/scheduler mode and should not be co-run against same symbol stream with UI mode unless lock checks are disabled.
- Stream lock guard path: `data/runlocks/<SYMBOL>.lock`.
- On shutdown:
  - stop ingestion loop.
  - drain coalesced updates.
  - flush buffered parquet writers.
  - emit shutdown summary logs.

## Telemetry
- Per-stage latency: `ingestion_ms`, `mapping_ms`, `routing_ms`, `calibration_ms`, `pricing_ms`,
  `ui_bridge_ms`, `persist_ms`, `total_ms` (legacy aliases retained for compatibility).
- Buffer metrics: `flush_rows`, `flush_files`, `flush_ms`, `buffer_depth`.
- Memory metrics: state bytes, RSS bytes, trim reason (`soft` or `hard`).
