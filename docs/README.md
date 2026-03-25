# Docs Map

This `docs/` tree is split into three labeled document sets.

## Canonical Docs

These files describe the current implemented system and should win if they disagree with older notes:

- `architecture.md`
- `operations.md`
- `data_contracts.md`

## Historical Planning Artifacts

These files are preserved for planning history, traceability, and review context, but they are not the default source of truth:

- `historical/working/`
- `historical/source_copies/`
- `historical/review/`

## Implementation Records

These files explain how currently shipped features were designed and landed. They are more detailed than the canonical docs, but they are not the top-level source of truth for operator behavior:

- `implementation/`
- `implementation/spy-short-expiry-workstation/`

Useful review entrypoints:

- `historical/review/ahead_of_development.md` for features and docs that are still ahead of the current implementation

## Operating Rule

- Prefer top-level `docs/*.md` for current behavior.
- Use `docs/implementation/**` for build notes, rollout details, and implementation-specific rationale.
- Treat `docs/historical/**` as historical or planning material unless a file explicitly says it has been promoted.
- If a historical decision becomes current, promote it into the top-level canonical docs rather than relying on the archived copy.
