# 0001-mode-runtime-decoupling

- status: accepted
- date: 2026-03-04
- deciders: msgalaxy-core

## Context

MsGalaxy currently runs two production modes (`agent_loop`, `mass`) and is preparing a new LLM policy-program layer.
Core runtime files have accumulated mixed responsibilities:
- mode routing + mode implementation in one class,
- LLM planners with mixed responsibilities in one module,
- scenario scripts that patch runtime behavior directly.

This increases coupling and makes future LLM strategy evolution risky.

## Decision

Adopt a mode-decoupled runtime architecture:
1. Keep one orchestrator as thin bootstrap/router only.
2. Split execution into independent mode runners:
   - `workflow/modes/agent_loop/*`
   - `workflow/modes/mass/*`
   - `workflow/modes/vop_maas/*` (new LLM scheme host)
3. Split LLM control into dedicated controllers:
   - strategic planner (agent_loop)
   - intent modeler (mass)
   - policy programmer (new scheme)
4. Replace script monkey patching with explicit provider/adapter injection.
5. Keep pymoo as numeric optimization core and preserve hard-constraint contract `g(x)<=0`.

## Consequences

### Positive
- Clear architecture boundaries and lower blast radius per change.
- Better readability and testability.
- New LLM scheme can evolve without contaminating legacy pipelines.

### Negative
- Initial migration cost and temporary dual-path maintenance.
- Requires regression harness to guarantee artifact compatibility.

### Neutral / Constraints
- No claim of new scientific capability from this refactor alone.
- Must preserve current benchmark semantics and observability fields.

## Alternatives Considered

1. Keep current monolithic structure and add comments/config guards.
- Rejected: does not solve coupling root cause.

2. Fully rewrite runtime in one-shot.
- Rejected: too risky for current unstable COMSOL online path.

3. Phase-wise split with compatibility gates.
- Selected: best risk-control vs progress tradeoff.
