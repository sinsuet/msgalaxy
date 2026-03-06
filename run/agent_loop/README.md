# agent_loop L1-L4 Entrypoints

Canonical agent_loop scenario scripts:

- `run/agent_loop/run_L1.py`
- `run/agent_loop/run_L2.py`
- `run/agent_loop/run_L3.py`
- `run/agent_loop/run_L4.py`

Execution model:

- force `--mode agent_loop`
- force `config/bom/agent_loop/*` BOM
- force `config/system/agent_loop/base.yaml`
- execute dedicated agent_loop runtime entry implementation

The unified dispatcher remains available:

`run/run_scenario.py --stack agent_loop --level Lx`
