# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MsGalaxy is an intelligent satellite design optimization system based on a three-layer neuro-symbolic collaborative architecture. It integrates 3D layout, real-world simulation (COMSOL), and AI-driven multi-disciplinary optimization.

**Current Version**: v2.0.2.1
**System Maturity**: 99%
**Last Updated**: 2026-02-28

**Language**: The codebase uses Chinese comments and documentation. All user-facing messages are in Chinese.

**Tech Stack**: Python 3.12, Qwen-Plus/GPT-4-Turbo, Pydantic 2.6+, py3dbp (3D bin packing), pythonocc-core (STEP export), COMSOL MPh, Scipy

## Important Documents

**CRITICAL**: Always refer to [handoff.md](handoff.md) for the most up-to-date project status, architecture decisions, and implementation details. This is the single source of truth for the project.

Other key documents:
- [README.md](README.md) - Project overview and quick start
- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) - Technical summary and version history
- [RULES.md](RULES.md) - Development guidelines

## Commands

### Environment Setup
```bash
# Create and activate conda environment
conda create -n msgalaxy python=3.12
conda activate msgalaxy

# Install dependencies
pip install -r requirements.txt

# Optional: Install pythonocc-core for STEP export
conda install -c conda-forge pythonocc-core

# Optional: Install MPh for COMSOL integration
pip install mph
```

### Testing
```bash
# Run end-to-end workflow test (10 iterations)
python test_real_workflow.py

# Check generated visualizations
ls experiments/run_*/visualizations/

# View complete log
cat experiments/run_*/run_log.txt

# Run pytest suite
pytest tests/ -v
```

### Running Optimization
```bash
# Run optimization with CLI
python -m api.cli optimize --max-iter 10

# List experiments
python -m api.cli list

# Show experiment details
python -m api.cli show run_20260228_000935
```

## Architecture

### Three-Layer Neuro-Symbolic Architecture

1. **Strategic Layer (战略层)**: Meta-Reasoner
   - Multi-disciplinary coordination and strategic decision-making
   - Chain-of-Thought reasoning with Few-Shot examples
   - Located in: [optimization/meta_reasoner.py](optimization/meta_reasoner.py)

2. **Tactical Layer (战术层)**: Multi-Agent System
   - Domain-specific agents: Geometry, Thermal, Structural, Power
   - DV2.0: 10 types of operators (8 geometry + 5 thermal)
   - Located in: [optimization/agents/](optimization/agents/)
   - Coordination: [optimization/coordinator.py](optimization/coordinator.py)

3. **Execution Layer (执行层)**: Tool Integration
   - Layout Engine: [geometry/layout_engine.py](geometry/layout_engine.py)
   - COMSOL Driver (Dynamic STEP Import): [simulation/comsol_driver.py](simulation/comsol_driver.py)
   - Structural Physics: [simulation/structural_physics.py](simulation/structural_physics.py)
   - Workflow Orchestrator (Smart Rollback): [workflow/orchestrator.py](workflow/orchestrator.py)

### DV2.0 Operators

**Geometry Operators** (8 types):
- MOVE, SWAP, ROTATE, DEFORM (FFD), ALIGN, CHANGE_ENVELOPE, ADD_BRACKET, REPACK

**Thermal Operators** (5 types):
- MODIFY_COATING, ADD_HEATSINK, SET_THERMAL_CONTACT, ADJUST_LAYOUT, CHANGE_ORIENTATION

### Core Data Protocol

All data exchange uses Pydantic models defined in [core/protocol.py](core/protocol.py):
- `DesignState`: Current design state with components, envelope, keepouts, state_id, parent_id
- `ComponentGeometry`: Component position, dimensions, rotation, mass, power, thermal properties
- `Vector3D`: 3D vector with conversion to/from numpy arrays
- `OperatorType`: Optimization operators (10 types in DV2.0)
- `ViolationType`: Constraint violations (THERMAL_OVERHEAT, GEOMETRY_CLASH, CG_OFFSET_EXCESSIVE, etc.)

