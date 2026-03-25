# Ahead Of Development

Status: Historical review note for docs that intentionally describe target-state or spec-only work that is not fully implemented in the current app.

## Forward-Looking Docs

- `working/hybrid_surface_constructor_spec.md`
  - Describes a manual stitched multi-engine surface constructor with region assignment, provenance, and continuity validation.
  - Current reality: not implemented in the runtime or UI. The current UI only exposes single-engine and routed views.
- `working/hybrid_surface_inputs_needed.md`
  - Captures unresolved product and persistence decisions required before hybrid surface construction can be built safely.
  - Current reality: still an open design questionnaire and still a blocker for implementation.
- `source_copies/PLAN_UI_ASYNC_FINAL.md`
  - Historical implementation plan for async, state, and UI hardening.
  - Current reality: many runtime and UI items landed, but this remains a historical plan copy, not a promise that every item is shipped exactly as written.
- `source_copies/PLAN_UI_core_ui_scheduler_FINAL.md`
  - Historical implementation plan for runtime modes, scheduler behavior, and telemetry policy.
  - Current reality: much of the unified runtime work landed, but this file still contains roadmap framing and should be read as an implementation archive.

## Code Areas Still Marked As Future Work

- `python/flow_core/quant/deamericanization.py`
  - One early-exercise-premium estimation path still uses MVP heuristic coefficients and explicitly calls out a phase-2 replacement.
- Desktop packaging and release CD
  - Not implemented in the current repo tooling.
- ArcticDB integration
  - Mentioned historically as phase-2 work and not part of the current implementation.

## Practical Reading Rule

- Use top-level `docs/*.md` for what the app does now.
- Use this file plus `docs/historical/working/*.md` to understand what is still ahead of the current implementation.
