# System Config Split

Stack-specific base configs:

- `config/system/mass/base.yaml`

These are selected by `run/run_scenario.py` via `config/scenarios/registry.yaml`.
`run/run_scenario.py` enforces stack contract fail-fast:
- `mass` stack -> `config/system/mass/*`

Legacy `config/system.yaml` has been removed. Use stack-specific base config only.
