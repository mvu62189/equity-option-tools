# Latency And Cadence

## Default Refresh Intervals

- focused short-expiry refresh: `15s`
- full-surface refresh: `300s`

## Slower Fallback

The worker slows down when:

- repeated fetch failures accumulate
- recent fetch latency becomes too large relative to the fast short-expiry refresh

Current fallback caps:

- focused short-expiry refresh: `30s`
- full-surface refresh: `600s`

## Why This Shape

The current provider is snapshot-oriented, not tape-oriented. That leads to a deliberate tradeoff:

- keep the scanner responsive for the expiries the trader cares about most
- refresh the full surface more slowly so validation context stays coherent
- do not pretend to provide tick-level flow semantics when the source cannot support it

## UI Latency Work

The biggest shipped UI latency improvement in this feature is reuse of prebuilt chart and table data:

- build once per saved snapshot
- reuse while the user changes controls
- clear only when a new saved snapshot arrives

This is lower-risk than redesigning the Qt plus asyncio topology and matches the current pipeline better.
