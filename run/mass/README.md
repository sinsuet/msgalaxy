# mass Runtime

Legacy `run_L1.py` ~ `run_L4.py` entry scripts have been removed.

Use the unified scenario CLI instead:

```powershell
python run/run_scenario.py --stack mass --scenario optical_remote_sensing_bus
```

The rebuilt mass core now lives behind:

- `workflow/scenario_runtime.py`
- `workflow/modes/mass/pipeline_service.py`
- `config/scenarios/registry.yaml`
