# UI Spec

## Layout

The workstation is now organized into three practical layers:

- `Scanner`: landing page for `0DTE`, `1DTE`, and `EOW`
- `Drilldown`: existing detailed tabs
- `Validation`: existing trust and diagnostics tabs

## Scanner Page

Visible sections:

- status line with selected focus bucket, trust, and snapshot age
- runtime badge with current live cadence and fetch scope
- three expiry cards: `0DTE`, `1DTE`, `EOW`
- strike heatmap using gamma-OI exposure
- focused-expiry summary table
- scanner levels table
- flow proxy table

## Interaction Rules

- Clicking a focus card selects that bucket and syncs the detailed expiry selectors used by the drilldown tabs.
- Clicking a focus card does not change the live polling selection.
- The page remains readable when one or more focus buckets are unavailable.

## Chart Semantics

- `option_type=all` keeps call and put lines separate.
- Single-expiry contexts hide redundant expiry heatmaps.
- Price-fit views compare quoted American `bid/ask/mid` to `american_model_price`.
- Flow rows remain labeled as proxy analytics.

## Styling Direction

- Keep the app’s existing white/light debug-workstation visual language.
- Use color-coded trust cards:
  - green for `trusted`
  - amber for `review`
  - red for `caution`
  - gray for unavailable
