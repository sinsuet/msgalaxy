# 0004-optimization-mode-package-split

- status: accepted
- date: 2026-03-05
- deciders: msgalaxy-core

## Context

`optimization/` previously mixed shared primitives with mode-specific implementations:
- `agent_loop` agents/coordinator lived beside `mass` compiler/MCTS/operator modules,
- imports across workflow/tests referenced legacy top-level paths directly,
- readability and ownership boundaries were weak during ongoing R2-R4 refactor.

This made two-stack evolution (`agent_loop` vs `mass`) harder to reason about and increased migration risk.

## Decision

1. Split optimization implementations by mode:
   - `optimization/modes/agent_loop/*`
   - `optimization/modes/mass/*`
2. Keep only shared cross-mode modules at optimization root:
   - `optimization/protocol.py`
   - `optimization/meta_reasoner.py`
   - `optimization/llm/*`
   - `optimization/knowledge/*`
3. Remove legacy top-level mode modules and old agent shell paths.
4. Update workflow/tests imports to mode-qualified paths.
5. Add lightweight protocol compatibility re-exports under each mode package.

## Consequences

### Positive
- Clear mode ownership and lower coupling between `agent_loop` and `mass`.
- Reduced ambiguity in runtime import graph and easier code navigation.
- Better foundation for `vop_maas` reserved-mode extension without reintroducing mixed layers.

### Negative
- Requires synchronized edits in docs/tests and any external scripts using legacy imports.
- Short-term churn for downstream branches that still reference removed paths.

### Constraints
- No change to optimization semantics (`g(x) <= 0`, pymoo as numeric core).
- No new scientific capability claimed solely from this package refactor.
