# R11 Stack Split + L1-L4 Runtime Cleanup (20260305)

## Objective

Complete runtime separation for two active stacks:

- `mass` stack
- `agent_loop` stack

and remove legacy templates/files that caused path and mode ambiguity.

## Final State

1. Config split is canonical:
- BOM:
  - `config/bom/mass/*`
  - `config/bom/agent_loop/*`
- System base config:
  - `config/system/mass/base.yaml`
  - `config/system/agent_loop/base.yaml`
  - `config/system/llm/base.yaml`

2. Scenario registry is canonical:
- `config/scenarios/registry.yaml`
- stacks: `mass`, `agent_loop`
- levels: `L1`, `L2`, `L3`, `L4`

3. Run entries are canonical:
- unified dispatcher:
  - `run/run_scenario.py --stack --level`
- per-stack L1-L4:
  - `run/mass/run_L1.py` ~ `run/mass/run_L4.py`
  - `run/agent_loop/run_L1.py` ~ `run/agent_loop/run_L4.py`

4. Stack contract is fail-fast:
- module: `run/stack_contract.py`
- binding:
  - `mass -> mass`
  - `agent_loop -> agent_loop`
- BOM/base-config cross-stack mixing is rejected.

5. Legacy files/templates were removed:
- root legacy BOM files: `config/bom_L*.json`
- legacy system file: `config/system.yaml`
- legacy mass_v2 folder: `config/mass_v2/`
- legacy vop config folder: `config/system/vop/`
- old root run entries:
  - `run/run_L1_simple.py`
  - `run/run_L2_intermediate.py`
  - `run/run_L3_complex.py`
  - `run/run_L4_extreme.py`

## Notes

- This document reflects post-cleanup state, not a transition window.
- L1-L4 are the only scenario levels exposed in the split stack runner.
