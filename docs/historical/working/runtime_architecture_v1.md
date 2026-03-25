# Runtime Architecture V1

## Runtime Modes

- `run_ui.py --with-live`: interactive single-process UI with one live worker thread
- `run_ui.py` without live: offline review mode that hydrates from stored snapshots
- `run_live.py`: live pipeline without UI
- `run_snapshot.py`: manual full snapshot capture
- `run_daemon.py`: headless ingestion/scheduler/persistence path

## Ownership Rules

1. UI never calls C++ directly from the UI thread.
2. Compute happens in the pipeline/service layer and results are published into atomic Python snapshots.
3. Overlay/viewmodel prep happens off the UI thread.
4. UI consumes immutable snapshot handles and latest-wins overlay payloads.

## After-Hours Scheduler Semantics

Target state for this phase:

1. At `17:00 ET`, live polling stops and the session enters final-snapshot mode.
2. A final computed batch may be captured if the app owns the live session.
3. UI should show freshness status and refresh affordances instead of continuing blind live pulls.
4. At `20:30 ET`, the app checks or offers an OI refresh.
5. Starting the app after-hours should hydrate from stored final data first.

## UI Refresh Model

The UI should support:

- latest-wins push/coalesced updates during live mode
- offline hydration from parquet on startup
- explicit refresh notifications/buttons after final-snapshot checkpoints
- temporal Greeks playback from stored batches

## Failure Policy

1. Strict runtime mode should fail loudly for required core backends.
2. Chart payloads should expose missing/degenerate data via metadata, not by silently inventing axes.
3. Offline mode should prefer coherent stored state over blank startup.
4. Final/OI refresh state should preserve batch lineage.
