# Revised Next Dev Program (Phases 1.5 -> 2 -> 3) with Anti-Heuristic Guardrails

## Summary
1. Keep bundled delivery across Phase 1.5, 2, and 3, but enforce hard gates per milestone.
2. Replace heuristic/rigid points from review with explicit runtime policies:
3. No silent live fallback from C++ to Python for core SSVI/FDM.
4. Log-space dividend remap uses monotone high-order mapping in `S` space with safe fallback.
5. UI default is single-canvas `[Log | Strike | Residual]`, not always side-by-side.
6. Daemon mode and UI mode are separated by contract, no implicit dual-run on same stream.

## Scope and Milestone Order
1. `M1` (Phase 1.5): Production C++ SSVI log calibrator + C++ log-space FDM slice, strict live behavior.
2. `M2` (Phase 2): Overlay UI completion with model toggles and space-mode switch; side-by-side only debug-gated.
3. `M3` (Phase 3): Ops hardening with explicit run modes, scheduler daemon, split telemetry, alerts.

## Hard Architecture Decisions (Final)
1. **Runtime strictness policy**
2. Add `runtime_mode: live_strict | live_research | backtest`.
3. In `live_strict`, C++ quant module must load at startup or process exits with actionable error.
4. In `live_strict`, optimizer failure returns failed status immediately and skips compute for that slice; no Python retry path.
5. In `live_research/backtest`, Python fallback is allowed with explicit telemetry tag and warning logs.
6. **Log-space dividend jump policy**
7. For FDM log-space node-event jumps, remap through `S_post = max(S_pre - D, eps)` and interpolate value using monotone cubic interpolation over `S` grid.
8. If local stencil invalid, non-monotone, or near boundary, fallback to linear interpolation for that node only.
9. Record remap mode in diagnostics (`jump_interp_mode: cubic|linear_fallback`).
10. **UI rendering policy**
11. Default render mode is single heatmap with selector `[Log | Strike | Residual]`.
12. Side-by-side dual heatmap is allowed only when `ui_overlay_dual_mode_enabled=true` and symbol count is within debug cap.
13. If render budget breach detected (`p95 apply > threshold`), auto-degrade to single-canvas residual mode.
14. **Daemon/UI boundary policy**
15. `run_daemon.py` is headless ingestion/scheduler/persistence only.
16. `run_ui.py --with-live` is single-process interactive mode only.
17. Do not run both against same target stream by default; enforce lockfile guard with clear error.

## Milestone M1 (Phase 1.5) Implementation Plan
1. Add production C++ SSVI implementation in `cpp/src/ssvi.cpp` and wire pybind in [module.cpp](/d:/quant-pipeline-mvp/cpp/src/module.cpp).
2. New C++ API in [api.hpp](/d:/quant-pipeline-mvp/cpp/include/quantcore/api.hpp):
3. `calibrate_ssvi_log_slice(strikes, vols, forward, tau, init_guess, constraints) -> {params, iterations, sse, converged, durrleman, reason}`.
4. Add C++ log-space CN FDM kernel in `cpp/src/fdm_cn_log.cpp`:
5. `price_greeks_fdm_cn_log(contract, vol, grid_cfg, dividends) -> {price, delta, gamma, theta, success, diagnostics}`.
6. Add startup capability check in [pipeline.py](/d:/quant-pipeline-mvp/python/flow_core/orchestration/pipeline.py):
7. enforce strict mode behavior for C++ availability and convergence.
8. Keep Python paths as research-only fallback, with telemetry fields `backend_used`, `fallback_reason`, `runtime_mode`.
9. Persist diagnostics to `data/derived/diagnostics/...` with strict schema additions:
10. `backend_used`, `runtime_mode`, `failure_reason`, `jump_interp_mode`, `durrleman_pass`, `converged`, `iterations`, `sse_final`.

