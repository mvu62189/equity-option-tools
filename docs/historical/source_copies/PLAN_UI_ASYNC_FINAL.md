# Source Copy

- Original: $rel
- Copied: 2026-03-16
- Note: Original remains unchanged; this copy exists for manager review.

---

## Revised Phase 2/3 Plan: UI+Async Runtime Hardening with Deterministic State and Memory

### Summary
This revision incorporates your review and removes heuristic weak points.  
The implementation will lock in:
1. Single-process runtime with `Qt main thread + one asyncio worker thread`.
2. Push/coalesce UI updates with strict `latest-wins`.
3. Atomic versioned state snapshots (no mixed-generation views).
4. Memory control by dataset byte budgets first, row caps second.
5. Dual memory telemetry (`state_estimated_bytes` + RSS) with hysteresis to prevent trim death spirals.
6. Persistent zero-flicker heatmap updates using precomputed NumPy payloads.

---

## 1) Goals, SLOs, and Scope

### Goals
1. Keep UI responsive under live ingestion and quant updates.
2. Eliminate race conditions between producer thread and UI reads.
3. Bound memory growth for 1â€“3 symbols over long sessions.
4. Keep data persistence reliable while avoiding tiny-file explosion.

### SLOs
1. UI apply+render latency: `p95 < 50ms`.
2. Process memory guardrail for 1â€“3 symbols: `< 1.5 GB` steady-state RSS.
3. No mixed-version UI state during live updates.
4. No unbounded queue growth anywhere in UI update path.

### In-Scope
1. State store redesign.
2. UI bridge/coalescing and view payload prep.
3. Memory manager and backpressure policies.
4. Scheduler/lifecycle hardening.
5. Ops telemetry and runbook alignment.

### Out-of-Scope
1. New quant model math.
2. ArcticDB integration.
3. Full multi-process IPC migration.

---

## 2) Architecture Decisions (Final)

1. Runtime topology: `Qt main thread` + `one dedicated asyncio worker thread`.
2. Overlay/viewmodel preparation: no unbounded `QThreadPool`; use one bounded prep executor with `max_workers=1` behind a latest-wins queue.
3. Cache consistency model: single atomic publish per batch; readers consume immutable snapshot handles.
4. UI update policy: push-driven with coalescing; timer used only as throttled apply cadence, not data polling source.
5. Memory policy: dataset byte budgets are authoritative; row caps are fallback guardrails.
6. Memory monitor policy: no blind periodic trimming from raw RSS; use dual metrics + hysteresis + cooldown.

---

## 3) Public Interfaces and Config Changes

### 3.1 New state module
Create [state_store.py](/d:/quant-pipeline-mvp/python/flow_core/orchestration/state_store.py) with:

1. `@dataclass(frozen=True) BatchPayload`
- `symbol: str`
- `batch_id: str`
- `version_hint: int | None`
- `updated_at_utc: datetime`
- `raw: pl.DataFrame`
- `greeks: pl.DataFrame`
- `ssvi: pl.DataFrame`
- `dispatch: pl.DataFrame`
- `parity: pl.DataFrame`
- `parity_detail: pl.DataFrame`
- `calibration_diag: pl.DataFrame`
- `latency_ms: dict[str, float]`
- `status: dict[str, str | bool | float]`

2. `@dataclass(frozen=True) SymbolSnapshot`
- `symbol: str`
- `batch_id: str`
- `version: int`
- `updated_at_utc: datetime`
- same frame fields as `BatchPayload`
- `overlay_payloads: dict[str, object]` (NumPy arrays + metadata for plotting)
- `memory_bytes: dict[str, int]`
- `drop_counters: dict[str, int]`

3. `class LiveStateStore`
- `publish(payload: BatchPayload) -> int`
- `get_snapshot(symbol: str) -> SymbolSnapshot | None`
- `get_latest_version(symbol: str) -> int`
- `append_history(symbol: str, dataset: str, frame: pl.DataFrame) -> None`
- `trim_to_budget(symbol: str) -> dict[str, int]`
- `estimate_symbol_bytes(symbol: str) -> dict[str, int]`
- `estimate_total_bytes() -> int`
- `stats_frame() -> pl.DataFrame`

Locking model: `threading.Lock` for cross-thread correctness; publish performs copy-on-write pointer swap.

### 3.2 UI bridge module
Create [state_bridge.py](/d:/quant-pipeline-mvp/python/flow_ui/state_bridge.py):

