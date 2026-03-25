# SPY Short-Expiry Workstation

Status: implementation record for the first shipped scanner-first workstation.

Included documents:

- `system-design.md`
- `ui-spec.md`
- `data-and-metrics.md`
- `latency-cadence.md`
- `execution-log.md`

Scope of this implementation:

- SPY-only landing workflow for `0DTE`, `1DTE`, and `EOW`
- focused short-expiry scanner plus existing drilldown/validation tabs
- cadence-aware `ui_live` runtime
- persisted scanner datasets and DuckDB registration
- batch-scoped UI payload caching

Explicit non-goals for this version:

- true order-flow or sweep classification
- multi-symbol scanner
- release packaging or installer work
