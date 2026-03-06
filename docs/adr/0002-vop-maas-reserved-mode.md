# 0002-vop-maas-reserved-mode

- status: accepted
- date: 2026-03-05
- deciders: msgalaxy-core

## Context

R4 requires a dedicated host mode for the upcoming LLM policy-program stack,
but M4 neural guidance and full policy-program execution semantics are not yet implemented.
We need:
- an explicit runtime mode boundary now,
- no over-claim of unimplemented capability,
- no bypass of pymoo numeric optimization core.

## Decision

Introduce `optimization.mode = "vop_maas"` as a reserved mode:
1. Add `workflow/modes/vop_maas/*` runner/service scaffold.
2. In `vop_maas`, call `policy_programmer` for proposal diagnostics only.
3. Delegate executable optimization to existing `mass` pipeline.
4. Mark run metadata explicitly (`optimization_mode=vop_maas`, delegated execution mode = `mass`).
5. Keep hard-constraint and `g(x) <= 0` contract unchanged.

## Consequences

### Positive
- Future LLM scheme has a clean runtime slot and routing boundary.
- Team can test mode wiring and observability before M4 algorithm rollout.
- Avoids contaminating baseline `agent_loop` / `mass` codepaths.

### Negative
- Temporary semantic overlap (`vop_maas` uses `mass` executor) may confuse readers if undocumented.
- Additional maintenance for transitional mode plumbing.

### Neutral / Constraints
- `vop_maas` is not evidence that new neural guidance is implemented.
- Publication claims must still treat M4 as not started.