Optimization-specific protocols in [optimization/protocol.py](optimization/protocol.py):
- `GlobalContextPack`: Input to Meta-Reasoner with metrics, violations, recent_failures, rollback_warning
- `StrategicPlan`: Output from Meta-Reasoner with strategy and task assignments
- `GeometryMetrics`, `ThermalMetrics`, `StructuralMetrics`, `PowerMetrics`
- `EvaluationResult`: Evaluation result for state pool storage

### Workflow Orchestration

Main orchestrator: [workflow/orchestrator.py](workflow/orchestrator.py)

The optimization loop:
1. Initialize design with 3D layout (bin packing algorithm)
2. Iterate (max 10 times):
   - Export STEP file (pythonocc-core)
   - Run COMSOL dynamic simulation (STEP import + Box Selection + numerical stability anchor + global thermal network)
   - Run physics simulation (geometry, thermal, structural, power analysis)
   - Check constraints and calculate penalty score
   - Check rollback conditions (simulation failure, penalty > 1000, 3 consecutive increases)
   - RAG knowledge retrieval (semantic + keyword + graph search)
   - Meta-Reasoner strategic decision (Chain-of-Thought)
   - Multi-Agent tactical execution (each agent proposes solutions)
   - Agent coordination (validate, detect conflicts, resolve)
   - Execute optimization operations
   - Update state and save to state pool
   - Record to Trace audit log
3. Output results (CSV, JSON, visualizations, report, run_log.txt)

### Key Modules

- **Geometry**: 3D bin packing ([geometry/packing.py](geometry/packing.py)), AABB keepout zones ([geometry/keepout.py](geometry/keepout.py)), FFD deformation ([geometry/ffd.py](geometry/ffd.py)), STEP export ([geometry/cad_export_occ.py](geometry/cad_export_occ.py))
- **Simulation**: COMSOL driver with dynamic STEP import ([simulation/comsol_driver.py](simulation/comsol_driver.py)), structural physics ([simulation/structural_physics.py](simulation/structural_physics.py)), simplified physics ([simulation/physics_engine.py](simulation/physics_engine.py))
- **Optimization**: RAG system ([optimization/knowledge/rag_system.py](optimization/knowledge/rag_system.py)), multi-objective optimization ([optimization/multi_objective.py](optimization/multi_objective.py))
- **Logging**: Experiment logger with run_log.txt ([core/logger.py](core/logger.py))
- **API**: CLI ([api/cli.py](api/cli.py)), REST server ([api/server.py](api/server.py))

## Configuration

Main config file: [config/system.yaml](config/system.yaml)

Key sections:
- `openai`: API key, model, temperature, base_url (supports Qwen)
- `simulation`: Backend type (comsol), mode (dynamic/static), paths to COMSOL
- `geometry`: Envelope dimensions, clearance, fill ratio
- `optimization`: Max iterations, convergence threshold, allowed operators

Environment variables can be used with `${VAR_NAME}` syntax (e.g., `${OPENAI_API_KEY}`).

## Output Files

Each optimization run creates a directory: `experiments/run_YYYYMMDD_HHMMSS/`

Contents:
- `run_log.txt`: Complete terminal log (all modules) ⭐
- `evolution_trace.csv`: Quantitative metrics per iteration (with penalty_score, state_id)
- `trace/`: Complete context tracking
  - `iter_XX_context.json`: Input to LLM
  - `iter_XX_plan.json`: LLM strategic plan
  - `iter_XX_eval.json`: Physics evaluation result
- `rollback_events.jsonl`: Rollback event log
- `llm_interactions/`: LLM request/response JSON files
- `step_files/`: STEP geometry files
- `mph_models/`: COMSOL model files
- `visualizations/`: PNG charts (evolution trace, 3D layout, thermal heatmap)
- `design_state_iter_XX.json`: Design state snapshots

## Important Notes

### Windows Encoding
The codebase handles Windows GBK encoding issues. The fix is already implemented in [test_real_workflow.py](test_real_workflow.py:19-26):
```python
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
```

