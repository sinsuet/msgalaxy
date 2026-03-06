"""
Compile ModelingIntent into executable pymoo problem specs (Phase B/C bridge).
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.protocol import DesignState

from .metric_registry import (
    MANDATORY_HARD_CONSTRAINT_GROUPS_DEFAULT,
    detect_covered_groups,
    get_metric_status,
    metric_covers_group,
    normalize_metric_key,
    parse_mandatory_groups,
)
from .protocol import ModelingConstraint, ModelingIntent, ModelingVariable
from .pymoo_integration.specs import (
    ConstraintSpec,
    ObjectiveSpec,
    PymooProblemSpec,
    SemanticZone,
    VariableSpec,
)


@dataclass
class CompileReport:
    parsed_variables: int = 0
    dropped_variables: List[str] = field(default_factory=list)
    parsed_objectives: int = 0
    parsed_constraints: int = 0
    dropped_constraints: List[str] = field(default_factory=list)
    injected_constraints: List[str] = field(default_factory=list)
    unsupported_metrics: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parsed_variables": self.parsed_variables,
            "dropped_variables": list(self.dropped_variables),
            "parsed_objectives": self.parsed_objectives,
            "parsed_constraints": self.parsed_constraints,
            "dropped_constraints": list(self.dropped_constraints),
            "injected_constraints": list(self.injected_constraints),
            "unsupported_metrics": list(self.unsupported_metrics),
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }


def formulate_modeling_intent(intent: ModelingIntent) -> Dict[str, Any]:
    """
    Phase B: symbolic formulation report with normalized g(x)<=0 expressions.
    """
    constraints = []
    for idx, cons in enumerate(intent.hard_constraints, start=1):
        constraints.append({
            "name": cons.name,
            "metric_key": cons.metric_key,
            "original_relation": cons.relation,
            "target_value": cons.target_value,
            "original_expression": cons.expression,
            "original_latex": cons.latex,
            "normalized_g_leq_0": _normalized_constraint_text(cons),
            "normalized_latex": _normalized_constraint_latex(cons, idx),
            "physical_meaning": cons.physical_meaning,
        })

    objectives = []
    for idx, obj in enumerate(intent.objectives, start=1):
        sign = "-" if obj.direction == "maximize" else ""
        objectives.append({
            "name": obj.name,
            "metric_key": obj.metric_key,
            "direction": obj.direction,
            "weight": obj.weight,
            "normalized_text": f"f_{idx} = {sign}{obj.weight} * {obj.metric_key}",
        })

    return {
        "intent_id": intent.intent_id,
        "problem_type": intent.problem_type,
        "normalized_hard_constraints": constraints,
        "normalized_objectives": objectives,
    }


def compile_intent_to_problem_spec(
    intent: ModelingIntent,
    base_state: DesignState,
    runtime_constraints: Dict[str, float],
    thermal_evaluator=None,
    enable_semantic_zones: bool = True,
    mandatory_hard_constraint_groups: Optional[List[str] | Tuple[str, ...] | str] = None,
    inject_mandatory_constraints: bool = True,
    metric_registry_mode: str = "warn",
) -> Tuple[PymooProblemSpec, CompileReport]:
    """
    Convert ModelingIntent into PymooProblemSpec.

    Parsing strategy:
    - Variables are accepted only if component + axis are resolvable.
    - If no valid variables remain, fallback to default full (x,y,z) variables.
    """
    report = CompileReport()
    variable_specs: List[VariableSpec] = []

    component_order = [comp.id for comp in base_state.components]
    component_ids = set(component_order)
    component_aliases = _build_component_aliases(base_state.components)
    for var in intent.variables:
        parsed = _parse_variable(
            var,
            component_ids=component_ids,
            component_aliases=component_aliases,
        )
        if parsed is None:
            report.dropped_variables.append(var.name)
            continue
        variable_specs.append(parsed)
        report.parsed_variables += 1

    if not variable_specs:
        report.warnings.append(
            "No valid variable mapping found in ModelingIntent; fallback to default full component xyz variables."
        )

    registry_mode = _normalize_registry_mode(metric_registry_mode)
    mandatory_groups = parse_mandatory_groups(
        mandatory_hard_constraint_groups
        if mandatory_hard_constraint_groups is not None
        else MANDATORY_HARD_CONSTRAINT_GROUPS_DEFAULT
    )

    objective_specs: List[ObjectiveSpec] = []
    for obj in intent.objectives:
        normalized_metric = _normalize_metric_key(obj.metric_key)
        metric_status = get_metric_status(normalized_metric)
        if not bool(metric_status.get("is_known", False)) or not bool(metric_status.get("is_implemented", False)):
            msg = (
                f"objective[{obj.name}] metric 不可执行: "
                f"{obj.metric_key} -> {normalized_metric}"
            )
            if registry_mode == "strict":
                raise ValueError(msg)
            report.warnings.append(msg)
            report.unsupported_metrics.append(str(obj.metric_key))
        objective_specs.append(
            ObjectiveSpec(
                name=obj.name,
                metric_key=normalized_metric,
                sense=obj.direction,
                weight=max(float(obj.weight), 1e-9),
            )
        )
        report.parsed_objectives += 1

    if not objective_specs:
        report.warnings.append("No objectives parsed from intent; fallback objective should be injected upstream.")

    constraint_specs: List[ConstraintSpec] = []
    for cons in intent.hard_constraints:
        normalized_metric = _normalize_metric_key(cons.metric_key)
        metric_status = get_metric_status(normalized_metric)
        if not bool(metric_status.get("is_known", False)) or not bool(metric_status.get("is_implemented", False)):
            msg = (
                f"constraint[{cons.name}] metric 不可执行: "
                f"{cons.metric_key} -> {normalized_metric}"
            )
            if registry_mode == "strict":
                raise ValueError(msg)
            report.warnings.append(msg)
            report.unsupported_metrics.append(str(cons.metric_key))
            report.dropped_constraints.append(str(cons.name))
            continue
        constraint_specs.append(
            ConstraintSpec(
                name=cons.name,
                metric_key=normalized_metric,
                relation=cons.relation,
                target_value=float(cons.target_value),
                eq_tolerance=1e-3 if cons.relation == "==" else 1e-6,
            )
        )
        report.parsed_constraints += 1

    if inject_mandatory_constraints:
        injected = _inject_mandatory_constraint_specs(
            existing=constraint_specs,
            mandatory_groups=mandatory_groups,
        )
        if injected:
            constraint_specs.extend(injected)
            report.injected_constraints.extend(spec.name for spec in injected)
            report.notes.append(
                "mandatory_constraints_injected="
                + ",".join(spec.name for spec in injected)
            )

    semantic_zones: List[SemanticZone] = []
    if enable_semantic_zones:
        semantic_zones = _extract_semantic_zones(
            assumptions=intent.assumptions,
            base_state=base_state,
        )
        if semantic_zones:
            report.notes.append(f"semantic_zones_enabled={len(semantic_zones)}")
    else:
        report.notes.append("semantic_zones_disabled")

    spec = PymooProblemSpec(
        base_state=base_state.model_copy(deep=True),
        runtime_constraints=dict(runtime_constraints),
        variable_specs=variable_specs,
        objective_specs=objective_specs,
        constraint_specs=constraint_specs,
        semantic_zones=semantic_zones,
        thermal_evaluator=thermal_evaluator,
        tags={
            "intent_id": intent.intent_id,
            "problem_type": intent.problem_type,
            "assumptions": list(intent.assumptions),
        },
    )
    return spec, report


def _parse_variable(
    var: ModelingVariable,
    component_ids: set[str],
    component_aliases: Dict[str, str],
) -> Optional[VariableSpec]:
    variable_type = var.variable_type
    if variable_type not in {"continuous", "integer", "binary"}:
        return None

    component_id = _resolve_component_id(
        raw_component_id=(var.component_id or ""),
        variable_name=var.name,
        component_ids=component_ids,
        component_aliases=component_aliases,
    )
    if not component_id or component_id not in component_ids:
        return None

    axis = _infer_axis(var)
    if axis is None:
        return None

    if var.lower_bound is None or var.upper_bound is None:
        return None

    return VariableSpec(
        name=var.name,
        component_id=component_id,
        axis=axis,
        variable_type=variable_type,
        lower_bound=float(var.lower_bound),
        upper_bound=float(var.upper_bound),
    )


def _build_component_aliases(components: List[Any]) -> Dict[str, str]:
    alias_map: Dict[str, str] = {}
    family_counter: Dict[str, int] = {}

    for idx, component in enumerate(components, start=1):
        component_id = str(getattr(component, "id", "") or "")
        if not component_id:
            continue

        base_tokens = {
            _normalize_component_token(component_id),
            _normalize_component_token(f"component_{idx}"),
            _normalize_component_token(f"component_{idx:02d}"),
            _normalize_component_token(f"comp_{idx}"),
            _normalize_component_token(f"comp_{idx:02d}"),
            _normalize_component_token(f"component{idx}"),
            _normalize_component_token(f"component{idx:02d}"),
            _normalize_component_token(f"comp{idx}"),
            _normalize_component_token(f"comp{idx:02d}"),
        }

        raw_tokens = _tokenize_component_text(component_id)
        category = str(getattr(component, "category", "") or "")
        raw_tokens.extend(_tokenize_component_text(category))
        raw_tokens.extend(_semantic_alias_seeds(component_id, category))

        deduped_raw: List[str] = []
        seen_raw: set[str] = set()
        for token in raw_tokens:
            normalized = _normalize_component_token(token)
            if not normalized or normalized in seen_raw:
                continue
            seen_raw.add(normalized)
            deduped_raw.append(normalized)

        for token in base_tokens:
            if token and token not in alias_map:
                alias_map[token] = component_id

        for seed in deduped_raw:
            family_idx = int(family_counter.get(seed, 0) + 1)
            family_counter[seed] = family_idx
            _register_alias_seed(alias_map, seed, component_id, family_idx)
    return alias_map


def _tokenize_component_text(text: str) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = [item for item in re.split(r"[\s_\-]+", raw) if item]
    if not parts:
        return []

    tokens: List[str] = []
    joined = _normalize_component_token(raw)
    if joined:
        tokens.append(joined)

    if len(parts) >= 2 and (parts[-1].isdigit() or len(parts[-1]) == 1):
        stem = _normalize_component_token("".join(parts[:-1]))
        if stem:
            tokens.append(stem)

    first = _normalize_component_token(parts[0])
    if first:
        tokens.append(first)

    if len(parts) >= 2:
        first_two = _normalize_component_token("".join(parts[:2]))
        if first_two:
            tokens.append(first_two)
    return tokens


def _semantic_alias_seeds(component_id: str, category: str) -> List[str]:
    cid = _normalize_component_token(component_id)
    cat = _normalize_component_token(category)
    seeds: List[str] = []

    def _append_all(values: List[str]) -> None:
        for item in values:
            token = _normalize_component_token(item)
            if token:
                seeds.append(token)

    if "battery" in cid:
        _append_all(["battery", "batt"])
    if "payload" in cid:
        _append_all(["payload", "instrument"])
    if "rw" in cid or "wheel" in cid:
        _append_all(["rw", "adcs", "reactionwheel"])
    if "comm" in cid or "tx" in cid or "rx" in cid:
        _append_all(["comm", "comms", "communication", "transceiver"])
    if "bus" in cid or "obc" in cid:
        _append_all(["bus", "powerbus", "obc", "avionics"])

    if cat:
        _append_all([cat, f"{cat}s"])

    return seeds


def _register_alias_seed(
    alias_map: Dict[str, str],
    seed: str,
    component_id: str,
    family_idx: int,
) -> None:
    variants = [
        seed,
        f"{seed}_{family_idx}",
        f"{seed}_{family_idx:02d}",
        f"{seed}{family_idx}",
        f"{seed}{family_idx:02d}",
    ]
    if seed.endswith("s") and len(seed) > 2:
        singular = seed[:-1]
        variants.extend(
            [
                singular,
                f"{singular}_{family_idx}",
                f"{singular}_{family_idx:02d}",
                f"{singular}{family_idx}",
                f"{singular}{family_idx:02d}",
            ]
        )
    else:
        plural = f"{seed}s"
        variants.extend(
            [
                plural,
                f"{plural}_{family_idx}",
                f"{plural}_{family_idx:02d}",
                f"{plural}{family_idx}",
                f"{plural}{family_idx:02d}",
            ]
        )

    for token in variants:
        normalized = _normalize_component_token(token)
        if normalized and normalized not in alias_map:
            alias_map[normalized] = component_id


def _normalize_component_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").strip().lower())


def _candidate_component_keys(text: str) -> List[str]:
    if not text:
        return []
    raw = str(text).strip()
    if not raw:
        return []

    keys: List[str] = []
    normalized = _normalize_component_token(raw)
    if normalized:
        keys.append(normalized)
        stem = re.sub(r"\d+$", "", normalized)
        if stem and stem != normalized:
            keys.append(stem)
        if normalized.endswith("s") and len(normalized) > 2:
            keys.append(normalized[:-1])

    parts = [item for item in re.split(r"[\s_\-]+", raw) if item]
    if len(parts) >= 2 and parts[-1].lower() in {"x", "y", "z"}:
        key = _normalize_component_token("".join(parts[:-1]))
        if key:
            keys.append(key)
            stem = re.sub(r"\d+$", "", key)
            if stem and stem != key:
                keys.append(stem)
            if key.endswith("s") and len(key) > 2:
                keys.append(key[:-1])

    suffix_match = re.match(r"(.+?)(?:[_\-\s]?)(x|y|z)$", raw, flags=re.IGNORECASE)
    if suffix_match:
        key = _normalize_component_token(suffix_match.group(1))
        if key:
            keys.append(key)

    deduped: List[str] = []
    seen: set[str] = set()
    for key in keys:
        if key not in seen:
            deduped.append(key)
            seen.add(key)
    return deduped


def _resolve_component_id(
    raw_component_id: str,
    variable_name: str,
    component_ids: set[str],
    component_aliases: Dict[str, str],
) -> str:
    exact = str(raw_component_id or "").strip()
    if exact:
        if exact in component_ids:
            return exact
        lower_exact = exact.lower()
        for component_id in component_ids:
            if component_id.lower() == lower_exact:
                return component_id

    for candidate in (raw_component_id, variable_name):
        for key in _candidate_component_keys(candidate):
            mapped = component_aliases.get(key)
            if mapped:
                return mapped
        fuzzy_mapped = _fuzzy_resolve_component_id(
            keys=_candidate_component_keys(candidate),
            component_aliases=component_aliases,
        )
        if fuzzy_mapped:
            return fuzzy_mapped

    return _infer_component_id_from_name(variable_name, component_ids)


def _fuzzy_resolve_component_id(
    *,
    keys: List[str],
    component_aliases: Dict[str, str],
) -> str:
    if not keys or not component_aliases:
        return ""

    best_component = ""
    best_score = 0.0
    alias_keys = list(component_aliases.keys())

    for key in keys:
        if not key:
            continue
        key_stem = re.sub(r"\d+$", "", key)
        key_forms = [key]
        if key_stem and key_stem != key:
            key_forms.append(key_stem)
        if key.endswith("s") and len(key) > 2:
            key_forms.append(key[:-1])

        for form in key_forms:
            if not form:
                continue
            for alias in alias_keys:
                score = difflib.SequenceMatcher(None, form, alias).ratio()
                if score > best_score:
                    best_score = score
                    best_component = component_aliases[alias]

    threshold = 0.86
    if best_score >= threshold:
        return best_component
    return ""


def _infer_component_id_from_name(name: str, component_ids: set[str]) -> str:
    lowered = str(name).lower()
    lowered_norm = _normalize_component_token(lowered)
    for component_id in component_ids:
        candidate = component_id.lower()
        if candidate in lowered:
            return component_id
        candidate_norm = _normalize_component_token(candidate)
        if candidate_norm and candidate_norm in lowered_norm:
            return component_id
    return ""


def _infer_axis(var: ModelingVariable) -> Optional[str]:
    name = var.name.lower()
    description = (var.description or "").lower()
    for axis in ("x", "y", "z"):
        tokens = {f"_{axis}", f" {axis}", f"{axis}-", f"-{axis}", f"{axis}axis", f"{axis}_position"}
        if any(token in name for token in tokens):
            return axis
        if f" {axis} " in f" {description} ":
            return axis
    return None


def _normalize_metric_key(metric_key: str) -> str:
    return normalize_metric_key(metric_key)


def _normalize_registry_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in {"off", "warn", "strict"}:
        return normalized
    return "warn"


def _inject_mandatory_constraint_specs(
    *,
    existing: List[ConstraintSpec],
    mandatory_groups: Tuple[str, ...],
) -> List[ConstraintSpec]:
    existing_metrics = [spec.metric_key for spec in existing]
    covered = detect_covered_groups(existing_metrics, mandatory_groups=mandatory_groups)
    injected: List[ConstraintSpec] = []

    for group in mandatory_groups:
        if group in covered:
            continue
        fallback = _mandatory_constraint_for_group(group)
        if fallback is None:
            continue
        # avoid duplicate if equivalent metric already exists after alias normalization
        already_exists = any(
            metric_covers_group(spec.metric_key, group)
            for spec in [*existing, *injected]
        )
        if already_exists:
            continue
        injected.append(fallback)

    return injected


def _mandatory_constraint_for_group(group: str) -> Optional[ConstraintSpec]:
    key = str(group or "").strip().lower()
    if key == "collision":
        return ConstraintSpec(
            name="collision",
            metric_key="collision_violation",
            relation="<=",
            target_value=0.0,
        )
    if key == "clearance":
        return ConstraintSpec(
            name="clearance",
            metric_key="clearance_violation",
            relation="<=",
            target_value=0.0,
        )
    if key == "boundary":
        return ConstraintSpec(
            name="boundary",
            metric_key="boundary_violation",
            relation="<=",
            target_value=0.0,
        )
    if key == "thermal":
        return ConstraintSpec(
            name="thermal",
            metric_key="thermal_violation",
            relation="<=",
            target_value=0.0,
        )
    if key == "cg_limit":
        return ConstraintSpec(
            name="cg_limit",
            metric_key="cg_violation",
            relation="<=",
            target_value=0.0,
        )
    if key == "struct_safety":
        return ConstraintSpec(
            name="struct_safety",
            metric_key="safety_factor_violation",
            relation="<=",
            target_value=0.0,
        )
    if key == "struct_modal":
        return ConstraintSpec(
            name="struct_modal",
            metric_key="modal_freq_violation",
            relation="<=",
            target_value=0.0,
        )
    if key == "power_vdrop":
        return ConstraintSpec(
            name="power_vdrop",
            metric_key="voltage_drop_violation",
            relation="<=",
            target_value=0.0,
        )
    if key == "power_margin":
        return ConstraintSpec(
            name="power_margin",
            metric_key="power_margin_violation",
            relation="<=",
            target_value=0.0,
        )
    if key == "power_peak":
        return ConstraintSpec(
            name="power_peak",
            metric_key="peak_power_violation",
            relation="<=",
            target_value=0.0,
        )
    if key == "mission_keepout":
        return ConstraintSpec(
            name="mission_keepout",
            metric_key="mission_keepout_violation",
            relation="<=",
            target_value=0.0,
        )
    return None


def _normalized_constraint_text(cons: ModelingConstraint) -> str:
    metric = cons.metric_key
    target = cons.target_value
    if cons.relation == "<=":
        return f"g(x) = {metric} - {target} <= 0"
    if cons.relation == ">=":
        return f"g(x) = {target} - {metric} <= 0"
    return f"g(x) = |{metric} - {target}| - eps <= 0"


def _normalized_constraint_latex(cons: ModelingConstraint, idx: int) -> str:
    metric = cons.metric_key
    target = cons.target_value
    if cons.relation == "<=":
        return f"g_{{{idx}}}(x)={metric}-{target}\\le 0"
    if cons.relation == ">=":
        return f"g_{{{idx}}}(x)={target}-{metric}\\le 0"
    return f"g_{{{idx}}}(x)=\\left|{metric}-{target}\\right|-\\epsilon\\le 0"


def _extract_semantic_zones(
    assumptions: List[str],
    base_state: Optional[DesignState] = None,
) -> List[SemanticZone]:
    """
    Parse semantic zoning from assumptions, then fallback to heuristic zoning.

    Supported explicit formats:
    1) Legacy text format:
      zone:<id>:x1,y1,z1:x2,y2,z2:compA,compB
    2) JSON object string:
      {
        "zone_id": "thermal_band",
        "min_corner": [x1, y1, z1],
        "max_corner": [x2, y2, z2],
        "component_ids": ["compA", "compB"]
      }

    If assumptions do not provide explicit zones, a conservative heuristic
    zoning is inferred for high-power / thermal-critical components.
    """
    zones: List[SemanticZone] = []

    for item in assumptions:
        text = (item or "").strip()
        if not text:
            continue

        if text.startswith("zone:"):
            try:
                _, zone_id, min_txt, max_txt, comps_txt = text.split(":", 4)
                min_corner = tuple(float(v) for v in min_txt.split(","))
                max_corner = tuple(float(v) for v in max_txt.split(","))
                comp_ids = tuple(c.strip() for c in comps_txt.split(",") if c.strip())
                if len(min_corner) == 3 and len(max_corner) == 3:
                    zones.append(
                        SemanticZone(
                            zone_id=zone_id,
                            min_corner=min_corner,  # type: ignore[arg-type]
                            max_corner=max_corner,  # type: ignore[arg-type]
                            component_ids=comp_ids,
                        )
                    )
            except Exception:
                continue
            continue

        if text.startswith("{") and text.endswith("}"):
            try:
                payload = json.loads(text)
                if not isinstance(payload, dict):
                    continue
                zone_id = str(payload.get("zone_id") or payload.get("id") or "")
                min_corner_raw = payload.get("min_corner")
                max_corner_raw = payload.get("max_corner")
                comp_ids_raw = payload.get("component_ids", [])
                if not zone_id:
                    continue
                if not isinstance(min_corner_raw, list) or not isinstance(max_corner_raw, list):
                    continue
                if len(min_corner_raw) != 3 or len(max_corner_raw) != 3:
                    continue
                min_corner = tuple(float(v) for v in min_corner_raw)
                max_corner = tuple(float(v) for v in max_corner_raw)
                comp_ids = tuple(str(v).strip() for v in comp_ids_raw if str(v).strip())
                zones.append(
                    SemanticZone(
                        zone_id=zone_id,
                        min_corner=min_corner,  # type: ignore[arg-type]
                        max_corner=max_corner,  # type: ignore[arg-type]
                        component_ids=comp_ids,
                    )
                )
            except Exception:
                continue

    if zones:
        return zones

    if base_state is None:
        return []

    return _infer_semantic_zones_from_state(base_state)


def _infer_semantic_zones_from_state(base_state: DesignState) -> List[SemanticZone]:
    """
    Infer conservative thermal zoning from current layout.

    Heuristic:
    - Components with high thermal load are constrained to a Y-side cooling band
      near their current side (+Y / -Y), preserving current hemispheric intent.
    """
    zones: List[SemanticZone] = []
    env = base_state.envelope
    size = (
        float(env.outer_size.x),
        float(env.outer_size.y),
        float(env.outer_size.z),
    )
    if env.origin == "center":
        env_min = (-size[0] / 2.0, -size[1] / 2.0, -size[2] / 2.0)
        env_max = (size[0] / 2.0, size[1] / 2.0, size[2] / 2.0)
    else:
        env_min = (0.0, 0.0, 0.0)
        env_max = size

    for comp in base_state.components:
        category = str(getattr(comp, "category", "") or "").lower()
        power = float(getattr(comp, "power", 0.0) or 0.0)
        is_thermal_critical = (
            power >= 20.0 or
            "power" in category or
            "battery" in category
        )
        if not is_thermal_critical:
            continue

        half = (
            float(comp.dimensions.x) / 2.0,
            float(comp.dimensions.y) / 2.0,
            float(comp.dimensions.z) / 2.0,
        )
        lb = (
            env_min[0] + half[0],
            env_min[1] + half[1],
            env_min[2] + half[2],
        )
        ub = (
            env_max[0] - half[0],
            env_max[1] - half[1],
            env_max[2] - half[2],
        )
        if lb[0] > ub[0] or lb[1] > ub[1] or lb[2] > ub[2]:
            continue

        y_span = max(ub[1] - lb[1], 0.0)
        band_width = max(y_span * 0.35, 10.0)
        if float(comp.position.y) >= 0.0:
            y_min = max(lb[1], ub[1] - band_width)
            y_max = ub[1]
        else:
            y_min = lb[1]
            y_max = min(ub[1], lb[1] + band_width)

        if y_min > y_max:
            y_min, y_max = lb[1], ub[1]

        zones.append(
            SemanticZone(
                zone_id=f"auto_thermal_band_{comp.id}",
                min_corner=(lb[0], y_min, lb[2]),
                max_corner=(ub[0], y_max, ub[2]),
                component_ids=(comp.id,),
            )
        )

    return zones
