# Manager Doc Hub

This folder is the manager-facing entry point for product guardrails, runtime behavior, data model decisions, and review notes.

Original repo docs remain untouched. Source copies live under `docs/manager/source_copies/`. Consolidated working docs live under `docs/manager/working/`.

## Where To Start

1. Product guardrails: [working/final_product_guardrails.md](d:/quant-pipeline-mvp/docs/manager/working/final_product_guardrails.md)
2. Runtime and UI/live responsibilities: [working/runtime_architecture_v1.md](d:/quant-pipeline-mvp/docs/manager/working/runtime_architecture_v1.md)
3. Persisted data model and offline bootstrap: [working/data_model_v1.md](d:/quant-pipeline-mvp/docs/manager/working/data_model_v1.md)
4. Future hybrid-surface spec: [working/hybrid_surface_constructor_spec.md](d:/quant-pipeline-mvp/docs/manager/working/hybrid_surface_constructor_spec.md)
5. Open input decisions for hybrid surface build-out: [working/hybrid_surface_inputs_needed.md](d:/quant-pipeline-mvp/docs/manager/working/hybrid_surface_inputs_needed.md)
6. Inventory of all discovered docs: [review/doc_inventory.md](d:/quant-pipeline-mvp/docs/manager/review/doc_inventory.md)
7. Possible delete candidates: [review/delete_candidates.md](d:/quant-pipeline-mvp/docs/manager/review/delete_candidates.md)

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

## Operating Rule

During this phase:

- originals are reference material only
- working docs are the clean, auditable target state
- suspected redundancies are recorded in `review/delete_candidates.md`
- no original doc should be deleted without explicit approval
