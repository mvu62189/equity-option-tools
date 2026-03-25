# Hybrid Surface Constructor Spec

This is a specification-only document in the current phase. It is not yet a production feature.

## Purpose

Allow the user to manually assign different model engines to different regions of the `(expiry, strike)` surface and inspect the stitched result with provenance and diagnostics.

## Required Inputs

- symbol
- option type
- Greek/output type
- surface region assignments
- one engine per assigned region

## Validation Rules

1. No overlapping regions.
2. No unassigned regions when hybrid mode is active.
3. Each region must record provenance for engine, backend, and input batch.
4. Continuity and arbitrage diagnostics must run on the stitched result before it is shown as a validated hybrid surface.

## Provenance Requirements

Every stitched cell should be attributable to:

- source engine
- source backend
- source batch id
- region definition

## Current Phase Rule

The default overlay UI must remain provenance-safe:

- single-engine view by default
- single-option-type view by default
- no silent stitched or averaged hybrid surface

Hybrid construction remains a future implementation after the base charts and routed Greeks are fully trustworthy.
