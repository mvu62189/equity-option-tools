# Doc Inventory

This inventory maps discovered markdown files to their role in the managed doc set.

## Core Product And Ops Docs

| Original | Role | Manager Handling |
| --- | --- | --- |
| `README.md` | repo entrypoint and run commands | copied to `source_copies/README.md`, summarized by `working/final_product_guardrails.md` and `working/runtime_architecture_v1.md` |
| `docs/architecture.md` | current architecture overview | copied and consolidated into `working/runtime_architecture_v1.md` |
| `docs/data_contracts.md` | current contract descriptions | copied and consolidated into `working/data_model_v1.md` |
| `docs/operations.md` | runtime/ops runbook | copied and consolidated into `working/runtime_architecture_v1.md` |

## Plan Docs

| Original | Role | Manager Handling |
| --- | --- | --- |
| `PLAN_UI_ASYNC_FINAL.md` | UI async/state/memory hardening plan | copied and merged into runtime/data-model working docs |
| `PLAN_UI_core_ui_scheduler_FINAL.md` | UI/runtime scheduler hardening plan | copied and merged into runtime/product working docs |

## Quant Spec Docs

| Original | Role | Manager Handling |
| --- | --- | --- |
| `python/flow_core/quant/Bjerksund_Stensland.md` | BS2002 reference and implementation guardrails | copied to source copies; retained as model reference |
| `python/flow_core/quant/Crank_N_FD.md` | FDM reference | copied to source copies; retained as model reference |
| `python/flow_core/quant/fdm_cn_logscheme_edit.md` | log-space FDM design refinement | copied to source copies; reflected in runtime/product docs where relevant |
| `python/flow_core/quant/laplace_put_boundary.md` | Laplace put boundary math note | copied to source copies; retained as model reference |
| `python/flow_core/quant/laplace_t_for_leaps.md` | Laplace LEAPS reference | copied to source copies; retained as model reference |
| `python/flow_core/quant/LUBA.md` | LUBA/RIM reference | copied to source copies; retained as model reference |
| `python/flow_core/quant/ssvi_logscheme_edit.md` | log-moneyness SSVI refinement | copied to source copies; reflected in product/runtime docs where relevant |
| `python/flow_core/quant/tree_Richdsn_extrp_1w_to_1m.md` | tree/Richardson spec | copied to source copies; retained as model reference |

## Working Docs Produced In This Phase

| Working Doc | Purpose |
| --- | --- |
| `working/final_product_guardrails.md` | exact target behavior for the MVP app and the user-visible rules |
| `working/runtime_architecture_v1.md` | live/UI/daemon/offline responsibilities and scheduler semantics |
| `working/data_model_v1.md` | batch metadata, persisted datasets, offline bootstrap, and batch-aware querying |
| `working/hybrid_surface_constructor_spec.md` | spec-only placeholder for future stitched multi-engine surface constructor |

## Notes

- Originals remain in place.
- Working docs should be treated as the current management layer.
- Delete recommendations, if any, are tracked separately in `delete_candidates.md`.
