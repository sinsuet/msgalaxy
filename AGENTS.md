# MsGalaxy Agent Guide

This file is the project-level operational memory and action guide for coding agents.
If any statement here conflicts with runtime code or project policies, follow this priority:
1. `HANDOFF.md` (single source of truth for status and architecture)
2. `RULES.md` (execution and editing rules)
3. `config/system.yaml` (runtime defaults)
4. This file

## Strict Rules

### Project mission and architecture

MsGalaxy targets automated 3D layout design for small satellites (including CubeSat-style and flat-panel configurations), from natural-language requirements and BOM inputs to physically feasible candidate layouts.

The active architecture is neuro-symbolic meta-optimization:
- LLM layer: requirement interpretation, modeling intent generation, constraint/objective specification, strategy updates.
- pymoo layer: numerical multi-objective search and Pareto optimization.
- Physics layer: proxy thermal model and optional online COMSOL feedback.

### Runtime baseline (must match current implementation)

Two optimization modes are active:
- `optimization.mode = "agent_loop"`: multi-agent iterative loop with physics evaluation.
- `optimization.mode = "pymoo_maas"`: A/B/C/D closed loop:
  - A. Understanding: generate `ModelingIntent`
  - B. Formulation: normalize constraints to `g(x) <= 0`
  - C. Coding/Execution: compile intent to `ElementwiseProblem` and run configured pymoo MOEA (`NSGA-II` / `NSGA-III` / `MOEA/D`)
  - D. Reflection: diagnose outcomes and optionally relax constraints for retry

Key implemented components:
- `workflow/maas_pipeline_service.py`
- `optimization/maas_compiler.py`
- `optimization/modeling_validator.py`
- `optimization/maas_reflection.py`
- `optimization/pymoo_integration/`
- `optimization/maas_mcts.py`

### Approved next-phase direction (OP-MaaS, not yet fully implemented)

As of 2026-03-02, the project approved a new direction:
- Upgrade LLM role from "intent/formulation helper" to "operator-program/meta-policy controller".
- Keep pymoo MOEA family as numeric optimizer core (default `NSGA-II`).
- Use proxy + online COMSOL as multi-fidelity physics feedback.

Critical boundary:
- Do not claim OP-MaaS capabilities as implemented unless code path exists and is tested.
- When reporting status, explicitly distinguish:
  - current baseline (`pymoo_maas` coordinate-centric optimization),
  - planned target (operator-program-guided optimization).

### Modeling and optimization rules

In `pymoo_maas` mode, do not directly output final component coordinates as the solution.
Instead:
- define decision variables with explicit bounds,
- define objectives and hard constraints,
- let pymoo search feasible coordinate assignments.

Hard constraints must be representable as `g(x) <= 0`:
- `metric <= target` -> `g = metric - target`
- `metric >= target` -> `g = target - metric`
- `metric == target` -> `g = |metric - target| - eps`

Generated pymoo problems must include:
- explicit bounds (`xl`, `xu`),
- explicit objective vector `F`,
- explicit inequality constraint vector `G`,
- duplicate elimination in NSGA-II/NSGA-III where supported (`eliminate_duplicates=True`).

Current active solvers are:
- `PymooNSGA2Runner` (`NSGA-II`, default, feasibility-first)
- `PymooNSGA3Runner` (`NSGA-III`)
- `PymooMOEADRunner` (`MOEA/D`, constrained path uses penalty-objective adapter + raw CV diagnostics)
Runtime switch is controlled by `optimization.pymoo_algorithm`.

Supported decision variable types in MaaS compile/execution:
- `continuous`
- `integer` (decoded via rounding)
- `binary` (decoded via threshold)

`categorical` is not currently compiled into executable vector variables.

For OP-MaaS development tasks, prefer this transition path:
- Stage R0: lock baseline metrics and trace schema.
- Stage R1: introduce `OperatorProgram` schema + validator + executable operator actions.
- Stage R2: inject operator-program bias into pymoo search operators and MCTS branching.
- Stage R3: add multi-fidelity scheduling policy tied to uncertainty/feasibility signals.

In all stages, avoid bypassing pymoo by directly emitting final coordinates as "optimized result".

### Physics and constraint enforcement rules

Current enforced checks include:
- geometry: minimum clearance, collision count (AABB-based), envelope boundary violation,
- thermal: max temperature limit,
- structural: minimum safety factor,
- mass-property: CG offset limit.

Thresholds are runtime-driven:
- base defaults from `config/system.yaml`,
- may be overridden by BOM constraints,
- orchestrator fallbacks exist for robustness.

### Toolchain and execution rules

Primary stack in active flow:
- LLM: `qwen3-max` (default and project-approved baseline)
- Optimization: `pymoo` + NumPy/SciPy
- Geometry/layout: internal layout engine + AABB checks + CAD export path
- Physics backend: COMSOL integration (with proxy/online evaluator bridge in MaaS)

Always use UTF-8-safe prefix and conda env for Python commands:
- `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy ...`

For architecture/workflow changes, update docs in this order:
1. `HANDOFF.md`
2. `PROJECT_SUMMARY.md`
3. relevant docs under `docs/`
4. this `AGENTS.md` when agent guidance changes

## Preferred Heuristics

### Search-space pruning and semantic guidance

Use semantic zoning when requirements are clear:
- explicit `assumptions` formats:
  - `zone:<id>:x1,y1,z1:x2,y2,z2:compA,compB`
  - JSON zone payload in string form
- fallback heuristic zoning for thermal-critical/high-power components to cooling-side bands.

`ElementwiseProblem._evaluate` is scalar by pymoo API, but internal geometry/constraint calculations should use NumPy vectorized utilities whenever practical for performance and stability.

Do not hardcode mission rules like "payload always +Z" or "propulsion always -Z" unless the requirement/BOM explicitly states them.

### Reflection and retry behavior

After each MaaS solve:
- diagnose feasibility/stall status,
- generate structured relaxation suggestions when needed,
- apply bounded relaxation and retry (if enabled),
- optionally run MCTS branch search with action priors and CV penalties,
- optionally run top-K physics audit (COMSOL backend only).

For OP-MaaS iterations, additionally require:
- action-level diagnostics (which operator program was used),
- first-feasible efficiency metrics (`first_feasible_eval`, `COMSOL_calls_to_first_feasible`),
- ablation-friendly logging so baseline vs OP-MaaS gains are attributable.

## Future Work

The following are domain expectations but not default hardcoded constraints in current implementation:
- FOV/EMC occlusion logic (e.g., star tracker keep-out) is not a default evaluator.
- thermal delta targets like `12.6 K` are not universal built-ins.
- hot-component spacing like `0.5 mm` is not a global hardcoded rule.
- packing efficiency is currently a placeholder metric in orchestrator and should not be treated as a trusted optimization target without implementation updates.

Potential roadmap extensions (implement before claiming support):
- Additional MOEA solvers beyond NSGA-II/NSGA-III/MOEA-D with compatible diagnostics and tests.
- First-class FOV/EMC constraints integrated into `ModelingIntent` -> compile -> evaluate pipeline.
- Physically grounded packing-efficiency metric replacing placeholder value in orchestrator.
