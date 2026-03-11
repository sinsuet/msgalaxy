from __future__ import annotations

from typing import Any, Dict

from simulation.comsol.field_registry import get_field_spec

COMSOL_SIMULATION_METRIC_UNIT_CONTRACT_VERSION = "1.0"


def build_simulation_metric_unit_contract() -> Dict[str, Dict[str, Any]]:
    temperature_field = get_field_spec("temperature")
    stress_field = get_field_spec("stress")
    displacement_field = get_field_spec("displacement")
    return {
        "max_temp": {
            "summary_unit": "degC",
            "field_registry_key": temperature_field.key,
            "field_unit": temperature_field.unit,
            "summary_transform": "field_value_K - 273.15",
        },
        "min_temp": {
            "summary_unit": "degC",
            "field_registry_key": temperature_field.key,
            "field_unit": temperature_field.unit,
            "summary_transform": "field_value_K - 273.15",
        },
        "avg_temp": {
            "summary_unit": "degC",
            "field_registry_key": temperature_field.key,
            "field_unit": temperature_field.unit,
            "summary_transform": "field_value_K - 273.15",
        },
        "temp_gradient": {
            "summary_unit": "K",
            "field_registry_key": temperature_field.key,
            "field_unit": temperature_field.unit,
            "summary_transform": "max(T) - min(T)",
        },
        "max_stress": {
            "summary_unit": "MPa",
            "field_registry_key": stress_field.key,
            "field_unit": stress_field.unit,
            "summary_transform": "field_value_Pa / 1e6",
        },
        "max_displacement": {
            "summary_unit": "mm",
            "field_registry_key": displacement_field.key,
            "field_unit": displacement_field.unit,
            "summary_transform": "field_value_m * 1000",
        },
        "first_modal_freq": {
            "summary_unit": "Hz",
            "summary_transform": "direct_modal_frequency",
        },
        "safety_factor": {
            "summary_unit": "1",
            "summary_transform": "allowable_stress_mpa / max_stress_mpa",
        },
        "total_power": {
            "summary_unit": "W",
            "summary_transform": "direct_power_metric",
        },
        "peak_power": {
            "summary_unit": "W",
            "summary_transform": "direct_power_metric",
        },
        "power_margin": {
            "summary_unit": "%",
            "summary_transform": "direct_power_metric",
        },
        "voltage_drop": {
            "summary_unit": "V",
            "summary_transform": "direct_power_metric",
        },
    }
