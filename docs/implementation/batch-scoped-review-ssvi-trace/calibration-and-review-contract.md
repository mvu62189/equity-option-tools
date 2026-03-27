# Calibration And Review Contract

Status: implementation detail for the currently shipped review-time pricing and Greeks flow.

## End-To-End Path

1. Save the raw American option-chain snapshot for the selected run.
2. Mark unusable quotes and calibration-eligibility results in the saved quote-cleaning dataset.
3. Convert the surviving American bid and ask prices into European-equivalent bid and ask prices.
4. Convert those European-equivalent prices into an implied-volatility bid/ask range at each strike:
   - implied volatility from the de-Americanized bid price (`iv_bid`)
   - implied volatility from the de-Americanized ask price (`iv_ask`)
   - reference implied volatility from the cleaned European input (`iv_ref`)
5. Build one fitted SSVI implied-volatility curve per expiry:
   - OTM puts supply the left wing
   - OTM calls supply the right wing
   - only the ATM strike, or the two strikes bracketing forward, blends call-side and put-side implied-volatility bid/ask ranges into one ATM input
6. Evaluate the fitted SSVI implied-volatility curve back at the contract strikes.
7. Reprice European option prices from the fitted SSVI volatility and check whether those European model prices stay inside the European bid/ask range.
8. Convert the fitted surface back into American-style option prices and check whether those American model prices stay inside the cleaned American bid/ask range.
9. Compute surface-based model Greeks from American prices using bump-and-reprice under sticky delta.

## Why Volatility Space Is Primary

- The main fit is judged first in implied-volatility space because the uncertainty introduced by de-Americanization is easier to isolate there than in repeated American/European price conversions.
- Midpoint is not treated as theoretical truth. It is used only as a light guide when the fitted volatility already sits inside the implied-volatility bid/ask range.
- Price-space checks still matter, but they are downstream validation:
  - European model price implied by fitted SSVI volatility versus the European bid/ask range
  - American model price implied by the fitted surface versus the cleaned American bid/ask range

## One Selected Saved Snapshot Drives Review

- In `ui_review`, one selected saved snapshot is the source of truth for all non-history pages.
- If two review tabs show different expiry universes for the same selected saved snapshot, that is a bug.
- `Temporal Greeks` is the intentional exception because it can switch into an explicit history mode.

## Saved Provenance

- `quote_quality_points` records quote cleaning, European conversion, implied-volatility bid/ask ranges, and calibration eligibility.
- `ssvi` records one fitted SSVI volatility summary per expiry.
- `surface_points` records per-contract fitted volatility, European and American repricing checks, and smoothness diagnostics.
- `model_greeks` is the default review-time Greeks dataset.
- `greeks` remains the saved legacy comparison dataset.

## Python And C++ Parity

- Python and compiled `quantcore` now use the same SSVI fitting process.
- The compiled path exposes:
  - `calibrate_ssvi_slice` as the production slice-calibration entrypoint
  - `ssvi_residuals_slice` for parity and raw-residual inspection
- The older `calibrate_ssvi_log_slice` name remains only as a compatibility shim around the newer slice-calibration process.
- The pipeline can now use either Python or C++ for the same expiry-level SSVI fit without changing:
  - the OTM/ATM input rules
  - the implied-volatility bid/ask range logic
  - the weak midpoint tie-break behavior
  - the butterfly no-arbitrage penalty handling
- The compiled Bjerksund-Stensland de-Americanization helper is now symmetric for calls and puts, so put-side implied-volatility inversion no longer has to fall back immediately to the slower Python/binomial proxy when the compiled backend is present.

## Derivative Safety

- Duplicate-strike or non-strictly-increasing strike grids are treated as explicit statuses such as `duplicate_strike_grid` or `insufficient_unique_strikes`.
- The pipeline should surface those statuses in diagnostics instead of letting NumPy derivative helpers emit divide-by-zero runtime warnings.
