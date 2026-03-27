# Review By Saved Snapshot, Unified OTM SSVI Fit, and Processing Trace

Status: implementation record for the shipped review workstation and the current review-time calibration process.

## What Landed

- `ui_review` now revolves around one selected saved snapshot.
- The selected saved snapshot drives Option Chain, Greeks, overlay, model-vs-market, validation, calendar/density, scanner, and Processing Trace.
- The review header exposes a saved-snapshot selector sourced from the saved snapshot index (`snapshot_catalog`).
- `Pull Full Snapshot` runs a fresh full-surface snapshot, saves it, and switches the review screen to that new saved snapshot without restart.
- Review-time Greeks now default to the surface-based model Greeks dataset, while older routed Greeks remain available for side-by-side comparison.
- The active SSVI calibration no longer targets vendor-supplied implied volatility. It now works from implied-volatility bid/ask ranges derived from cleaned European-equivalent prices.
- `Processing Trace` now shows the full pricing workflow for one expiry in the review UI.

## Review Model

- Review startup now prefers the latest available saved snapshot, not the latest final-only snapshot.
- Saved parquet history can still be read across older and newer files even when saved columns changed over time.
- Review pages no longer mix one current cache snapshot with unrelated history by accident.
- `Temporal Greeks` is the one page that keeps an explicit choice between:
  - one selected saved snapshot
  - saved time history

## Calibration Process

- The starting point is raw American bid/ask.
- The quote-cleaning step excludes one-sided markets, crossed markets, conflicting duplicates, and strip-shape failures from calibration eligibility.
- The surviving quotes are converted into European-equivalent bid and ask prices.
- Those European-equivalent prices are converted into an implied-volatility bid/ask range.
- Each expiry then gets one fitted SSVI implied-volatility curve.
- When both calls and puts are available:
  - OTM puts drive the left wing
  - OTM calls drive the right wing
  - only the ATM strike, or the two strikes bracketing forward, blends call-side and put-side implied-volatility ranges into one ATM input
- ITM quotes remain visible in diagnostics but do not drive the main fit when both OTM wings exist.
- When one side is completely missing, the current implementation can temporarily fit the available side so sparse or incomplete saved snapshots still produce diagnostics.
- Python and C++ now share the same SSVI fitting process:
  - compiled `quantcore` exposes `calibrate_ssvi_slice`
  - Python uses the same residual logic and remains the explicit non-strict fallback
  - `calibrate_ssvi_log_slice` remains only as a backward-compatibility shim around the newer slice-calibration process

## Processing Trace Semantics

The Processing Trace page is tied to one selected saved snapshot and one expiry. It shows:

- `Cleaning`: raw American bid/ask, surviving cleaned American bid/ask, and dropped-point markers
- `De-Americanization`: cleaned American bid/ask against European-equivalent bid/ask/reference prices
- `Volatility Range Check`: implied volatility from de-Americanized bid and ask, reference implied volatility, and fitted SSVI volatility
- `Full Vol Diagnostic`: full call/put volatility context plus excluded-fit markers
- `European Reprice`: European bid/ask/reference prices against the European model price implied by fitted SSVI volatility
- `American Reprice`: cleaned American bid/ask and market midpoint against the American model price implied by the fitted surface

All Processing Trace plots keep calls and puts as separate series and show point markers on every plotted series.

## Greeks Rollout

- Surface-based model Greeks are computed from American prices by bump-and-reprice under sticky delta in moneyness space.
- `snapshot.greeks` now prefers those surface-based model Greeks when they are present, so review mode defaults to the smooth surface-driven path.
- Older routed Greeks are still saved and can be inspected for comparison and debugging.

## Plot Controls

- Line plots auto-range to the current data region when the plot is still in auto mode.
- Manual pan or zoom disables auto-follow for that plot until the user presses `Auto X`, `Auto Y`, or `Auto Both`.
- These range controls are available on overlay, model-vs-market, validation, density, runtime metrics, and Processing Trace line plots.

## Limitations

- One early-exercise-premium path used during American-to-European conversion is still MVP-grade and remains under validation.
- `ui_live` still uses the same Python-native desktop stack. This implementation focused on review trust, calibration traceability, and consistent saved-snapshot scoping rather than a frontend rewrite.
