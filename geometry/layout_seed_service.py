"""
Layout seed service built on top of the migrated layout engine.
"""

from __future__ import annotations

import copy
import random
from typing import Any, Callable, Dict, List, Mapping, Optional

import numpy as np

from core.protocol import ComponentGeometry, DesignState, Envelope, Vector3D
from geometry.catalog_geometry import extract_catalog_component_specs_from_layout_config
from geometry.layout_engine import LayoutEngine
from geometry.schema import EnvelopeGeometry, PackingResult


def _component_from_part(
    part: Any,
    *,
    clearance_mm: float,
    component_props_by_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> ComponentGeometry:
    pos_min = np.asarray(part.get_actual_position(), dtype=float)
    dims = np.asarray([float(part.dims[0]), float(part.dims[1]), float(part.dims[2])], dtype=float)
    center_pos = pos_min + dims / 2.0
    comp_props = dict((component_props_by_id or {}).get(str(part.id), {}) or {})

    return ComponentGeometry(
        id=str(part.id),
        position=Vector3D(
            x=float(center_pos[0]),
            y=float(center_pos[1]),
            z=float(center_pos[2]),
        ),
        dimensions=Vector3D(
            x=float(dims[0]),
            y=float(dims[1]),
            z=float(dims[2]),
        ),
        rotation=Vector3D(x=0.0, y=0.0, z=0.0),
        mass=float(getattr(part, "mass", 0.0)),
        power=float(getattr(part, "power", 0.0)),
        category=str(getattr(part, "category", "unknown") or "unknown"),
        clearance=float(clearance_mm),
        thermal_contacts=dict(comp_props.get("thermal_contacts", {}) or {}),
        emissivity=(
            float(comp_props.get("emissivity"))
            if comp_props.get("emissivity") is not None
            else 0.8
        ),
        absorptivity=(
            float(comp_props.get("absorptivity"))
            if comp_props.get("absorptivity") is not None
            else 0.3
        ),
        coating_type=str(comp_props.get("coating_type") or "default"),
    )


def _envelope_from_geometry(envelope_geom: EnvelopeGeometry) -> Envelope:
    outer_size = envelope_geom.outer_size()
    inner_size = envelope_geom.inner_size()
    return Envelope(
        outer_size=Vector3D(
            x=float(outer_size[0]),
            y=float(outer_size[1]),
            z=float(outer_size[2]),
        ),
        inner_size=Vector3D(
            x=float(inner_size[0]),
            y=float(inner_size[1]),
            z=float(inner_size[2]),
        ),
        thickness=float(envelope_geom.thickness_mm),
        fill_ratio=float(envelope_geom.fill_ratio),
        origin="center",
    )


def packing_result_to_design_state(
    *,
    packing_result: PackingResult,
    envelope_geom: EnvelopeGeometry,
    clearance_mm: float,
    component_props_by_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
    iteration: int = 0,
    state_id: str = "state_iter_00_init",
    parent_id: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> DesignState:
    components = [
        _component_from_part(
            part,
            clearance_mm=clearance_mm,
            component_props_by_id=component_props_by_id,
        )
        for part in list(getattr(packing_result, "placed", []) or [])
    ]
    return DesignState(
        iteration=int(iteration),
        components=components,
        envelope=_envelope_from_geometry(envelope_geom),
        metadata=copy.deepcopy(dict(metadata or {})),
        state_id=str(state_id),
        parent_id=parent_id,
    )


def apply_packing_result_to_reference_state(
    *,
    reference_state: DesignState,
    packing_result: PackingResult,
    envelope_geom: Optional[EnvelopeGeometry] = None,
    state_id: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> DesignState:
    placed_by_id = {
        str(part.id): part
        for part in list(getattr(packing_result, "placed", []) or [])
    }
    required_ids = {
        str(comp.id)
        for comp in list(getattr(reference_state, "components", []) or [])
    }
    if not required_ids.issubset(set(placed_by_id.keys())):
        missing = sorted(required_ids - set(placed_by_id.keys()))
        raise ValueError(f"packing_result_missing_components:{','.join(missing)}")

    state = reference_state.model_copy(deep=True)
    for comp in list(state.components or []):
        part = placed_by_id[str(comp.id)]
        pos_min = np.asarray(part.get_actual_position(), dtype=float)
        dims = np.asarray([float(part.dims[0]), float(part.dims[1]), float(part.dims[2])], dtype=float)
        center_pos = pos_min + dims / 2.0
        comp.position = Vector3D(
            x=float(center_pos[0]),
            y=float(center_pos[1]),
            z=float(center_pos[2]),
        )
        comp.dimensions = Vector3D(
            x=float(dims[0]),
            y=float(dims[1]),
            z=float(dims[2]),
        )

    if envelope_geom is not None:
        state.envelope = _envelope_from_geometry(envelope_geom)

    if state_id is not None:
        state.state_id = str(state_id)
    if parent_id is not None:
        state.parent_id = parent_id
    return state


def _state_position_fingerprint(state: DesignState) -> tuple:
    return tuple(
        sorted(
            (
                str(comp.id),
                round(float(comp.position.x), 6),
                round(float(comp.position.y), 6),
                round(float(comp.position.z), 6),
                round(float(comp.dimensions.x), 6),
                round(float(comp.dimensions.y), 6),
                round(float(comp.dimensions.z), 6),
            )
            for comp in list(getattr(state, "components", []) or [])
        )
    )


def _merge_metadata(
    base_metadata: Optional[Mapping[str, Any]],
    extra_metadata: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    merged = copy.deepcopy(dict(base_metadata or {}))
    for key, value in dict(extra_metadata or {}).items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            nested = copy.deepcopy(dict(merged.get(key) or {}))
            nested.update(copy.deepcopy(dict(value)))
            merged[key] = nested
        else:
            merged[key] = copy.deepcopy(value)
    return merged


class LayoutSeedService:
    """
    Generate deterministic layout-derived seeds for the MaaS coordinate search.
    """

    def __init__(
        self,
        *,
        layout_engine: Optional[LayoutEngine] = None,
        layout_config: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if layout_engine is None and layout_config is None:
            raise ValueError("layout_engine or layout_config is required")

        source_config = layout_engine.config if layout_engine is not None else layout_config
        self.layout_config: Dict[str, Any] = copy.deepcopy(dict(source_config or {}))

    def _catalog_component_metadata(self) -> Dict[str, Any]:
        specs = extract_catalog_component_specs_from_layout_config(self.layout_config)
        if not specs:
            return {}
        return {
            "catalog_components": {
                comp_id: spec.model_dump() if hasattr(spec, "model_dump") else spec.dict()
                for comp_id, spec in specs.items()
            }
        }

    def _shell_metadata(self) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        for key in ("shell_spec", "shell_spec_file", "shell_spec_path"):
            if self.layout_config.get(key) is not None:
                metadata[key] = copy.deepcopy(self.layout_config.get(key))
        return metadata

    def generate_seed_states(
        self,
        *,
        reference_state: Optional[DesignState] = None,
        component_props_by_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
        clearance_mm: float = 5.0,
        max_count: int = 3,
        seed_start: int = 43,
        attempts_multiplier: int = 4,
        recenter_fn: Optional[Callable[[DesignState], DesignState]] = None,
        state_id_prefix: str = "layout_seed",
    ) -> List[DesignState]:
        requested = max(0, int(max_count))
        if requested <= 0:
            return []

        attempts = max(requested, int(requested) * max(1, int(attempts_multiplier)))
        seeds: List[DesignState] = []
        fingerprints: set[tuple] = set()
        seed_metadata = {}
        seed_metadata.update(self._catalog_component_metadata())
        seed_metadata.update(self._shell_metadata())

        for offset in range(attempts):
            random_seed = int(seed_start) + offset
            random.seed(random_seed)
            np.random.seed(random_seed)

            engine = LayoutEngine(config=copy.deepcopy(self.layout_config))
            packing_result = engine.generate_layout()

            try:
                if reference_state is None:
                    state = packing_result_to_design_state(
                        packing_result=packing_result,
                        envelope_geom=engine.envelope,
                        clearance_mm=float(clearance_mm),
                        component_props_by_id=component_props_by_id,
                        metadata=seed_metadata,
                        state_id=f"{state_id_prefix}_{random_seed}",
                    )
                else:
                    state = apply_packing_result_to_reference_state(
                        reference_state=reference_state,
                        packing_result=packing_result,
                        envelope_geom=engine.envelope,
                        state_id=f"{state_id_prefix}_{random_seed}",
                        parent_id=str(reference_state.state_id),
                    )
                    if seed_metadata:
                        state.metadata = _merge_metadata(state.metadata, seed_metadata)
            except ValueError:
                continue

            if recenter_fn is not None:
                state = recenter_fn(state)

            fingerprint = _state_position_fingerprint(state)
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            seeds.append(state)

            if len(seeds) >= requested:
                break

        return seeds