1. `class UIStateBridge(QObject)`
- `snapshot_ready = Signal(str, int)`
- `stats_ready = Signal(dict)`
- `publish(symbol: str, version: int) -> None`
- `coalesce(symbol: str, version: int) -> None`
- `consume_latest(symbol: str) -> int | None`

Rule: at most one pending version per symbol; newer replaces older.

### 3.3 View payload module
Create [viewmodels.py](/d:/quant-pipeline-mvp/python/flow_ui/viewmodels.py):

1. `build_overlay_payload(snapshot: SymbolSnapshot, greek: str, option_type: str, expiry_filter: str) -> dict`
2. Output schema:
- `line_series: dict[str, np.ndarray]` (engine -> Nx2 float32 arrays)
- `heat_image: np.ndarray[float32]` shape `(n_exp, n_strike)`
- `rect: tuple[float, float, float, float]`
- `levels: tuple[float, float]`
- `meta: dict[str, object]`

All arrays generated off UI thread; `np.ascontiguousarray(dtype=np.float32)` enforced.

### 3.4 Config extensions
Extend [models.py](/d:/quant-pipeline-mvp/python/flow_core/config/models.py) and [default.yaml](/d:/quant-pipeline-mvp/configs/pipeline/default.yaml):

1. `state_max_symbols: int = 3`
2. `state_budget_raw_mb: int = 128`
3. `state_budget_greeks_mb: int = 256`
4. `state_budget_ssvi_mb: int = 32`
5. `state_budget_dispatch_mb: int = 16`
6. `state_budget_parity_mb: int = 64`
7. `state_budget_parity_detail_mb: int = 128`
8. `state_budget_diagnostics_mb: int = 64`
9. `state_budget_overlay_mb: int = 128`
10. `state_max_rows_raw: int = 10000`
11. `state_max_rows_greeks: int = 10000`
12. `state_max_rows_ssvi: int = 2000`
13. `state_max_rows_diagnostics: int = 2000`
14. `ui_apply_interval_ms: int = 50`
15. `ui_max_pending_per_symbol: int = 1`
16. `memory_soft_limit_mb: int = 1300`
17. `memory_hard_limit_mb: int = 1536`
18. `memory_check_interval_sec: int = 5`
19. `memory_trim_cooldown_sec: int = 30`
20. `diag_flush_interval_sec: int = 120`
21. `parquet_flush_interval_sec: int = 5`
22. `parquet_flush_max_rows: int = 5000`

---

## 4) Detailed Runtime Flow

### 4.1 Producer flow (async worker thread)
1. Fetch + map + routing + greeks + diagnostics.
2. Compute per-stage latency map.
3. Build `BatchPayload`.
4. Publish once to `LiveStateStore`; receive `version`.
5. Enqueue UI signal via bridge with `coalesce(symbol, version)`.
6. Append to persistence buffers for raw/derived.
7. Periodic flusher writes buffered parquet chunks.

### 4.2 Consumer flow (Qt main thread)
1. `snapshot_ready(symbol, version)` marks symbol dirty.
2. `QTimer` at `ui_apply_interval_ms` applies latest dirty versions only.
3. UI apply step reads `SymbolSnapshot` by symbol/version.
4. Widgets update only with precomputed payloads:
- table model replacement
- line `setData`
- heatmap `setImage` and `setRect`
5. Stale versions are dropped silently and counted.

### 4.3 Overlay prep flow
1. Snapshot publish triggers prep task enqueue.
2. Prep queue is bounded latest-wins per symbol.
3. Worker computes line/heatmap arrays and level bounds.
4. Snapshot updated with `overlay_payloads`.
5. UI reads these payloads directly without Polars transforms.

---

## 5) Heatmap Rendering Contract (Zero-Flicker)

1. Create `ImageItem` once and reuse.
2. Never clear/recreate plot per tick.
3. Update path:
- `image_item.setImage(image, autoLevels=False, autoDownsample=True)`
- `image_item.setRect(QRectF(*rect))`
- `image_item.setLevels((z_lo, z_hi))`
4. Level policy:
- default levels from rolling robust quantiles (q05/q95) with EWMA smoothing.
- clamp to finite min/max fallback when sparse.
5. Sparse grid fill:
- first choice: axis-wise forward/backward fill on canonical strike/expiry grid.
- optional SciPy interpolation only when density threshold is met and prep latency budget allows.

---

## 6) Memory Management Algorithm (Revised)

### 6.1 Metrics
1. `state_estimated_bytes`: sum of `DataFrame.estimated_size()` and NumPy payload `.nbytes`.
2. `process_rss_bytes`: from `psutil`.

