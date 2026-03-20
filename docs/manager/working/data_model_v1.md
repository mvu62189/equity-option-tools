# Data Model V1

## Batch Identity

Every raw and derived batch should carry:

- `batch_id`
- `symbol`
- `asof_ts`
- `trading_date`
- `snapshot_kind`
- `source_mode`

These fields allow the UI and SQL layer to reconstruct a coherent batch instead of guessing from filesystem timestamps.

## Snapshot Kinds

- `live_batch`
- `manual_snapshot`
- `eod_final`
- `eod_oi_refresh`
- `offline_bootstrap`

## Persisted Datasets

Current/target datasets:

- raw option quotes
- routed Greeks
- SSVI diagnostics/summary
- dispatch summary
- parity summary
- parity detail
- calibration diagnostics
- `snapshot_catalog`
- `oi_refresh_deltas`

## Snapshot Catalog

One row per coherent batch, including:

- `batch_id`
- `symbol`
- `trading_date`
- `snapshot_kind`
- `source_mode`
- `is_final_for_day`
- `parent_batch_id`
- completion/freshness timestamps

The catalog becomes the batch-aware source of truth for latest-final resolution.

## OI Refresh Delta Model

`oi_refresh_deltas` stores OI/volume style changes keyed against the final batch they update.

Rules:

1. OI refresh does not overwrite `eod_final` prices, Greeks, or calibration outputs.
2. OI refresh can be layered onto the final batch for after-hours review.
3. Parent linkage must remain explicit through `parent_batch_id`.

## Offline Bootstrap Contract

Offline UI startup should:

1. load the latest stored `is_final_for_day=true` batch for the symbol
2. load same-day `oi_refresh_deltas` if present
3. publish a coherent `offline_bootstrap` snapshot into memory
4. only then notify the user about refresh opportunities

## Querying Contract

DuckDB views should become batch-aware:

- latest-final queries resolve through `snapshot_catalog`
- routed Greeks and diagnostics remain queryable by `batch_id`
- after-hours review can inspect both the final batch and later OI deltas without ambiguity
