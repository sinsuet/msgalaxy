from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

COMSOL_FIELD_REGISTRY_VERSION = "1.0"


@dataclass(frozen=True)
class ComsolFieldSpec:
    key: str
    label: str
    expression: str
    expression_candidates: tuple[str, ...]
    unit: str
    export_basename: str
    dataset_role: str


_FIELD_REGISTRY: Dict[str, ComsolFieldSpec] = {
    "temperature": ComsolFieldSpec(
        key="temperature",
        label="Temperature",
        expression="T",
        expression_candidates=("T",),
        unit="K",
        export_basename="temperature",
        dataset_role="thermal_stationary",
    ),
    "displacement_magnitude": ComsolFieldSpec(
        key="displacement_magnitude",
        label="Displacement Magnitude",
        expression="solid.disp",
        expression_candidates=("solid.disp", "sqrt(u^2+v^2+w^2)"),
        unit="m",
        export_basename="displacement",
        dataset_role="structural_stationary",
    ),
    "displacement_u": ComsolFieldSpec(
        key="displacement_u",
        label="Displacement U",
        expression="u",
        expression_candidates=("u", "solid.u"),
        unit="m",
        export_basename="displacement_u",
        dataset_role="structural_stationary",
    ),
    "displacement_v": ComsolFieldSpec(
        key="displacement_v",
        label="Displacement V",
        expression="v",
        expression_candidates=("v", "solid.v"),
        unit="m",
        export_basename="displacement_v",
        dataset_role="structural_stationary",
    ),
    "displacement_w": ComsolFieldSpec(
        key="displacement_w",
        label="Displacement W",
        expression="w",
        expression_candidates=("w", "solid.w"),
        unit="m",
        export_basename="displacement_w",
        dataset_role="structural_stationary",
    ),
    "von_mises": ComsolFieldSpec(
        key="von_mises",
        label="von Mises Stress",
        expression="solid.mises",
        expression_candidates=("solid.mises", "solid.svm", "mises"),
        unit="Pa",
        export_basename="von_mises",
        dataset_role="structural_stationary",
    ),
}

_FIELD_ALIASES = {
    "displacement": "displacement_magnitude",
    "displacement_x": "displacement_u",
    "displacement_y": "displacement_v",
    "displacement_z": "displacement_w",
    "stress": "von_mises",
}


def get_field_registry() -> Mapping[str, ComsolFieldSpec]:
    return dict(_FIELD_REGISTRY)


def get_field_aliases(name: Any) -> tuple[str, ...]:
    key = get_field_spec(name).key
    aliases = sorted(alias for alias, target in _FIELD_ALIASES.items() if target == key)
    return tuple(aliases)


def get_field_spec(name: Any) -> ComsolFieldSpec:
    key = str(name or "").strip().lower()
    resolved = _FIELD_ALIASES.get(key, key)
    if resolved not in _FIELD_REGISTRY:
        raise KeyError(f"unknown_comsol_field:{name}")
    return _FIELD_REGISTRY[resolved]


def build_field_registry_manifest() -> Dict[str, Dict[str, Any]]:
    return {
        key: {
            "key": spec.key,
            "aliases": list(get_field_aliases(spec.key)),
            "label": spec.label,
            "expression": spec.expression,
            "expression_candidates": list(spec.expression_candidates),
            "unit": spec.unit,
            "export_basename": spec.export_basename,
            "dataset_role": spec.dataset_role,
        }
        for key, spec in _FIELD_REGISTRY.items()
    }
