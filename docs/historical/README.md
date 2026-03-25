# Historical Planning Doc Hub

Status: Historical planning artifacts and manager-facing working material. Not canonical unless promoted into top-level `docs/`.

This folder is the manager-facing entry point for product guardrails, runtime behavior, data model decisions, and review notes.

Canonical docs live at the top level of `docs/`. Source copies live under `docs/historical/source_copies/`. Consolidated working docs live under `docs/historical/working/`.

## Where To Start

1. Product guardrails: [working/final_product_guardrails.md](working/final_product_guardrails.md)
2. Runtime and UI/live responsibilities: [working/runtime_architecture_v1.md](working/runtime_architecture_v1.md)
3. Persisted data model and offline bootstrap: [working/data_model_v1.md](working/data_model_v1.md)
4. Future hybrid-surface spec: [working/hybrid_surface_constructor_spec.md](working/hybrid_surface_constructor_spec.md)
5. Open input decisions for hybrid surface build-out: [working/hybrid_surface_inputs_needed.md](working/hybrid_surface_inputs_needed.md)
6. Inventory of all discovered docs: [review/doc_inventory.md](review/doc_inventory.md)
7. Possible delete candidates: [review/delete_candidates.md](review/delete_candidates.md)
8. Forward-looking items still ahead of implementation: [review/ahead_of_development.md](review/ahead_of_development.md)

## Source Copies

The `source_copies/` folder contains manager-safe copies of:

- root README
- active architecture/data-contracts/operations docs
- major UI/runtime plan docs
- quant model markdown specs that currently inform implemented behavior

Each copy includes:

- original source path
- copy date
- reminder that the original file was not modified
- any embedded links or legacy path references the snapshot originally contained

## Operating Rule

During this phase:

- top-level `docs/*.md` are the current reference docs
- source copies are reference material only
- working docs are planning/analysis targets, not the default source of truth
- suspected redundancies are recorded in `review/delete_candidates.md`
- no original doc should be deleted without explicit approval
