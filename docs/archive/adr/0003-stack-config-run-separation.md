# 0003-stack-config-run-separation

- status: accepted
- date: 2026-03-05
- deciders: msgalaxy-core

## Context

`agent_loop` and `mass/llm` artifacts were mixed in shared paths:
- BOM files in multiple roots (`config/bom_L*.json`, `config/mass_v2/*`)
- one large `config/system.yaml` carrying multi-stack knobs
- fragmented run entry scripts

This caused high misconfiguration risk and poor readability.

After initial split, `run/` root still kept MaaS-specific benchmark utilities, which
reintroduced boundary ambiguity between stack-level orchestration and mode-specific tooling.

## Decision

1. Split BOM by stack:
   - `config/bom/agent_loop/*`
   - `config/bom/mass/*`
2. Split stack base configs:
   - `config/system/agent_loop/base.yaml`
   - `config/system/mass/base.yaml`
   - `config/system/llm/base.yaml`
3. Add scenario registry:
   - `config/scenarios/registry.yaml`
4. Add unified run entry:
   - `run/run_scenario.py` (`--stack`, `--level`)
5. Canonicalize MaaS benchmark tooling into `run/mass/`:
   - `run/mass/benchmark_matrix.py`
   - `run/mass/benchmark_l1_l4_nsga2_smoke.py`
   - `run/mass/render_benchmark_dashboard.py`
   - `run/mass/smoke.py`
6. Remove legacy root benchmark shims; keep only canonical mode paths:
   - `run/mass/*` for MaaS benchmark and scenario execution
   - `run/agent_loop/*` for agent-loop scenario execution

## Consequences

### Positive
- Stack boundary is explicit at BOM/config/run layers.
- Reproducibility and onboarding clarity improve.
- Runtime ambiguity from legacy paths is eliminated.

### Negative
- Requires synchronized updates in docs/tests/scripts that referenced legacy root paths.
- Registry maintenance remains an explicit operational dependency.

### Constraints
- No change to optimization contract (`g(x) <= 0`, pymoo core).
- No over-claim of M4 neural capability.


