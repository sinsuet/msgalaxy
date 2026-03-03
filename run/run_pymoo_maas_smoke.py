#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pymoo_maas smoke run (non-COMSOL backend).

Usage:
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_pymoo_maas_smoke.py
"""

import io
import json
import os
import sys
import tempfile
from pathlib import Path

import yaml

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from optimization.protocol import ModelingConstraint, ModelingIntent, ModelingObjective, ModelingVariable
from workflow.orchestrator import WorkflowOrchestrator


def _build_smoke_intent(component_ids, runtime_constraints):
    variables = []
    for comp_id in component_ids:
        for axis in ("x", "y", "z"):
            variables.append(
                ModelingVariable(
                    name=f"{comp_id}_{axis}",
                    component_id=comp_id,
                    variable_type="continuous",
                    lower_bound=-120.0,
                    upper_bound=120.0,
                    unit="mm",
                    description=f"{axis}-position",
                )
            )

    objectives = [
        ModelingObjective(
            name="min_cg_offset",
            metric_key="cg_offset",
            direction="minimize",
            weight=1.0,
            description="keep centroid balanced",
        ),
        ModelingObjective(
            name="min_max_temp",
            metric_key="max_temp",
            direction="minimize",
            weight=1.0,
            description="reduce thermal peak",
        ),
    ]

    constraints = [
        ModelingConstraint(
            name="g_temp",
            metric_key="max_temp",
            relation="<=",
            target_value=float(runtime_constraints.get("max_temp_c", 60.0)),
            category="thermal",
            unit="C",
        ),
        ModelingConstraint(
            name="g_clearance",
            metric_key="min_clearance",
            relation=">=",
            target_value=float(runtime_constraints.get("min_clearance_mm", 5.0)),
            category="geometry",
            unit="mm",
        ),
        ModelingConstraint(
            name="g_cg",
            metric_key="cg_offset",
            relation="<=",
            target_value=float(runtime_constraints.get("max_cg_offset_mm", 20.0)),
            category="geometry",
            unit="mm",
        ),
    ]

    return ModelingIntent(
        intent_id="INTENT_SMOKE_001",
        iteration=1,
        problem_type="multi_objective",
        variables=variables,
        objectives=objectives,
        hard_constraints=constraints,
        soft_constraints=[],
        assumptions=[],
        notes="smoke_intent",
    )


def main():
    base_config_path = ROOT_DIR / "config" / "system.yaml"
    base_config = yaml.safe_load(base_config_path.read_text(encoding="utf-8"))

    base_config["simulation"]["backend"] = "simplified"
    base_config["optimization"]["mode"] = "pymoo_maas"
    base_config["optimization"]["max_iterations"] = 2
    base_config["optimization"]["pymoo_pop_size"] = 20
    base_config["optimization"]["pymoo_n_gen"] = 6
    base_config["optimization"]["pymoo_maas_max_attempts"] = 3
    base_config["optimization"]["pymoo_maas_enable_mcts"] = True
    base_config["optimization"]["pymoo_maas_mcts_budget"] = 3
    base_config["optimization"]["pymoo_maas_enable_physics_audit"] = False
    base_config["optimization"]["pymoo_maas_thermal_evaluator_mode"] = "proxy"

    if not str(base_config.get("openai", {}).get("api_key", "")).strip():
        base_config["openai"]["api_key"] = os.environ.get("OPENAI_API_KEY", "smoke_dummy_key")
    base_config["openai"]["model"] = "qwen3-max"

    bom = {
        "constraints": {
            "max_temperature": 52.0,
            "min_clearance": 5.0,
            "max_cg_offset": 25.0,
        },
        "components": [
            {
                "id": "Battery_01",
                "name": "Battery Pack",
                "dimensions": {"x": 90, "y": 70, "z": 40},
                "mass": 6.0,
                "power": 30.0,
                "category": "power",
            },
            {
                "id": "Payload_01",
                "name": "Payload Unit",
                "dimensions": {"x": 80, "y": 80, "z": 50},
                "mass": 4.5,
                "power": 16.0,
                "category": "payload",
            },
            {
                "id": "Avionics_01",
                "name": "Avionics Box",
                "dimensions": {"x": 70, "y": 60, "z": 35},
                "mass": 3.8,
                "power": 14.0,
                "category": "avionics",
            },
        ],
    }

    with tempfile.TemporaryDirectory(prefix="msgalaxy_smoke_") as tmpdir:
        tmp = Path(tmpdir)
        cfg_path = tmp / "smoke_system.yaml"
        bom_path = tmp / "smoke_bom.json"
        cfg_path.write_text(yaml.safe_dump(base_config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        bom_path.write_text(json.dumps(bom, ensure_ascii=False, indent=2), encoding="utf-8")
        component_ids = [str(item["id"]) for item in bom["components"]]

        orchestrator = WorkflowOrchestrator(config_path=str(cfg_path))

        def _patched_generate_modeling_intent(context, runtime_constraints=None, requirement_text=""):
            return _build_smoke_intent(component_ids, runtime_constraints or {})

        orchestrator.meta_reasoner.generate_modeling_intent = _patched_generate_modeling_intent

        final_state = orchestrator.run_optimization(
            bom_file=str(bom_path),
            max_iterations=2,
            convergence_threshold=0.01,
        )

        meta = dict(final_state.metadata or {})
        print("SMOKE_DONE")
        print(f"state_id={final_state.state_id}")
        print(f"attempts={meta.get('maas_attempt_count')}")
        print(f"mcts_enabled={meta.get('mcts_report', {}).get('enabled')}")
        print(f"mcts_stop_reason={meta.get('mcts_report', {}).get('stop_reason')}")
        print(f"diagnosis={meta.get('solver_diagnosis', {}).get('status')}")
        print(f"optimization_mode={meta.get('optimization_mode')}")


if __name__ == "__main__":
    main()