### File Writing Protocol (from RULES.md)
- Always verify parent directories exist before writing files (use `mkdir -p`)
- Use forward slashes for paths (Git Bash/Conda environment on Windows)
- Avoid mixing `E:\` and `/e/` path styles
- If Write tool fails, fallback to bash `cat > filepath << 'EOF'`

### Path Conventions
- Use relative paths from workspace root
- The shell environment uses Unix-style paths even on Windows
- Quote paths with spaces: `cd "path with spaces/file.txt"`

### COMSOL Integration
- **Mode**: Dynamic STEP import (not static parameter adjustment)
- **Numerical Stability**: Implemented in v2.0.2
  - Numerical stability anchor: weak convective boundary (h=0.1 W/(m²·K))
  - Global thermal network: thin layer with h_gap=10 W/(m²·K)
- **API Fixes**: v2.0.2.1
  - ThinLayer parameter: `ds` (not `d`)
  - HeatFluxBoundary (not ConvectiveHeatFlux)

### LLM Integration
- System uses Qwen-Plus or GPT-4-Turbo
- Meta-Reasoner and agents use structured prompts with Chain-of-Thought
- RAG system uses OpenAI embeddings for semantic search
- Thermal Agent: strictly limited to 5 thermal operators
- Geometry Agent: aggressive center of mass balancing strategy (100-200mm strides)

## Development Workflow

### Adding a New Simulation Driver
1. Create driver file in [simulation/](simulation/)
2. Inherit from `SimulationDriver` base class in [simulation/base.py](simulation/base.py)
3. Implement `run_simulation()` method
4. Add new simulation type to `SimulationType` enum in [core/protocol.py](core/protocol.py)
5. Update config schema in [config/system.yaml](config/system.yaml)

### Adding a New Optimization Operator
1. Define operator in `OperatorType` enum in [core/protocol.py](core/protocol.py)
2. Implement operator logic in relevant agent ([optimization/agents/](optimization/agents/))
3. Update LLM system prompts to explain new operator usage
4. Update operation executor in [workflow/operation_executor.py](workflow/operation_executor.py)

### Adding a New Agent
1. Create agent file in [optimization/agents/](optimization/agents/)
2. Implement `generate_proposal()` method
3. Define proposal data structure in [optimization/protocol.py](optimization/protocol.py)
4. Register agent in [optimization/coordinator.py](optimization/coordinator.py)
5. Update Meta-Reasoner prompts to include new agent in task assignments

## Version History

- **v2.0.2.1** (2026-02-28): COMSOL API fixes (ThinLayer `ds`, HeatFluxBoundary)
- **v2.0.2** (2026-02-28): Ultimate fixes (numerical stability anchor, global thermal network, aggressive CG balancing)
- **v2.0.1** (2026-02-27): Bug fixes (Thermal Agent prompt, run_log.txt)
- **v2.0.0** (2026-02-27): DV2.0 complete (10 operators, dynamic geometry generation)
- **v1.5.1** (2026-02-27): Phase 4 complete (smart rollback, Trace audit log)
- **v1.5.0** (2026-02-27): Phase 3 complete (FFD deformation, structural physics, T⁴ radiation)
- **v1.4.0** (2026-02-27): Phase 2 complete (COMSOL dynamic import, STEP export)
- **v1.3.0** (2026-02-27): COMSOL radiation problem solved

## Current Status

**System Maturity**: 99%

**Completed**:
- ✅ DV2.0 ten-operator architecture
- ✅ COMSOL dynamic import architecture
- ✅ FFD deformation operator
- ✅ Structural physics integration (center of mass offset)
- ✅ Smart rollback mechanism
- ✅ Complete Trace audit log
- ✅ COMSOL numerical stability fixes
- ✅ Aggressive center of mass balancing

**Next Steps**:
- Phase 5: End-to-end optimization loop validation
- Phase 6: Production readiness (Docker, CI/CD, monitoring)

## References

- [handoff.md](handoff.md) - Project handoff document ⭐⭐⭐ (MOST IMPORTANT)
- [README.md](README.md) - Project overview
- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) - Technical summary
- [RULES.md](RULES.md) - Development guidelines
- [docs/archive/](docs/archive/) - Archived documents (Phase 2/3, v2.0.2 fixes, test reports)

# currentDate
Today's date is 2026-02-28.
