# Latency And Cadence

## Default Cadence

- hot focused cadence: `15s`
- full-surface cadence: `300s`

## Backoff

The worker backs off when:

- repeated fetch failures accumulate
- recent fetch latency becomes too large relative to the hot cadence

Current backoff caps:

- hot cadence: `30s`
- full-surface cadence: `600s`

## Why This Shape

The current provider is snapshot-oriented, not tape-oriented. That leads to a deliberate tradeoff:

- keep the scanner responsive for the expiries the trader cares about most
- refresh the full surface more slowly so validation context stays coherent
- do not pretend to provide tick-level flow semantics when the source cannot support it

## UI Latency Work

The biggest shipped UI latency improvement in this feature is payload reuse:

- build once per batch
- reuse while the user changes controls
- invalidate only when a new batch arrives

This is lower-risk than redesigning the Qt plus asyncio topology and aligns better with the current pipeline.
