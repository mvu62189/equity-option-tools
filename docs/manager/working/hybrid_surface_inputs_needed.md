# Hybrid Surface Constructor: Inputs Needed

These decisions are still required before the manual hybrid surface constructor can be implemented without guessing.

## 1. Region Assignment Granularity
- Should the user assign models by expiry bucket only, or by full `(expiry, strike)` regions?
- If strike-aware regions are allowed, should the user define them by absolute strike, moneyness, or delta buckets?

## 2. Boundary Semantics
- At a boundary between two models, should the system:
  - hard switch,
  - blend across a transition band,
  - or force continuity constraints at the seam?
- If blending is allowed, what is the preferred blend width unit: days, strikes, log-moneyness, or percentage moneyness?

## 3. Surface Completion Rules
- Must the user cover the full available surface before compute is allowed?
- If not fully covered, should uncovered regions:
  - fail validation,
  - inherit a default routed engine,
  - or remain blank and excluded from charts?

## 4. Stored Provenance
- What exact provenance must be persisted per region?
- Minimum candidate fields:
  - assigned engine,
  - assigned backend,
  - region bounds,
  - user note/rationale,
  - creation timestamp,
  - last edited timestamp,
  - continuity policy,
  - validation status.

## 5. Greeks and Surface Outputs
- Should the hybrid constructor produce only price and first-order Greeks initially, or also second/higher-order Greeks when the component models support them?
- If a chosen model lacks a requested Greek, should the constructor:
  - reject the build,
  - finite-difference the hybrid surface,
  - or mark that Greek unavailable in that region?

## 6. Validation Policy
- Which checks are mandatory before a hybrid surface is accepted?
- Candidates:
  - no overlaps,
  - no uncovered gaps,
  - seam continuity,
  - butterfly/calendar arbitrage scan,
  - monotonicity checks on selected Greeks.

## 7. UI Workflow
- Should hybrid mode be a separate builder window, a dock/panel inside the main UI, or a dedicated tab?
- Do you want saved presets/templates for region maps?
- Should edits apply live to the charts, or only after pressing `Build Surface`?

## 8. Persistence Model
- Should hybrid surface definitions be stored per trading date, per symbol, or as reusable named templates?
- Should the stored object reference a specific `batch_id`, or float to “latest available” data by default?

## 9. Comparison View
- When the hybrid surface is displayed, what should the default comparison be?
- Candidates:
  - versus routed production surface,
  - versus single selected engine,
  - versus market-mid error,
  - versus previous saved hybrid surface.
