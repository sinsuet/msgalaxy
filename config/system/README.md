# System Config Split

Stack-specific base configs:

- `config/system/mass/base.yaml`
- `config/system/agent_loop/base.yaml`

These are selected by `run/run_scenario.py` via `config/scenarios/registry.yaml`.
`run/run_scenario.py` enforces stack contract fail-fast:
- `agent_loop` stack -> `config/system/agent_loop/*`
- `mass` stack -> `config/system/mass/*`

Legacy `config/system.yaml` has been removed. Use stack-specific base config only.
