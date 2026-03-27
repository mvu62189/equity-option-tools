# UI Spec

## Layout

The workstation is organized into three practical layers:

- `Scanner`: landing page for `0DTE`, `1DTE`, and `EOW`
- `Drilldown`: existing detailed review tabs
- `Validation`: existing data-quality, calibration, and diagnostics tabs

## Scanner Page

Visible sections:

- status line with selected focus bucket, data-quality status, and snapshot age
- runtime badge with current live refresh interval and fetch scope
- three expiry cards: `0DTE`, `1DTE`, `EOW`
- strike heatmap using gamma-OI exposure
- focused-expiry summary table
- scanner levels table
- snapshot-to-snapshot activity table

## Interaction Rules

- Clicking a focus card selects that bucket and syncs the detailed expiry selectors used by the drilldown tabs.
- Clicking a focus card does not change the live polling selection.
- The page remains readable when one or more focus buckets are unavailable.

## Chart Semantics

- `option_type=all` keeps call and put lines separate.
- Single-expiry contexts hide redundant expiry heatmaps.
- Price-fit views compare market American `bid`, `ask`, and `mid` with the American model price.
- Activity rows remain labeled as estimates inferred from snapshot changes.

## Styling Direction

- Keep the app's existing white/light debug-workstation visual language.
- Use color-coded data-quality cards:
  - green for `trusted`
  - amber for `review`
  - red for `caution`
  - gray for unavailable
