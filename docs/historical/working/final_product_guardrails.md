# Final Product Guardrails

## Product Intent

The MVP is a desktop debugging application for the live and snapshot Greeks calibration pipeline. It is not just a viewer. It must let the user inspect:

- what data was ingested
- which routing/engine path produced each result
- whether calibrations converged
- whether chart values came from a coherent batch and scope
- how Greeks evolve across strike, expiry, and stored observation times

## User-Facing Guardrails

1. UI must never silently mix incompatible scopes.
2. Routed Greeks must show provenance for displayed values.
3. Offline UI startup should show the latest stored final snapshot if one exists.
4. After-hours behavior should be explicit and visible to the user.
5. Hybrid stitched surfaces are spec-only in this phase and must not appear silently in production views.

## Live And Final Snapshot Rules

1. During live hours, polling and computation can continue normally.
2. At `17:00 America/New_York`, the session should transition into final-snapshot mode.
3. If the running app owns the live session, it may capture one `eod_final` batch for that day.
4. After that point the UI should show final-state freshness and offer refresh actions, not pretend the stream is still live.
5. At `20:30 America/New_York`, one `eod_oi_refresh` check may run or be offered to the user.
6. `eod_oi_refresh` is OI-only state. It must not overwrite final prices, Greeks, or calibration outputs from `eod_final`.

## Routed Greeks Rules

Each displayed routed Greeks row should make clear:

- market quote fields
- model price
- displayed price source
- rate and dividend input used
- time to expiry used
- theta convention
- vega/rho method
- engine/backend used
- fallback reason if any
- source batch identity

## Chart Integrity Rules

1. Heatmap y-axis must represent real tenor coordinates, not synthetic row indexes.
2. Default chart mode must not mix calls and puts.
3. Default chart mode must not mix engines.
4. Single-expiry payloads should degrade to line-first mode and clearly state that the surface is degenerate.
5. Future hybrid surfaces require explicit construction and validation; they are not default views.

## Temporal Greeks View

The app should support a fixed-expiry time-evolution panel where the user:

- selects one expiry
- selects one Greek
- moves a time slider across stored observation timestamps
- sees `Greek(strike)` update smoothly for that expiry

This view is for understanding how the same expiry evolves through the day, not for mixing expiries together.
