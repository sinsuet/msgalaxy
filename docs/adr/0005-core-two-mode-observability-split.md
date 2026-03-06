# 0005-core-two-mode-observability-split

- status: accepted
- date: 2026-03-05
- deciders: msgalaxy-core

## Context

`core/logger.py` and `core/visualization.py` accumulated mixed responsibilities across runtime modes:
- mode detection/normalization, I/O writing, and report rendering were coupled in single files,
- `mass` trace row shaping and event payload materialization lived inside `ExperimentLogger`,
- LLM interaction artifacts were not explicitly partitioned by active mode buckets.

This reduced readability and made incremental refactors harder.

## Decision

1. Keep only two active runtime buckets in core-facing observability:
   - `agent_loop`
   - `mass`
2. Add `core/mode_contract.py` for runtime mode normalization.
3. Extract LLM artifact storage into `core/llm_interaction_store.py`.
4. Extract mode-specific visualization dispatch into `core/visualization_mode_dispatch.py`.
5. Extract MaaS trace CSV/payload shaping into `core/mass_trace_store.py`.
6. Keep `core/logger.py` as orchestration facade calling specialized stores.

## Consequences

### Positive
- Clearer responsibilities and lower coupling inside `core/`.
- Easier future refactor of `visualization.py` and logger internals.
- More explicit two-mode observability contract for active pipelines.

### Negative
- Additional modules increase navigation overhead for new contributors.
- Requires synchronized test updates when payload schema evolves.

### Constraints
- No change to optimization semantics or solver behavior.
- No claim that reserved/new LLM mode is independently executable.
