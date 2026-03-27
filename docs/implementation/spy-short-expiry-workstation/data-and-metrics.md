# Data And Metrics

## New Datasets

### `focus_expiry_summary`

Purpose:

- one row per focus bucket
- feeds the scanner cards and summary table

Key fields:

- `focus_label`
- `expiration`
- `eligible_ratio`
- `within_bid_ask_ratio`
- `atm_iv_ref`
- `iv_skew_wing_diff`
- `trust_score`
- `trust_status`
- `snapshot_age_sec`

### `dealer_exposure_points`

Purpose:

- strike-by-expiry dealer-style exposure view for the scanner heatmap and strike ladder

Key fields:

- `delta_exposure_oi`
- `gamma_exposure_oi`
- `vega_exposure_oi`
- `delta_exposure_volume_proxy`
- `gamma_exposure_volume_proxy`
- `vega_exposure_volume_proxy`

### `flow_proxy_points`

Purpose:

- explicit changes between saved snapshots, not tape-derived trades

Key fields:

- `delta_volume`
- `delta_open_interest`
- `delta_avg_market_mid`
- `delta_avg_iv_ref`
- `delta_gamma_exposure_oi`
- `proxy_confidence`
- `proxy_reason`

#### Snapshot-To-Snapshot Logic

Current implementation lives in [short_expiry_scanner.py](d:\equity-option-tools\python\flow_core\orchestration\short_expiry_scanner.py) and works as follows:

1. Build the current dealer-style scanner frame from the latest batch only.
   This means the activity estimate never compares partial mixed-batch state.

2. Take the latest previously persisted `dealer_exposure_points` batch as the comparison baseline.
   It does not compare against every historical batch, only the most recent prior scanner batch.

3. Join current and previous rows on:
   - `focus_label`
   - `expiration`
   - `option_type`
   - `strike`

4. For each matched or unmatched current row, compute changes between saved snapshots:
   - `delta_volume = current.volume - previous.volume`
   - `delta_open_interest = current.open_interest - previous.open_interest`
   - `delta_avg_market_mid = current.avg_market_mid - previous.avg_market_mid`
   - `delta_avg_iv_ref = current.avg_iv_ref - previous.avg_iv_ref`
   - `delta_delta_exposure_oi = current.delta_exposure_oi - previous.delta_exposure_oi`
   - `delta_gamma_exposure_oi = current.gamma_exposure_oi - previous.gamma_exposure_oi`
   - `delta_vega_exposure_oi = current.vega_exposure_oi - previous.vega_exposure_oi`

5. If there is no prior row for that keyed scanner point, the previous values are treated as zero and:
   - `proxy_reason = no_previous_scanner_batch`
   - the row is still persisted so the UI can show that this is a new or newly visible point

6. If there is a prior row, the row is labeled:
   - `proxy_reason = snapshot_delta_proxy_not_trade_tape`

7. `proxy_confidence` is currently a bounded additive score, not a probabilistic model.
   The score starts at `0.15` and then adds:
   - `+0.20` if a prior row exists
   - `+0.25` if `abs(delta_volume) > 0`
   - `+0.20` if `abs(delta_open_interest) > 0`
   - `+0.10` if `eligible_ratio >= 0.70`
   - `+0.10` if `within_bid_ask_ratio >= 0.70`
   The final score is capped at `0.95`.

8. The heuristic is intentionally descriptive, not inferential.
   It says "this strike-expiry point changed meaningfully between coherent snapshots" rather than "a sweep happened" or "buyers initiated this trade."

#### What It Can And Cannot Say

Useful signals:

- volume or open-interest expansion at a specific strike and focused expiry
- changes in the implied-volatility context
- changes in delta, gamma, or vega exposure aggregates
- appearance of new hotspots between scanner batches

Hard limits:

- no trade sequencing
- no buyer/seller initiation classification
- no sweep detection
- no multi-exchange print consolidation
- no distinction between real tape flow and stale-data correction unless the surrounding quality metrics suggest caution

#### Edge Cases

- Expiry roll:
  when `0DTE`, `1DTE`, or `EOW` map to different actual expiries than the previous batch, the proxy compares by actual expiry, not by label alone.
- Missing prior batch:
  new rows will often show large deltas simply because the previous keyed point did not exist.
- Quality deterioration:
  the heuristic remains persisted even when quote quality weakens, but `eligible_ratio` and `within_bid_ask_ratio` lower the confidence score.
- Provider refresh artifacts:
  because the source is snapshot-oriented, some apparent changes may reflect quote refresh timing rather than true market flow.

#### Operator Guidance

- Treat `flow_proxy_points` as a scanner prompt, not as a trade-classification engine.
- Use them together with:
  - `focus_expiry_summary` data-quality status
  - `scanner_levels`
  - price bid/ask range views
  - implied-volatility bid/ask range views
  - validation diagnostics
- If a proxy row is interesting but confidence is low, inspect the underlying quote-cleaning and surface diagnostics before trusting the signal.

### `scanner_levels`

Purpose:

- strike ladder for hotspot review and drilldown entry

Key fields:

- `total_volume`
- `total_open_interest`
- `avg_market_mid`
- `avg_iv_ref`
- `net_gamma_exposure_oi`
- `hotspot_score`

## Existing Inputs Reused

- `quote_quality_points`
- `surface_points`
- `surface_diagnostics`
- `runtime_metrics`

## Operator Interpretation

- `trusted` means the expiry bucket is comparatively healthy relative to current quote cleaning and fitted-surface checks.
- `review` means usable but not clean enough to trust without drilldown.
- `caution` means diagnostics are weak and the scanner output is mainly a prompt to inspect, not to trust blindly.
