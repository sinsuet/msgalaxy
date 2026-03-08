"""
Runtime feature fingerprint for experiment observability and summary rendering.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from core.path_policy import serialize_run_path


RUNTIME_FEATURE_FINGERPRINT_REL_PATH = Path("events") / "runtime_feature_fingerprint.json"
RUNTIME_FEATURE_FINGERPRINT_SCHEMA_VERSION = 1


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_to_jsonable(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _to_jsonable(value.model_dump())
        except Exception:
            pass
    return str(value)


def _bool_text(value: Any) -> str:
    return "on" if bool(value) else "off"


def _search_space_to_genome(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    if "operator" in token:
        return "operator_program_vector"
    if "hybrid" in token:
        return "hybrid_vector"
    if "coordinate" in token:
        return "coordinate_vector"
    return token


def _resolve_intent_mode(summary: Dict[str, Any]) -> Dict[str, str]:
    source = str(summary.get("modeling_intent_source", "") or "").strip().lower()
    api_succeeded = bool(summary.get("modeling_intent_api_call_succeeded", False))
    used_fallback = bool(summary.get("modeling_intent_used_fallback", False))
    called = bool(summary.get("modeling_intent_called", False))

    if "deterministic" in source:
        intent_mode = "deterministic_intent"
    elif api_succeeded or source in {"api", "llm", "llm_api"}:
        intent_mode = "llm_intent"
    elif called:
        intent_mode = "llm_intent_attempted"
    elif used_fallback:
        intent_mode = "fallback_intent"
    else:
        intent_mode = source or "unknown"
    return {
        "intent_mode": intent_mode,
        "intent_effective_source": source or "unknown",
    }


def _parse_jsonish_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value or {})
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _config_value(config: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in config:
            return config.get(key)
    return default


def _config_text(config: Dict[str, Any], *keys: str, default: str = "") -> str:
    value = _config_value(config, *keys, default=default)
    return str(value or "").strip()


def _config_bool(config: Dict[str, Any], *keys: str, default: bool = False) -> bool:
    value = _config_value(config, *keys, default=default)
    return bool(value)


def _summary_or_config_text(
    summary: Dict[str, Any],
    summary_key: str,
    config: Dict[str, Any],
    *config_keys: str,
    default: str = "",
) -> str:
    if summary_key in summary and str(summary.get(summary_key, "") or "").strip():
        return str(summary.get(summary_key, "") or "").strip()
    return _config_text(config, *config_keys, default=default)


def _summary_or_config_bool(
    summary: Dict[str, Any],
    summary_key: str,
    config: Dict[str, Any],
    *config_keys: str,
    default: bool = False,
) -> bool:
    if summary_key in summary:
        return bool(summary.get(summary_key))
    return _config_bool(config, *config_keys, default=default)


def _resolve_gate_mode(
    config: Dict[str, Any],
    *mode_keys: str,
    strict_keys: tuple[str, ...] = (),
) -> str:
    mode = _config_text(config, *mode_keys, default="")
    if mode:
        return mode
    for key in strict_keys:
        if _config_bool(config, key, default=False):
            return "strict"
    return ""


def _build_requested_baseline(
    summary: Dict[str, Any],
    runtime_config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    config = dict(runtime_config or {})
    opt_cfg = dict(config.get("optimization", {}) or {})
    sim_cfg = dict(config.get("simulation", {}) or {})
    run_mode = str(summary.get("run_mode", "") or "").strip()
    intent_info = _resolve_intent_mode(summary)
    requested_search_space = _config_text(
        opt_cfg,
        "mass_search_space",
        "search_space",
        default=str(summary.get("search_space", "") or summary.get("search_space_mode", "") or ""),
    )
    requested_thermal_mode = _config_text(
        opt_cfg,
        "mass_thermal_evaluator_mode",
        "thermal_evaluator_mode",
        default=str(summary.get("thermal_evaluator_mode", "") or ""),
    )
    requested_backend = _config_text(
        sim_cfg,
        "backend",
        "simulation_backend",
        default=str(summary.get("simulation_backend", "") or ""),
    )

    return {
        "entry_stack": _config_text(opt_cfg, "mode", "entry_stack", default=run_mode),
        "run_mode": run_mode,
        "execution_mode": str(summary.get("execution_mode", "") or ""),
        "delegated_execution_mode": str(summary.get("delegated_execution_mode", "") or ""),
        "intent_mode": intent_info["intent_mode"],
        "requested_search_space_mode": requested_search_space,
        "requested_genome_representation": _search_space_to_genome(requested_search_space),
        "requested_simulation_backend": requested_backend,
        "requested_thermal_evaluator_mode": requested_thermal_mode,
        "mcts_enabled": _config_bool(opt_cfg, "mass_enable_mcts", "enable_mcts", default=False),
        "meta_policy_enabled": _config_bool(
            opt_cfg,
            "mass_enable_meta_policy",
            "enable_meta_policy",
            default=False,
        ),
        "physics_audit_enabled": _config_bool(
            opt_cfg,
            "mass_enable_physics_audit",
            "enable_physics_audit",
            default=False,
        ),
        "operator_program_enabled": _config_bool(
            opt_cfg,
            "mass_enable_operator_program",
            "enable_operator_program",
            default=False,
        ),
        "seed_population_enabled": any(
            [
                _config_bool(opt_cfg, "mass_enable_seed_population", default=False),
                _config_bool(opt_cfg, "mass_enable_layout_seed_population", default=False),
                _config_bool(opt_cfg, "mass_enable_operator_seed_population", default=False),
                _config_bool(opt_cfg, "enable_seed_population", default=False),
            ]
        ),
        "source_gate_mode": _resolve_gate_mode(
            opt_cfg,
            "mass_source_gate_mode",
            "source_gate_mode",
            strict_keys=("strict_source_gate",),
        ),
        "source_gate_real_only": _config_bool(
            opt_cfg,
            "mass_physics_real_only",
            "source_gate_real_only",
            "strict_source_gate",
            default=False,
        ),
        "operator_family_gate_mode": _resolve_gate_mode(
            opt_cfg,
            "mass_operator_family_gate_mode",
            "operator_family_gate_mode",
            strict_keys=("strict_operator_family_gate",),
        ),
        "operator_realization_gate_mode": _resolve_gate_mode(
            opt_cfg,
            "mass_operator_realization_gate_mode",
            "operator_realization_gate_mode",
            strict_keys=("strict_operator_realization_gate",),
        ),
        "reflective_replan_enabled": _config_bool(
            opt_cfg,
            "vop_reflective_replan_enabled",
            "vop_enable_reflective_replan",
            default=False,
        ),
        "feedback_aware_fidelity_enabled": _config_bool(
            opt_cfg,
            "vop_feedback_aware_fidelity_enabled",
            "vop_feedback_aware_fidelity",
            default=False,
        ),
    }


def _build_effective_runtime(
    summary: Dict[str, Any],
    runtime_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = dict(runtime_config or {})
    opt_cfg = dict(config.get("optimization", {}) or {})
    sim_cfg = dict(config.get("simulation", {}) or {})
    decision = dict(summary.get("vop_decision_summary", {}) or {})
    delegated = dict(summary.get("vop_delegated_effect_summary", {}) or {})
    observed = _parse_jsonish_dict(delegated.get("observed_effects", {}))
    effective_fidelity = _parse_jsonish_dict(observed.get("effective_fidelity", {}))
    online_budget = _parse_jsonish_dict(
        effective_fidelity.get("online_comsol_attempt_budget", {})
    )
    intent_info = _resolve_intent_mode(summary)
    effective_search_space = str(
        observed.get("effective_search_space", "")
        or decision.get("search_space_override", "")
        or summary.get("vop_search_space_override", "")
        or summary.get("search_space", "")
        or summary.get("search_space_mode", "")
        or _config_text(opt_cfg, "mass_search_space", "search_space", default="")
        or ""
    ).strip()

    return {
        "intent_effective_source": intent_info["intent_effective_source"],
        "intent_api_attempted": bool(summary.get("modeling_intent_called", False)),
        "intent_api_succeeded": bool(summary.get("modeling_intent_api_call_succeeded", False)),
        "intent_used_fallback": bool(summary.get("modeling_intent_used_fallback", False)),
        "effective_search_space_mode": effective_search_space,
        "effective_genome_representation": _search_space_to_genome(effective_search_space),
        "effective_simulation_backend": _summary_or_config_text(
            summary,
            "simulation_backend",
            sim_cfg,
            "backend",
            "simulation_backend",
            default="",
        ),
        "effective_thermal_evaluator_mode": _summary_or_config_text(
            summary,
            "thermal_evaluator_mode",
            opt_cfg,
            "mass_thermal_evaluator_mode",
            "thermal_evaluator_mode",
            default=str(effective_fidelity.get("thermal_evaluator_mode", "") or ""),
        ),
        "effective_online_comsol_budget": online_budget.get(
            "configured_total_budget",
            summary.get("comsol_calls_to_first_feasible")
            or delegated.get("comsol_calls_to_first_feasible")
            or _config_value(opt_cfg, "online_comsol_eval_budget", default=None),
        ),
        "effective_physics_audit_top_k": effective_fidelity.get("physics_audit", {}).get(
            "requested_top_k",
            _config_value(opt_cfg, "physics_audit_top_k", "mass_physics_audit_top_k", default=""),
        ),
        "effective_first_feasible_eval": summary.get("first_feasible_eval")
        or delegated.get("first_feasible_eval"),
        "effective_comsol_calls_to_first_feasible": summary.get(
            "comsol_calls_to_first_feasible"
        )
        or delegated.get("comsol_calls_to_first_feasible"),
        "llm_effective_passed": bool(summary.get("llm_effective_passed", False)),
        "llm_effective_variable_mapping_passed": bool(
            summary.get("llm_effective_variable_mapping_passed", False)
        ),
        "llm_effective_metric_mapping_passed": bool(
            summary.get("llm_effective_metric_mapping_passed", False)
        ),
    }


def _build_gate_audit(
    summary: Dict[str, Any],
    runtime_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = dict(runtime_config or {})
    opt_cfg = dict(config.get("optimization", {}) or {})
    return {
        "source_gate": {
            "mode": _summary_or_config_text(
                summary,
                "source_gate_mode",
                opt_cfg,
                "mass_source_gate_mode",
                "source_gate_mode",
                default=_resolve_gate_mode(
                    opt_cfg,
                    "mass_source_gate_mode",
                    "source_gate_mode",
                    strict_keys=("strict_source_gate",),
                ),
            ),
            "passed": bool(summary.get("source_gate_passed", True)),
            "strict_blocked": bool(summary.get("source_gate_strict_blocked", False)),
            "real_only": _summary_or_config_bool(
                summary,
                "source_gate_real_only",
                opt_cfg,
                "mass_physics_real_only",
                "source_gate_real_only",
                "strict_source_gate",
                default=False,
            ),
            "require_structural_real": _summary_or_config_bool(
                summary,
                "source_gate_require_structural_real",
                opt_cfg,
                "mass_source_gate_require_structural_real",
                default=False,
            ),
            "require_power_real": _summary_or_config_bool(
                summary,
                "source_gate_require_power_real",
                opt_cfg,
                "mass_source_gate_require_power_real",
                default=False,
            ),
            "require_thermal_real": _summary_or_config_bool(
                summary,
                "source_gate_require_thermal_real",
                opt_cfg,
                "mass_source_gate_require_thermal_real",
                default=False,
            ),
            "require_mission_real": _summary_or_config_bool(
                summary,
                "source_gate_require_mission_real",
                opt_cfg,
                "mass_source_gate_require_mission_real",
                default=False,
            ),
            "missing_real_reasons": list(summary.get("source_gate_real_only_reasons", []) or []),
        },
        "operator_family_gate": {
            "mode": _summary_or_config_text(
                summary,
                "operator_family_gate_mode",
                opt_cfg,
                "mass_operator_family_gate_mode",
                "operator_family_gate_mode",
                default=_resolve_gate_mode(
                    opt_cfg,
                    "mass_operator_family_gate_mode",
                    "operator_family_gate_mode",
                    strict_keys=("strict_operator_family_gate",),
                ),
            ),
            "passed": bool(summary.get("operator_family_gate_passed", True)),
            "strict_blocked": bool(summary.get("operator_family_gate_strict_blocked", False)),
            "required_families": list(summary.get("operator_family_gate_required", []) or []),
            "covered_families": list(summary.get("operator_family_gate_covered", []) or []),
            "missing_families": list(summary.get("operator_family_gate_missing", []) or []),
        },
        "operator_realization_gate": {
            "mode": _summary_or_config_text(
                summary,
                "operator_realization_gate_mode",
                opt_cfg,
                "mass_operator_realization_gate_mode",
                "operator_realization_gate_mode",
                default=_resolve_gate_mode(
                    opt_cfg,
                    "mass_operator_realization_gate_mode",
                    "operator_realization_gate_mode",
                    strict_keys=("strict_operator_realization_gate",),
                ),
            ),
            "passed": bool(summary.get("operator_realization_gate_passed", True)),
            "strict_blocked": bool(
                summary.get("operator_realization_gate_strict_blocked", False)
            ),
            "missing_realized_families": list(
                summary.get("operator_realization_gate_missing", []) or []
            ),
            "non_real_actions_by_family": dict(
                summary.get("operator_realization_gate_non_real_actions_by_family", {}) or {}
            ),
        },
    }


def _build_vop_overlay(summary: Dict[str, Any]) -> Dict[str, Any]:
    decision = dict(summary.get("vop_decision_summary", {}) or {})
    delegated = dict(summary.get("vop_delegated_effect_summary", {}) or {})
    reflective = dict(summary.get("vop_reflective_replanning", {}) or {})
    return {
        "policy_round_count": int(summary.get("vop_round_count", 0) or 0),
        "primary_round_index": int(summary.get("vop_policy_primary_round_index", -1) or -1),
        "primary_round_key": str(summary.get("vop_policy_primary_round_key", "") or ""),
        "policy_id": str(decision.get("policy_id", "") or summary.get("vop_policy_id", "") or ""),
        "selected_operator_program_id": str(
            decision.get("selected_operator_program_id", "")
            or summary.get("vop_selected_operator_program_id", "")
            or ""
        ),
        "operator_actions": list(decision.get("operator_actions", []) or []),
        "search_space_override": str(
            decision.get("search_space_override", "")
            or summary.get("vop_search_space_override", "")
            or ""
        ),
        "runtime_overrides": _to_jsonable(
            decision.get("runtime_overrides", {}) or summary.get("vop_runtime_overrides", {})
        ),
        "fidelity_plan": _to_jsonable(
            decision.get("fidelity_plan", {}) or summary.get("vop_fidelity_overrides", {})
        ),
        "reflective_replan_triggered": bool(reflective.get("triggered", False)),
        "reflective_replan_reason": str(
            reflective.get("trigger_reason", "") or reflective.get("skipped_reason", "") or ""
        ),
        "feedback_aware_fidelity_reason": str(
            summary.get("vop_feedback_aware_fidelity_reason", "") or ""
        ),
        "delegated_effectiveness_verdict": str(
            delegated.get("effectiveness_verdict", "") or ""
        ),
    }


def build_runtime_feature_fingerprint(
    run_dir: str,
    *,
    runtime_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    run_path = Path(run_dir).resolve()
    summary = _read_json(run_path / "summary.json")
    manifest = _read_json(run_path / "events" / "run_manifest.json")
    run_mode = str(summary.get("run_mode", "") or manifest.get("run_mode", "") or "").strip()
    fingerprint = {
        "schema_version": int(RUNTIME_FEATURE_FINGERPRINT_SCHEMA_VERSION),
        "run_id": str(summary.get("run_id", "") or manifest.get("run_id", "") or ""),
        "run_mode": run_mode,
        "execution_mode": str(
            summary.get("execution_mode", "") or manifest.get("execution_mode", "") or ""
        ),
        "delegated_execution_mode": str(
            summary.get("delegated_execution_mode", "")
            or manifest.get("delegated_execution_mode", "")
            or ""
        ),
        "requested_baseline": _build_requested_baseline(summary, runtime_config),
        "effective_runtime": _build_effective_runtime(summary, runtime_config),
        "gate_audit": _build_gate_audit(summary, runtime_config),
        "vop_controller_overlay": _build_vop_overlay(summary),
        "source_artifacts": [
            "summary.json",
            "events/run_manifest.json",
            "tables/release_audit.csv",
            "tables/vop_rounds.csv",
        ],
    }
    return _to_jsonable(fingerprint)


def load_runtime_feature_fingerprint(run_dir: str) -> Dict[str, Any]:
    return _read_json(Path(run_dir) / RUNTIME_FEATURE_FINGERPRINT_REL_PATH)


def write_runtime_feature_fingerprint(run_dir: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    run_path = Path(run_dir).resolve()
    path = run_path / RUNTIME_FEATURE_FINGERPRINT_REL_PATH
    _write_json(path, payload)
    return dict(payload or {})


def persist_runtime_feature_fingerprint(
    run_dir: str,
    *,
    runtime_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = build_runtime_feature_fingerprint(run_dir, runtime_config=runtime_config)
    write_runtime_feature_fingerprint(run_dir, payload)
    return payload


def fingerprint_rel_path(run_dir: str) -> str:
    run_path = Path(run_dir).resolve()
    return serialize_run_path(str(run_path), str(run_path / RUNTIME_FEATURE_FINGERPRINT_REL_PATH))


def fingerprint_display_rows(payload: Dict[str, Any]) -> Dict[str, Any]:
    requested = dict(payload.get("requested_baseline", {}) or {})
    effective = dict(payload.get("effective_runtime", {}) or {})
    gates = dict(payload.get("gate_audit", {}) or {})
    vop = dict(payload.get("vop_controller_overlay", {}) or {})

    return {
        "baseline_table": [
            {
                "Feature": "入口栈 / run identity",
                "Requested": requested.get("entry_stack", ""),
                "Effective": f"{requested.get('run_mode', '')} -> {requested.get('execution_mode', '')}",
                "Notes": requested.get("delegated_execution_mode", "") or "n/a",
            },
            {
                "Feature": "Modeling intent",
                "Requested": requested.get("intent_mode", ""),
                "Effective": effective.get("intent_effective_source", ""),
                "Notes": (
                    f"api_attempted={_bool_text(effective.get('intent_api_attempted'))}, "
                    f"api_succeeded={_bool_text(effective.get('intent_api_succeeded'))}, "
                    f"fallback={_bool_text(effective.get('intent_used_fallback'))}"
                ),
            },
            {
                "Feature": "Search space / genome",
                "Requested": requested.get("requested_search_space_mode", ""),
                "Effective": effective.get("effective_search_space_mode", ""),
                "Notes": (
                    f"requested={requested.get('requested_genome_representation', '')}, "
                    f"effective={effective.get('effective_genome_representation', '')}"
                ),
            },
            {
                "Feature": "Physics path",
                "Requested": (
                    f"backend={requested.get('requested_simulation_backend', '')}, "
                    f"thermal={requested.get('requested_thermal_evaluator_mode', '')}"
                ),
                "Effective": (
                    f"backend={effective.get('effective_simulation_backend', '')}, "
                    f"thermal={effective.get('effective_thermal_evaluator_mode', '')}"
                ),
                "Notes": (
                    f"online_budget={effective.get('effective_online_comsol_budget', '')}, "
                    f"audit_top_k={effective.get('effective_physics_audit_top_k', '')}"
                ),
            },
            {
                "Feature": "Strategy layers",
                "Requested": (
                    f"mcts={_bool_text(requested.get('mcts_enabled'))}, "
                    f"meta_policy={_bool_text(requested.get('meta_policy_enabled'))}, "
                    f"physics_audit={_bool_text(requested.get('physics_audit_enabled'))}"
                ),
                "Effective": (
                    f"llm_effective={_bool_text(effective.get('llm_effective_passed'))}, "
                    f"var_map={_bool_text(effective.get('llm_effective_variable_mapping_passed'))}, "
                    f"metric_map={_bool_text(effective.get('llm_effective_metric_mapping_passed'))}"
                ),
                "Notes": (
                    f"operator_program={_bool_text(requested.get('operator_program_enabled'))}, "
                    f"seed_population={_bool_text(requested.get('seed_population_enabled'))}"
                ),
            },
            {
                "Feature": "Strict gates",
                "Requested": (
                    f"source={requested.get('source_gate_mode', '') or 'off'}, "
                    f"family={requested.get('operator_family_gate_mode', '') or 'off'}, "
                    f"realization={requested.get('operator_realization_gate_mode', '') or 'off'}"
                ),
                "Effective": (
                    f"source={_bool_text(dict(gates.get('source_gate', {}) or {}).get('passed'))}, "
                    f"family={_bool_text(dict(gates.get('operator_family_gate', {}) or {}).get('passed'))}, "
                    f"realization={_bool_text(dict(gates.get('operator_realization_gate', {}) or {}).get('passed'))}"
                ),
                "Notes": f"real_only={_bool_text(requested.get('source_gate_real_only'))}",
            },
            {
                "Feature": "Controller extras",
                "Requested": (
                    f"reflective_replan={_bool_text(requested.get('reflective_replan_enabled'))}, "
                    f"feedback_fidelity={_bool_text(requested.get('feedback_aware_fidelity_enabled'))}"
                ),
                "Effective": (
                    f"rounds={vop.get('policy_round_count', '')}, "
                    f"primary_round={('V' + str(vop.get('primary_round_index'))) if str(vop.get('primary_round_index', '')).strip() not in {'', '-1'} else 'n/a'}"
                ),
                "Notes": f"verdict={vop.get('delegated_effectiveness_verdict', '') or 'n/a'}",
            },
        ],
        "gate_table": [
            {
                "gate": "source_gate",
                "mode": dict(gates.get("source_gate", {}) or {}).get("mode", ""),
                "passed": _bool_text(dict(gates.get("source_gate", {}) or {}).get("passed")),
                "strict_blocked": _bool_text(
                    dict(gates.get("source_gate", {}) or {}).get("strict_blocked")
                ),
                "notes": ",".join(
                    list(dict(gates.get("source_gate", {}) or {}).get("missing_real_reasons", []) or [])
                )
                or "n/a",
            },
            {
                "gate": "operator_family_gate",
                "mode": dict(gates.get("operator_family_gate", {}) or {}).get("mode", ""),
                "passed": _bool_text(
                    dict(gates.get("operator_family_gate", {}) or {}).get("passed")
                ),
                "strict_blocked": _bool_text(
                    dict(gates.get("operator_family_gate", {}) or {}).get("strict_blocked")
                ),
                "notes": ",".join(
                    list(dict(gates.get("operator_family_gate", {}) or {}).get("missing_families", []) or [])
                )
                or "n/a",
            },
            {
                "gate": "operator_realization_gate",
                "mode": dict(gates.get("operator_realization_gate", {}) or {}).get("mode", ""),
                "passed": _bool_text(
                    dict(gates.get("operator_realization_gate", {}) or {}).get("passed")
                ),
                "strict_blocked": _bool_text(
                    dict(gates.get("operator_realization_gate", {}) or {}).get("strict_blocked")
                ),
                "notes": ",".join(
                    list(
                        dict(gates.get("operator_realization_gate", {}) or {}).get(
                            "missing_realized_families", []
                        )
                        or []
                    )
                )
                or "n/a",
            },
        ],
        "vop_table": [
            {
                "feature": "VOP rounds",
                "value": vop.get("policy_round_count", ""),
                "notes": f"primary_round={vop.get('primary_round_index', '')}",
            },
            {
                "feature": "Policy / operator",
                "value": vop.get("policy_id", ""),
                "notes": (
                    f"program={vop.get('selected_operator_program_id', '')}, "
                    f"actions={','.join(vop.get('operator_actions', []) or []) or 'n/a'}"
                ),
            },
            {
                "feature": "Override",
                "value": vop.get("search_space_override", ""),
                "notes": (
                    f"runtime={json.dumps(vop.get('runtime_overrides', {}), ensure_ascii=False, sort_keys=True)}, "
                    f"fidelity={json.dumps(vop.get('fidelity_plan', {}), ensure_ascii=False, sort_keys=True)}"
                ),
            },
            {
                "feature": "Replan / fidelity",
                "value": _bool_text(vop.get("reflective_replan_triggered")),
                "notes": (
                    f"replan_reason={vop.get('reflective_replan_reason', '') or 'n/a'}, "
                    f"feedback_fidelity={vop.get('feedback_aware_fidelity_reason', '') or 'n/a'}"
                ),
            },
            {
                "feature": "Observed verdict",
                "value": vop.get("delegated_effectiveness_verdict", ""),
                "notes": vop.get("primary_round_key", "") or "n/a",
            },
        ],
    }
