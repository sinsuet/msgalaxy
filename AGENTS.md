# MsGalaxy Agent Guide

This file is the project-level operational memory and action guide for coding agents.
If any statement here conflicts with runtime code or project policies, follow this priority:
1. `HANDOFF.md` (single source of truth for status and architecture)
2. `RULES.md` (execution and editing rules)
3. `config/system/<stack>/base.yaml` + `config/scenarios/registry.yaml` (runtime defaults)
4. This file

## Strict Rules

### Project mission and architecture

MsGalaxy targets automated 3D layout design for small satellites (including CubeSat-style and flat-panel configurations), from natural-language requirements and BOM inputs to physically feasible candidate layouts.

The active baseline architecture is neuro-symbolic meta-optimization:
- LLM layer: requirement interpretation, modeling intent generation, constraint/objective specification, strategy updates.
- pymoo layer: numerical multi-objective search and Pareto optimization.
- Physics layer: proxy thermal model and optional online COMSOL feedback.

Approved upgrade target (planning state) is `MP-OP-MaaS v3`:
- keep pymoo MOEA as numeric optimizer core (do not replace with direct coordinate generation),
- expand from thermal-dominant checks to multiphysics constraint contract (geometry + thermal + structural + power + mission keep-outs),
- add neural guidance modules for feasibility prediction, operator policy, and multi-fidelity scheduling.

### Runtime baseline (must match current implementation)

Three optimization modes are active:
- `optimization.mode = "agent_loop"`: multi-agent iterative loop with physics evaluation.
- `optimization.mode = "mass"`: A/B/C/D closed loop:
  - A. Understanding: generate `ModelingIntent`
  - B. Formulation: normalize constraints to `g(x) <= 0`
  - C. Coding/Execution: compile intent to `ElementwiseProblem` and run configured pymoo MOEA (`NSGA-II` / `NSGA-III` / `MOEA/D`)
  - D. Reflection: diagnose outcomes and optionally relax constraints for retry
- `optimization.mode = "vop_maas"`: reserved mode for upcoming LLM policy-program stack; currently runs policy-program proposal diagnostics and delegates executable search to `mass` core.

Key implemented components:
- `workflow/modes/mass/pipeline_service.py` (canonical)
- `workflow/modes/mass/runtime_support.py`
- `workflow/modes/vop_maas/policy_program_service.py`
- `workflow/modes/agent_loop/runtime_support.py`
- `workflow/runtime/runtime_facade.py`
- `optimization/modes/mass/maas_compiler.py`
- `optimization/modes/mass/modeling_validator.py`
- `optimization/modes/mass/maas_reflection.py`
- `optimization/modes/mass/pymoo_integration/`
- `optimization/modes/mass/maas_mcts.py`
- `optimization/modes/agent_loop/coordinator.py`
- `optimization/llm/controllers/`
- `optimization/knowledge/mass/` (`CGRAG-Mass` structured evidence retriever for `mass`)

RAG baseline note:
- `mass` now uses `CGRAG-Mass` as default retrieval backend.
- Legacy `optimization/knowledge/rag_system.py` is removed and should not be reintroduced.

Stack separation contract (implemented):
- canonical BOM roots:
  - `config/bom/mass/*` for `mass`
  - `config/bom/agent_loop/*` for `agent_loop`
- canonical base configs:
  - `config/system/mass/base.yaml`
  - `config/system/agent_loop/base.yaml`
- unified entry: `run/run_scenario.py --stack --level`
- fail-fast guard: `run/stack_contract.py`
  - enforces `stack -> mode` binding
  - rejects cross-stack BOM/base-config path mixing
- stack entry adapters:
  - `run/agent_loop/*` force `agent_loop + agent_loop BOM/base-config` with dedicated runner implementation

### Approved next-phase direction (`MP-OP-MaaS v3`, not yet fully implemented)

As of 2026-03-04, the project approved this direction:
- Upgrade LLM role from "intent/formulation helper" to "constraint+operator+policy program controller".
- Keep pymoo MOEA family as numeric optimizer core (default `NSGA-II`).
- Use proxy + online COMSOL as multi-fidelity physics feedback.
- Introduce operator DSL v3 and multiphysics hard-constraint contract with staged rollout.

Critical boundary:
- Do not claim `MP-OP-MaaS v3` capabilities as implemented unless code path exists and is tested.
- When reporting status, explicitly distinguish:
  - current baseline (`mass` with existing implemented constraints/actions),
  - planned target (`MP-OP-MaaS v3` with upgraded multiphysics/operator/neural modules).

### Modeling and optimization rules

In `mass` mode, do not directly output final component coordinates as the solution.
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
- Stage M0: lock baseline metrics and trace schema.
- Stage M1: enforce mandatory hard-constraint coverage + metric registry gate.
- Stage M2: add physically meaningful structural/power proxy metrics to executable pipeline (implemented on orchestrator + pymoo evaluator paths).
- Stage M3: upgrade to `OperatorProgram DSL v3` and inject operator bias into pymoo/MCTS (implemented as executable thin-slice with thermal/structural/power/mission action families).
- Stage M4: add neural modules (feasibility predictor, operator policy, MF scheduler) for budget-efficient search.

In all stages, avoid bypassing pymoo by directly emitting final coordinates as "optimized result".

### Physics and constraint enforcement rules

Current runtime truth (must report accurately):
- geometry checks are active: minimum clearance, collision count (AABB-based), envelope boundary violation,
- thermal max-temperature constraint is active (proxy and/or online COMSOL path),
- mass-property CG offset limit is active,
- structural/power proxy checks are active in executable paths (`workflow/orchestrator.py` and `optimization/modes/mass/pymoo_integration/problem_generator.py`), including safety factor / modal frequency / voltage drop / power margin (and optional peak-power budget gate),
- operator-program DSL v3 thin-slice actions are executable across validator + intent mutation + genome codec paths (`group_move/cg_recenter/hot_spread/swap/add_heatstrap/set_thermal_contact/add_bracket/stiffener_insert/bus_proximity_opt/fov_keepout_push`),
- do not over-claim these proxy checks as full high-fidelity multiphysics coupling.

Target v3 constraint contract (planned rollout):
- mandatory base hard constraints: `collision`, `clearance`, `boundary`, `thermal`, `cg_limit`,
- staged extension constraints: component-level thermal bounds, structural (`stress/safety_factor/modal_freq`), power (`margin/voltage_drop/SOC`), mission constraints (`FOV/EMC keep-out`).

Constraint governance requirement:
- introduce and enforce metric registry for hard constraints in benchmark/release profiles,
- unknown/unimplemented metric keys must not silently pass as valid hard constraints.

Thresholds are runtime-driven:
- base defaults from stack base config (`config/system/<stack>/base.yaml`),
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

Recommended v3 smoke harness (NSGA-II, L1-L4):
- `run/mass/benchmark_l1_l4_nsga2_smoke.py`
- uses deterministic v3 multiphysics intent and strict hard-constraint coverage checks.

Preferred scenario entry (stack-safe):
- `run/run_scenario.py`

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

For OP-MaaS / v3 iterations, additionally require:
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
- Operator DSL v3 action family with executable thermal/structural/power/mission operators.
- Neural guidance modules for feasibility prediction, operator ranking, and multi-fidelity scheduling.

