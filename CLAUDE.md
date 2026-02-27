# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MsGalaxy is an intelligent satellite design optimization system based on a three-layer neuro-symbolic collaborative architecture. It integrates 3D layout, real-world simulation (MATLAB/COMSOL), and AI-driven multi-disciplinary optimization.

**Language**: The codebase uses Chinese comments and documentation. All user-facing messages are in Chinese.

**Tech Stack**: Python 3.12, OpenAI GPT-4-turbo, Pydantic 2.6+, py3dbp (3D bin packing), MATLAB Engine API, COMSOL MPh, Scipy

## Commands

### Environment Setup
```bash
# Create and activate conda environment
conda create -n msgalaxy python=3.12
conda activate msgalaxy

# Install dependencies
pip install -r requirements.txt
```

### Testing
```bash
# Run all tests using the test runner
python run_tests.py

# Run integration tests (no API key required)
python test_integration.py

# Run specific module tests
python test_geometry.py
python test_simulation.py

# Run pytest suite
pytest tests/ -v

# Run specific test file
pytest tests/test_bom_parser.py -v

# Run with coverage
pytest --cov=. --cov-report=html
```

### Running Optimization
```bash
# Run optimization with CLI
python -m api.cli optimize

# Run with custom config
python -m api.cli optimize --config my_config.yaml --max-iter 30

# List experiments
python -m api.cli list

# Show experiment details
python -m api.cli show run_20260215_143022
```

### BOM (Bill of Materials) Operations
```bash
# Parse and validate BOM file
python core/bom_parser.py parse config/bom_example.json

# Generate BOM template
python core/bom_parser.py template json my_bom.json
```

### Visualization
```bash
# Generate visualizations for an experiment
python core/visualization.py experiments/run_20260215_143022
```

### COMSOL Model Creation (scripts/)
```bash
# Create complete satellite thermal model
python scripts/create_complete_satellite_model.py

# Create convection model
python scripts/create_convection_model.py

# Clean up old experiments
python scripts/clean_experiments.py
```

## Architecture

### Three-Layer Neuro-Symbolic Architecture

1. **Strategic Layer (战略层)**: Meta-Reasoner
   - Multi-disciplinary coordination and strategic decision-making
   - Located in: [optimization/meta_reasoner.py](optimization/meta_reasoner.py)

2. **Tactical Layer (战术层)**: Multi-Agent System
   - Domain-specific agents: Geometry, Thermal, Structural, Power
   - Located in: [optimization/agents/](optimization/agents/)
   - Coordination: [optimization/coordinator.py](optimization/coordinator.py)

3. **Execution Layer (执行层)**: Tool Integration
   - Layout Engine: [geometry/layout_engine.py](geometry/layout_engine.py)
   - Simulation Drivers: [simulation/](simulation/)
     - [matlab_driver.py](simulation/matlab_driver.py)
     - [comsol_driver.py](simulation/comsol_driver.py)
     - [physics_engine.py](simulation/physics_engine.py) (simplified physics)

### Core Data Protocol

All data exchange uses Pydantic models defined in [core/protocol.py](core/protocol.py):
- `DesignState`: Current design state with components, envelope, keepouts
- `ComponentGeometry`: Component position, dimensions, rotation, mass, power
- `Vector3D`: 3D vector with conversion to/from numpy arrays
- `OperatorType`: Optimization operators (MOVE, SWAP, ROTATE, ADD_SURFACE, DEFORM)
- `ViolationType`: Constraint violations (THERMAL_OVERHEAT, GEOMETRY_CLASH, etc.)

Optimization-specific protocols in [optimization/protocol.py](optimization/protocol.py):
- `GlobalContextPack`: Input to Meta-Reasoner with metrics and violations
- `StrategicPlan`: Output from Meta-Reasoner with strategy and task assignments
- `GeometryMetrics`, `ThermalMetrics`, `StructuralMetrics`, `PowerMetrics`

### Workflow Orchestration

Main orchestrator: [workflow/orchestrator.py](workflow/orchestrator.py)

The optimization loop:
1. Initialize design with 3D layout (bin packing algorithm)
2. Iterate (max 20 times):
   - Run physics simulation (geometry, thermal, structural, power analysis)
   - Check constraints and generate violations
   - RAG knowledge retrieval (semantic + keyword + graph search)
   - Meta-Reasoner strategic decision (Chain-of-Thought)
   - Multi-Agent tactical execution (each agent proposes solutions)
   - Agent coordination (validate, detect conflicts, resolve)
   - Execute optimization operations
   - Update state and verify (re-simulate, compare metrics, accept/rollback)
   - Knowledge learning (add successful/failed cases to knowledge base)
3. Output results (CSV, JSON, visualizations, report)

### Key Modules

- **Geometry**: 3D bin packing ([geometry/packing.py](geometry/packing.py)), AABB keepout zones ([geometry/keepout.py](geometry/keepout.py)), FFD deformation ([geometry/ffd.py](geometry/ffd.py))
- **Simulation**: Base class ([simulation/base.py](simulation/base.py)), driver implementations for MATLAB/COMSOL/simplified physics
- **Optimization**: RAG system ([optimization/knowledge/rag_system.py](optimization/knowledge/rag_system.py)), multi-objective optimization ([optimization/multi_objective.py](optimization/multi_objective.py))
- **Logging**: Experiment logger with structured output ([core/logger.py](core/logger.py))
- **API**: CLI ([api/cli.py](api/cli.py)), REST server ([api/server.py](api/server.py)), WebSocket ([api/websocket_client.py](api/websocket_client.py))

## Configuration

Main config file: [config/system.yaml](config/system.yaml)

Key sections:
- `openai`: API key, model, temperature, base_url (supports OpenAI-compatible APIs like Qwen)
- `simulation`: Backend type (simplified/matlab/comsol), paths to MATLAB/COMSOL
- `geometry`: Envelope dimensions, clearance, fill ratio
- `optimization`: Max iterations, convergence threshold, allowed operators

Environment variables can be used with `${VAR_NAME}` syntax (e.g., `${OPENAI_API_KEY}`).

## Output Files

Each optimization run creates a directory: `experiments/run_YYYYMMDD_HHMMSS/`

Contents:
- `evolution_trace.csv`: Quantitative metrics per iteration
- `llm_interactions/`: LLM request/response JSON files
- `visualizations/`: PNG charts (evolution trace, 3D layout, thermal heatmap)
- `design_state_iter_XX.json`: Design state snapshots
- `summary.json`: Experiment summary
- `report.md`: Markdown report

## Important Notes

### Windows Encoding
The codebase handles Windows GBK encoding issues. If you encounter encoding problems, the fix is already implemented in [run_tests.py](run_tests.py:14-21) and should be applied to new scripts:
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

### Simulation Backends
- `simplified`: No external dependencies, runs anywhere
- `matlab`: Requires MATLAB Engine API installation
- `comsol`: Requires MPh package and COMSOL license

### LLM Integration
- System uses OpenAI API format (compatible with Qwen and other providers)
- Meta-Reasoner and agents use structured prompts with Chain-of-Thought
- RAG system uses OpenAI embeddings for semantic search

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

### Adding a New Agent
1. Create agent file in [optimization/agents/](optimization/agents/)
2. Implement `generate_proposal()` method
3. Define proposal data structure in [optimization/protocol.py](optimization/protocol.py)
4. Register agent in [optimization/coordinator.py](optimization/coordinator.py)
5. Update Meta-Reasoner prompts to include new agent in task assignments