### 6.2 Control logic
1. Sample every `memory_check_interval_sec`.
2. If either metric exceeds soft limit for 3 consecutive samples:
- run one trim pass.
- start cooldown timer (`memory_trim_cooldown_sec`).
3. During cooldown, do not trim again unless hard limit exceeded.
4. If hard limit exceeded:
- immediate trim pass and diagnostics warning event.
5. `gc.collect()` policy:
- call only after trim pass and before post-trim measurement.
- max once per cooldown window.

### 6.3 Trim order (deterministic)
1. `parity_detail`
2. `calibration_diag`
3. `ssvi history`
4. `greeks history`
5. `raw history`
6. overlay payload cache (keep only latest)

Each step trims to per-dataset byte budget first, then row cap.

---

## 7) Persistence and File-Count Control

1. Replace per-batch direct parquet writes with buffered writer layer.
2. Flush triggers:
- `interval >= parquet_flush_interval_sec`
- or `rows >= parquet_flush_max_rows`
- or shutdown
3. Partition layout unchanged.
4. Writer emits telemetry:
- `flush_rows`, `flush_files`, `flush_ms`, `buffer_depth`.

---

## 8) Scheduler and Lifecycle Hardening

1. Wire [scheduler.py](/d:/quant-pipeline-mvp/python/flow_core/orchestration/scheduler.py) into `run_live` and `run_ui --with-live`.
2. Add cancellation primitives:
- `stop_event` for live loop
- `await` task cancellation with timeout
3. Shutdown sequence:
- stop ingest
- drain state bridge
- flush parquet buffers
- flush diagnostics
- final summary log

---

## 9) Implementation Phases

### Phase 2A: State and atomic publish
1. Add `state_store.py` and compatibility adapter in [cache.py](/d:/quant-pipeline-mvp/python/flow_core/orchestration/cache.py).
2. Refactor [pipeline.py](/d:/quant-pipeline-mvp/python/flow_core/orchestration/pipeline.py) to publish single `BatchPayload`.
3. Add batch/version consistency metadata in all derived outputs.

### Phase 2B: UI bridge and payload prep
1. Add `state_bridge.py`, `viewmodels.py`, and `update_coordinator.py`.
2. Migrate [main.py](/d:/quant-pipeline-mvp/python/flow_ui/main.py) to signal-driven apply path.
3. Integrate persistent heatmap contract.

### Phase 2C: Memory/backpressure/persistence
1. Add memory manager loop and trim policy.
2. Add buffered parquet writer.
3. Add telemetry counters and logs.

### Phase 3: Ops and runbooks
1. Wire EOD scheduler in live runtime.
2. Add startup/shutdown health logging.
3. Update docs:
- [operations.md](/d:/quant-pipeline-mvp/docs/operations.md)
- [architecture.md](/d:/quant-pipeline-mvp/docs/architecture.md)
- [README.md](/d:/quant-pipeline-mvp/README.md)

---

## 10) Tests and Acceptance Criteria

### Unit tests
1. Atomic publish produces monotonic versions and coherent snapshots.
2. Coalescer keeps latest version only.
3. Memory manager hysteresis prevents repeated trim loops.
4. Byte-budget trim works with wide and narrow frames.
5. Heatmap payload builder returns contiguous float32 arrays and valid rect/levels.

### Integration tests
1. UI + live worker run for 30 minutes synthetic feed:
- no cross-thread exceptions
- no mixed-version rendering
- bounded memory behavior
2. Burst producer test:
- pending queue remains bounded
- stale drop counters increase as expected
3. Buffered parquet flush:
- reduced file count vs per-batch baseline
- no data loss on shutdown flush
4. EOD scheduler invocation:
- callback fires and diagnostics flush completes.

### Performance tests
1. UI apply latency: `p95 < 50ms`.
2. Overlay prep latency budget: `p95 < 20ms` for 1 symbol.
3. Memory: RSS stays `< 1.5 GB` in 1â€“3 symbol profile after warmup.
4. Backpressure: queue depth stable at configured max.

Acceptance is met only when all above pass in CI and local stress run.

---

## 11) Assumptions and Defaults

1. Deployment remains Windows-first desktop runtime.
2. `PySide6 + pyqtgraph` remain mandatory UI dependencies.
3. Live cadence remains 5s by default.
4. Symbol target profile remains 1â€“3 symbols.
5. C++ quant kernels remain consumed through pybind API; UI never calls C++ directly.
6. Existing data contracts and partition paths remain backward-compatible.

