# BOM Stack Split

Canonical BOM locations:

- `config/bom/mass/`: mass stack scenarios
- `config/bom/agent_loop/`: agent_loop stack scenarios

Use `run/run_scenario.py` + `config/scenarios/registry.yaml` to resolve BOM paths.
`run/run_scenario.py` now enforces stack contract fail-fast:
- `agent_loop` stack only accepts `config/bom/agent_loop/*`
- `mass` stack only accepts `config/bom/mass/*`

Legacy root files (`config/bom_L*.json`) and `config/mass_v2/*` have been removed.
Use split BOM paths only.
