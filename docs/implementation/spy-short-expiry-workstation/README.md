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
- short-expiry scanner plus the existing detailed review and validation tabs
- refresh-aware `ui_live` runtime
- saved scanner datasets and DuckDB registration
- reuse of prebuilt chart and table data for one selected saved snapshot

Explicit non-goals for this version:

- true order-flow or sweep classification
- multi-symbol scanner
- release packaging or installer work