## Milestone M2 (Phase 2) Implementation Plan
1. Extend overlay controls in [main.py](/d:/quant-pipeline-mvp/python/flow_ui/main.py):
2. model-layer engine toggles (`FDM`, `Tree`, `BS2002`, `RIM`, `Laplace`).
3. space selector (`Log`, `Strike`, `Residual`).
4. debug-only side-by-side toggle.
5. Extend [viewmodels.py](/d:/quant-pipeline-mvp/python/flow_ui/viewmodels.py) payload contract:
6. include `space_mode`, `engine_mask`, `residual_stats`, `payload_bytes`.
7. Extend [update_coordinator.py](/d:/quant-pipeline-mvp/python/flow_ui/update_coordinator.py) request key:
8. `(symbol, version, greek, option_type, expiry, space_mode, engine_mask, dual_mode)`.
9. Add adaptive render degradation policy in UI:
10. if p95 apply breaches limit for rolling window, disable dual mode and log event.

## Milestone M3 (Phase 3) Implementation Plan
1. Add `scripts/run_daemon.py` as headless service entrypoint:
2. live ingestion loop, scheduler, buffered parquet flush, diagnostics flush, memory monitor.
3. keep [scripts/run_ui.py](/d:/quant-pipeline-mvp/scripts/run_ui.py) separate interactive path.
4. Add lockfile/run-mode guard:
5. prevent daemon and UI-live from attaching same stream target unless explicit override flag.
6. Split telemetry in [pipeline.py](/d:/quant-pipeline-mvp/python/flow_core/orchestration/pipeline.py):
7. `ingestion_ms`, `mapping_ms`, `routing_ms`, `calibration_ms`, `pricing_ms`, `ui_bridge_ms`, `persist_ms`, `total_ms`.
8. Add alert thresholds in config and structured warning events:
9. repeated non-convergence per symbol/expiry,
10. memory soft/hard breaches with cooldown state,
11. UI apply/render p95 breaches.

## Config and Interface Additions
1. In [models.py](/d:/quant-pipeline-mvp/python/flow_core/config/models.py) and `configs/pipeline/default.yaml` add:
2. `runtime_mode`, `ssvi_backend`, `fdm_backend`, `ui_overlay_default_mode`, `ui_overlay_dual_mode_enabled`.
3. `ui_apply_p95_limit_ms`, `ui_auto_degrade_enabled`, `stream_lock_enforced`.
4. `nonconvergence_alert_threshold`, `telemetry_emit_interval_sec`.
5. Keep defaults:
6. `runtime_mode=live_strict`, `ssvi_backend=cpp`, `fdm_backend=cpp`, `ui_overlay_default_mode=residual`, `ui_overlay_dual_mode_enabled=false`.

## Test Cases and Scenarios
1. Unit: strict mode exits when C++ module missing; research mode continues with fallback telemetry.
2. Unit: SSVI non-converged input returns fast failure status, no Python retry in strict live mode.
3. Unit: FDM dividend jump remap uses monotone cubic on interior nodes and linear fallback at boundaries.
4. Unit: UI payload generation for `Log`, `Strike`, `Residual` modes with engine masks.
5. Integration: live strict loop remains non-blocking under repeated failed slices.
6. Integration: UI adaptive degrade flips dual mode off after rolling p95 breach.
7. Integration: daemon and UI-live lock conflict produces deterministic startup error.
8. Perf: per-expiry calibration p95 `<250ms` in strict production path.
9. Perf: UI apply/render p95 `<50ms` in default single-canvas mode.
10. Perf: 30-minute 1-3 symbol run with bounded memory and stable queue depths.

## Acceptance Criteria
1. No stub SSVI path in production live flow.
2. No silent live fallback for core SSVI/FDM in strict mode.
3. Log-space dividend jump handling is stable and diagnostics expose remap mode.
4. Overlay UI ships with mandatory model toggles and single-canvas mode switch.
5. Daemon and UI modes are operationally separated and documented.
6. All unit/integration/perf gates pass locally and in CI.

## Assumptions and Defaults
1. Windows-first desktop deployment remains target.
2. UI continues consuming atomic Python snapshots only; no direct UI thread C++ calls.
3. Side-by-side dual heatmap is a debug capability, not default production behavior.
4. Fallback behavior is policy-driven by `runtime_mode`, not hardcoded globally.
