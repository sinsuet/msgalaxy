#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
可视化模块

生成优化过程的可视化图表
"""

import sys
import os
import json
import ast
from collections import defaultdict

# 设置UTF-8编码
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from mpl_toolkits.mplot3d import Axes3D
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from core.artifact_index import load_artifact_index
from core.exceptions import VisualizationError
from core.logger import get_logger
from core.mode_contract import is_mass_mode, normalize_observability_mode
from core.path_policy import serialize_run_path
from core.runtime_feature_fingerprint import (
    fingerprint_display_rows,
    load_runtime_feature_fingerprint,
)
from core.modes.agent_loop.visualization_dispatch import render_agent_loop_artifacts
from core.modes.mass.visualization_dispatch import render_mass_artifacts
from core.modes.vop_maas.visualization_dispatch import render_vop_maas_artifacts

logger = get_logger("visualization")


def _component_min_corner(comp) -> List[float]:
    """将组件中心点坐标转换为包围盒最小角坐标。"""
    return [
        float(comp.position.x - comp.dimensions.x / 2.0),
        float(comp.position.y - comp.dimensions.y / 2.0),
        float(comp.position.z - comp.dimensions.z / 2.0),
    ]


def _envelope_bounds(design_state) -> tuple[List[float], List[float]]:
    """返回包络最小角与尺寸。"""
    size = design_state.envelope.outer_size
    if getattr(design_state.envelope, "origin", "center") == "center":
        min_corner = [-size.x / 2.0, -size.y / 2.0, -size.z / 2.0]
    else:
        min_corner = [0.0, 0.0, 0.0]
    dims = [size.x, size.y, size.z]
    return min_corner, dims


def _is_constant_series(values: np.ndarray, eps: float = 1e-9) -> bool:
    """判断序列是否几乎恒定。"""
    if values.size <= 1:
        return True
    finite_values = values[np.isfinite(values)]
    if finite_values.size <= 1:
        return True
    return float(np.max(finite_values) - np.min(finite_values)) <= eps


def _to_float_series(df: pd.DataFrame, column: str) -> Optional[np.ndarray]:
    """从DataFrame中安全提取浮点序列。"""
    if column not in df.columns:
        return None
    return pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=float)


def _read_csv_safely(path: str) -> pd.DataFrame:
    """安全读取 CSV，失败时返回空表。"""
    if not path or not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _load_summary_safely(experiment_dir: str) -> Dict[str, Any]:
    summary_path = os.path.join(experiment_dir, "summary.json")
    if not os.path.exists(summary_path):
        return {}
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _coerce_json_like(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return {}
    try:
        return json.loads(text)
    except Exception:
        try:
            return ast.literal_eval(text)
        except Exception:
            return text


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _compact_json_text(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or "n/a"
    payload = _coerce_json_like(value)
    if payload in ({}, [], ""):
        return "n/a"
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(payload)
    if len(text) > 220:
        return text[:217] + "..."
    return text


def _build_runtime_feature_fingerprint_summary(experiment_dir: str) -> str:
    try:
        payload = load_runtime_feature_fingerprint(experiment_dir)
    except Exception:
        payload = {}
    if not payload:
        return ""
    summary = _load_summary_safely(experiment_dir)
    tables = fingerprint_display_rows(payload)
    lines: List[str] = ["=== Runtime Feature Fingerprint ==="]

    for row in list(tables.get("baseline_table", []) or []):
        lines.append(
            "- "
            + f"{str(row.get('Feature', '') or 'n/a')}: "
            + f"requested={str(row.get('Requested', '') or 'n/a')}, "
            + f"effective={str(row.get('Effective', '') or 'n/a')}, "
            + f"notes={str(row.get('Notes', '') or 'n/a')}"
        )

    for row in list(tables.get("gate_table", []) or []):
        lines.append(
            "- Gate "
            + f"{str(row.get('gate', '') or 'n/a')}: "
            + f"mode={str(row.get('mode', '') or 'n/a')}, "
            + f"passed={str(row.get('passed', '') or 'n/a')}, "
            + f"strict_blocked={str(row.get('strict_blocked', '') or 'n/a')}, "
            + f"notes={str(row.get('notes', '') or 'n/a')}"
        )

    for row in list(tables.get("vop_table", []) or []):
        lines.append(
            "- Overlay "
            + f"{str(row.get('feature', '') or 'n/a')}: "
            + f"value={str(row.get('value', '') or 'n/a')}, "
            + f"notes={str(row.get('notes', '') or 'n/a')}"
        )

    runtime_feature_path = str(summary.get("runtime_feature_fingerprint_path", "") or "").strip()
    llm_final_summary_path = str(summary.get("llm_final_summary_zh_path", "") or "").strip()
    if runtime_feature_path:
        lines.append(f"- Artifact path: {runtime_feature_path}")
    if llm_final_summary_path:
        lines.append(f"- Chinese final summary: {llm_final_summary_path}")
    return "\n".join(lines)


def _latest_vop_round_row(vop_rounds: pd.DataFrame) -> Optional[pd.Series]:
    if len(vop_rounds) == 0:
        return None
    ordered = vop_rounds.copy()
    if "round_index" in ordered.columns:
        ordered["__round_index"] = pd.to_numeric(
            ordered["round_index"], errors="coerce"
        ).fillna(-1)
        ordered = ordered.sort_values(["__round_index", "vop_round_key"])
    return ordered.tail(1).iloc[0]


def _resolve_indexed_path(experiment_dir: str, raw_path: Any) -> str:
    raw = str(raw_path or "").strip()
    if not raw:
        return ""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(experiment_dir) / candidate
    candidate = candidate.resolve(strict=False)
    if candidate.exists():
        return str(candidate)
    return ""


def _resolve_run_artifact_path(experiment_dir: str, *, index_key: str, fallback: str = "") -> str:
    index = load_artifact_index(experiment_dir)
    indexed = _resolve_indexed_path(
        experiment_dir,
        dict(index.get("paths", {}) or {}).get(index_key, ""),
    )
    if indexed:
        return indexed
    return _resolve_indexed_path(experiment_dir, fallback)


def _resolve_run_scoped_dir(
    experiment_dir: str,
    *,
    scope: str,
    field: str,
    fallback: str = "",
) -> str:
    index = load_artifact_index(experiment_dir)
    indexed = _resolve_indexed_path(
        experiment_dir,
        dict(dict(index.get("scopes", {}) or {}).get(scope, {}) or {}).get(field, ""),
    )
    if indexed:
        return indexed
    return _resolve_indexed_path(experiment_dir, fallback)


def _parse_bool_series(df: pd.DataFrame, column: str) -> np.ndarray:
    """将表中布尔列解析为 bool 数组。"""
    if column not in df.columns or len(df) == 0:
        return np.array([], dtype=bool)
    return (
        df[column]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"1", "true", "yes", "y"})
        .to_numpy(dtype=bool)
    )


def _parse_operator_actions(raw_value: Any) -> List[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, float) and not np.isfinite(raw_value):
        return []

    if isinstance(raw_value, list):
        values = raw_value
    else:
        text = str(raw_value or "").strip()
        if not text:
            return []
        if text.lower() in {"nan", "none", "null", "[]"}:
            return []
        values = []
        if text.startswith("[") and text.endswith("]"):
            parsed = None
            try:
                parsed = json.loads(text)
            except Exception:
                try:
                    parsed = ast.literal_eval(text)
                except Exception:
                    parsed = None
            if isinstance(parsed, list):
                values = list(parsed)
        if not values:
            values = [item for item in text.split(",") if str(item).strip()]
    actions: List[str] = []
    seen = set()
    for item in values:
        action = str(item or "").strip().strip("'\"[]() ").lower()
        if action in {"nan", "none", "null"}:
            continue
        if not action or action in seen:
            continue
        seen.add(action)
        actions.append(action)
    return actions


def _operator_action_family(action: str) -> str:
    name = str(action or "").strip().lower()
    if name in {"group_move", "cg_recenter", "hot_spread", "swap"}:
        return "geometry"
    if name in {"add_heatstrap", "set_thermal_contact"}:
        return "thermal"
    if name in {"add_bracket", "stiffener_insert"}:
        return "structural"
    if name in {"bus_proximity_opt"}:
        return "power"
    if name in {"fov_keepout_push"}:
        return "mission"
    return "other"


def _detect_optimization_mode(experiment_dir: str) -> str:
    """
    检测本次实验的优化模式：
    - 优先读取 summary.json 的 run_mode / optimization_mode
    - 其次看 indexed mass trace 是否有有效记录
    - 默认 agent_loop
    """
    try:
        summary = _load_summary_safely(experiment_dir)
        if summary:
            mode = normalize_observability_mode(
                summary.get("run_mode") or summary.get("optimization_mode"),
                default="legacy",
            )
            if mode != "legacy":
                return mode
            mode = normalize_observability_mode(
                summary.get("optimization_mode"),
                default="legacy",
            )
            if mode != "legacy":
                return mode
    except Exception:
        pass

    mass_trace_path = _resolve_run_artifact_path(
        experiment_dir,
        index_key="mass_trace_csv",
        fallback="mass_trace.csv",
    )
    try:
        if os.path.exists(mass_trace_path):
            df = pd.read_csv(mass_trace_path)
            if len(df) > 0:
                return "mass"
    except Exception:
        pass
    return "agent_loop"


def build_power_density_proxy(design_state, csv_path: str = "") -> Dict[str, float]:
    """
    构建组件热代理值（确定性，无随机数）。

    说明：当前每轮日志没有按组件温度分布，故使用功率密度生成可解释代理值，
    用于展示热风险空间分布。若有全局 max_temp，则用其缩放幅度。
    """
    components = getattr(design_state, "components", [])
    if not components:
        return {}

    density = {}
    for comp in components:
        volume = max(float(comp.dimensions.x * comp.dimensions.y * comp.dimensions.z), 1e-6)
        density[comp.id] = float(comp.power) / volume

    values = np.array(list(density.values()), dtype=float)
    v_min = float(np.min(values))
    v_max = float(np.max(values))
    if abs(v_max - v_min) < 1e-12:
        normalized = {k: 0.5 for k in density.keys()}
    else:
        normalized = {k: float((v - v_min) / (v_max - v_min)) for k, v in density.items()}

    # 默认 20~60°C；若有全局 max_temp 则适当拉伸到不超过 120°C
    t_base = 20.0
    t_span = 40.0
    try:
        if csv_path and Path(csv_path).exists():
            df = pd.read_csv(csv_path)
            if "max_temp" in df.columns and len(df) > 0:
                global_max = float(df["max_temp"].iloc[-1])
                global_max = float(np.clip(global_max, 30.0, 120.0))
                t_span = max(20.0, global_max - t_base)
    except Exception:
        pass

    return {k: t_base + t_span * v for k, v in normalized.items()}


def _build_visualization_summary(
    csv_path: str,
    initial_state=None,
    final_state=None,
    thermal_data: Optional[Dict[str, float]] = None,
) -> str:
    """
    构建可视化摘要文本，快速说明迭代是否有效。
    """
    lines: List[str] = []
    lines.append("=== Optimization Visualization Summary ===")

    df: Optional[pd.DataFrame] = None
    if csv_path and os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
        except Exception:
            df = None

    if df is not None and len(df) > 0:
        lines.append(f"- Iterations logged: {len(df)}")

        penalty = _to_float_series(df, "penalty_score")
        if penalty is not None and len(penalty) > 0:
            p0 = float(np.nan_to_num(penalty[0], nan=0.0))
            p1 = float(np.nan_to_num(penalty[-1], nan=0.0))
            if p1 <= p0:
                abs_drop = p0 - p1
                rel_drop = (abs_drop / max(abs(p0), 1e-9)) * 100.0
                lines.append(
                    f"- Penalty: {p0:.2f} -> {p1:.2f} "
                    f"(reduction {abs_drop:.2f}, {rel_drop:.1f}%)"
                )
            else:
                abs_up = p1 - p0
                rel_up = (abs_up / max(abs(p0), 1e-9)) * 100.0
                lines.append(
                    f"- Penalty: {p0:.2f} -> {p1:.2f} "
                    f"(increase {abs_up:.2f}, {rel_up:.1f}%)"
                )

            final_idx = len(df) - 1
            part_cols = [
                ("penalty_violation", "violation"),
                ("penalty_temp", "temp"),
                ("penalty_clearance", "clearance"),
                ("penalty_cg", "cg"),
                ("penalty_collision", "collision"),
            ]
            part_values = []
            for col, label in part_cols:
                series = _to_float_series(df, col)
                if series is None or final_idx >= len(series):
                    continue
                val = float(np.nan_to_num(series[final_idx], nan=0.0))
                part_values.append((label, val))
            if part_values:
                dominant = max(part_values, key=lambda x: x[1])
                lines.append(
                    f"- Dominant penalty term (final): {dominant[0]} = {dominant[1]:.2f}"
                )

        violations = _to_float_series(df, "num_violations")
        if violations is not None and len(violations) > 0:
            v0 = int(round(float(np.nan_to_num(violations[0], nan=0.0))))
            v1 = int(round(float(np.nan_to_num(violations[-1], nan=0.0))))
            lines.append(f"- Violations: {v0} -> {v1} ({v1 - v0:+d})")

        eff = _to_float_series(df, "effectiveness_score")
        if eff is not None and len(eff) > 0:
            eff = np.nan_to_num(eff, nan=0.0)
            positive_ratio = float(np.mean(eff > 0.0)) * 100.0
            lines.append(
                f"- Effectiveness: mean={float(np.mean(eff)):.2f}, "
                f"positive_ratio={positive_ratio:.1f}%"
            )

        stable_metrics = []
        for col in ("max_temp", "min_clearance", "cg_offset"):
            series = _to_float_series(df, col)
            if series is not None and _is_constant_series(np.nan_to_num(series, nan=0.0)):
                stable_metrics.append(col)
        if stable_metrics:
            lines.append(
                "- Near-constant metrics detected: " + ", ".join(stable_metrics)
            )

    # 布局位移摘要
    if initial_state is not None and final_state is not None:
        init_map = {c.id: c for c in initial_state.components}
        final_map = {c.id: c for c in final_state.components}
        common_ids = sorted(set(init_map.keys()) & set(final_map.keys()))
        if common_ids:
            movements = []
            for cid in common_ids:
                c0 = init_map[cid]
                c1 = final_map[cid]
                dx = float(c1.position.x - c0.position.x)
                dy = float(c1.position.y - c0.position.y)
                dz = float(c1.position.z - c0.position.z)
                dist = float(np.sqrt(dx * dx + dy * dy + dz * dz))
                movements.append((cid, dist, dx, dy, dz))

            max_item = max(movements, key=lambda x: x[1])
            mean_move = float(np.mean([m[1] for m in movements]))
            lines.append(
                f"- Layout movement: mean={mean_move:.2f} mm, "
                f"max={max_item[1]:.2f} mm ({max_item[0]})"
            )
            if max_item[1] > 1e-9:
                lines.append(
                    f"  direction({max_item[0]}): "
                    f"dx={max_item[2]:+.2f}, dy={max_item[3]:+.2f}, dz={max_item[4]:+.2f} mm"
                )

    # 热代理摘要
    if thermal_data:
        hottest = max(thermal_data.items(), key=lambda x: x[1])
        coldest = min(thermal_data.items(), key=lambda x: x[1])
        lines.append(
            f"- Thermal proxy: hottest={hottest[0]} {hottest[1]:.1f} degC, "
            f"coolest={coldest[0]} {coldest[1]:.1f} degC"
        )

    if len(lines) == 1:
        lines.append("- No available data for summary.")

    return "\n".join(lines)


def _build_mass_visualization_summary(mass_csv_path: str) -> str:
    """构建 mass 运行摘要。"""
    lines: List[str] = []
    lines.append("=== MASS Visualization Summary ===")

    if not mass_csv_path or not os.path.exists(mass_csv_path):
        lines.append("- No mass_trace.csv found.")
        return "\n".join(lines)

    try:
        df = pd.read_csv(mass_csv_path)
    except Exception:
        lines.append("- Failed to read mass_trace.csv.")
        return "\n".join(lines)

    if len(df) == 0:
        lines.append("- No attempt records available.")
        return "\n".join(lines)

    lines.append(f"- Attempts logged: {len(df)}")
    if "diagnosis_status" in df.columns:
        counts = df["diagnosis_status"].fillna("unknown").value_counts()
        status_desc = ", ".join([f"{idx}:{int(val)}" for idx, val in counts.items()])
        lines.append(f"- Diagnosis distribution: {status_desc}")
    if "dominant_violation" in df.columns:
        dom_counts = (
            df["dominant_violation"]
            .fillna("")
            .astype(str)
            .str.strip()
            .replace("", "none")
            .value_counts()
        )
        dom_desc = ", ".join([f"{idx}:{int(val)}" for idx, val in dom_counts.items()][:5])
        if dom_desc:
            lines.append(f"- Dominant violation(top): {dom_desc}")

    if "is_best_attempt" in df.columns:
        best_mask = (
            df["is_best_attempt"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"1", "true", "yes"})
        )
        best_rows = df[best_mask]
    else:
        best_rows = df.iloc[0:0]
    if len(best_rows) == 0:
        best_rows = df.tail(1)

    if len(best_rows) > 0:
        best = best_rows.iloc[-1]
        lines.append(
            "- Best attempt: "
            f"attempt={int(best.get('attempt', 0))}, "
            f"status={best.get('diagnosis_status', '')}, "
            f"best_cv={best.get('best_cv', '')}, "
            f"aocc_cv={best.get('aocc_cv', '')}"
        )
        selected_reason = str(best.get("physics_audit_selected_reason", "")).strip()
        if selected_reason:
            lines.append(f"- Physics audit selection: {selected_reason}")
        mp_pairs = [
            ("best_candidate_safety_factor", "safety_factor"),
            ("best_candidate_first_modal_freq", "modal_freq"),
            ("best_candidate_voltage_drop", "voltage_drop"),
            ("best_candidate_power_margin", "power_margin"),
            ("best_candidate_peak_power", "peak_power"),
        ]
        mp_items: List[str] = []
        for column, label in mp_pairs:
            if column not in df.columns:
                continue
            value = _safe_float(best.get(column), default=np.nan)
            if np.isfinite(value):
                mp_items.append(f"{label}={value:.4f}")
        if mp_items:
            lines.append("- Best attempt multiphysics: " + ", ".join(mp_items))

    if "operator_actions" in df.columns:
        family_counts: Dict[str, int] = defaultdict(int)
        for raw_actions in df["operator_actions"].tolist():
            for action in _parse_operator_actions(raw_actions):
                family = _operator_action_family(action)
                family_counts[family] += 1
        if family_counts:
            ordering = ["geometry", "thermal", "structural", "power", "mission", "other"]
            family_desc = ", ".join(
                f"{name}={int(family_counts.get(name, 0))}"
                for name in ordering
                if int(family_counts.get(name, 0)) > 0
            )
            if family_desc:
                lines.append(f"- Operator family coverage: {family_desc}")

    if "solver_cost" in df.columns:
        solver_cost = pd.to_numeric(df["solver_cost"], errors="coerce").fillna(0.0)
        if len(solver_cost) > 0:
            lines.append(
                f"- Solver cost: total={float(np.sum(solver_cost)):.2f}s, "
                f"mean={float(np.mean(solver_cost)):.2f}s"
            )

    return "\n".join(lines)


def _build_mass_tables_summary(tables_dir: str) -> str:
    """基于 tables/*.csv 构建补充摘要。"""
    lines: List[str] = []
    lines.append("=== MASS Tables Summary ===")

    attempts = _read_csv_safely(os.path.join(tables_dir, "attempts.csv"))
    generations = _read_csv_safely(os.path.join(tables_dir, "generations.csv"))
    policies = _read_csv_safely(os.path.join(tables_dir, "policy_tuning.csv"))
    physics = _read_csv_safely(os.path.join(tables_dir, "physics_budget.csv"))
    phases = _read_csv_safely(os.path.join(tables_dir, "phases.csv"))
    vop_rounds = _read_csv_safely(os.path.join(tables_dir, "vop_rounds.csv"))
    release_audit = _read_csv_safely(os.path.join(tables_dir, "release_audit.csv"))
    candidates = _read_csv_safely(os.path.join(tables_dir, "candidates.csv"))
    layouts = _read_csv_safely(os.path.join(tables_dir, "layout_timeline.csv"))
    layout_deltas = _read_csv_safely(os.path.join(tables_dir, "layout_deltas.csv"))

    counts = {
        "attempts": int(len(attempts)),
        "generations": int(len(generations)),
        "policies": int(len(policies)),
        "physics": int(len(physics)),
        "phases": int(len(phases)),
        "vop_rounds": int(len(vop_rounds)),
        "release_audit": int(len(release_audit)),
        "candidates": int(len(candidates)),
        "layouts": int(len(layouts)),
        "layout_deltas": int(len(layout_deltas)),
    }
    lines.append(
        "- Table rows: "
        + ", ".join(f"{key}={value}" for key, value in counts.items())
    )
    vop_round_overview = _collect_vop_round_overview(vop_rounds, policies, phases)
    if vop_round_overview is not None:
        lines.append(
            "- VOP round audit: "
            f"rounds={int(vop_round_overview['round_count'])}, "
            f"joined={int(vop_round_overview['joined_keys'])}, "
            f"latest={str(vop_round_overview['latest_round_key']) or 'n/a'}, "
            f"final_policy={str(vop_round_overview['final_policy_id']) or 'n/a'}"
        )
    if len(release_audit) > 0:
        audit_row = release_audit.tail(1).iloc[0]
        final_audit_status = str(audit_row.get("final_audit_status", "") or "").strip()
        simulation_backend = str(audit_row.get("simulation_backend", "") or "").strip()
        thermal_mode = str(
            audit_row.get("thermal_evaluator_mode", "") or ""
        ).strip()
        first_feasible_eval = "n/a"
        first_feasible_raw = audit_row.get("first_feasible_eval")
        if pd.notna(first_feasible_raw) and str(first_feasible_raw).strip():
            first_feasible_eval = str(first_feasible_raw).strip()
        comsol_calls_to_first_feasible = "n/a"
        comsol_calls_raw = audit_row.get("comsol_calls_to_first_feasible")
        if pd.notna(comsol_calls_raw) and str(comsol_calls_raw).strip():
            comsol_calls_to_first_feasible = str(comsol_calls_raw).strip()
        lines.append(
            "- Release audit: "
            f"status={final_audit_status or 'n/a'}, "
            f"backend={simulation_backend or 'n/a'}, "
            f"thermal={thermal_mode or 'n/a'}, "
            f"first_feasible_eval={first_feasible_eval}, "
            f"comsol_calls_to_first_feasible={comsol_calls_to_first_feasible}"
        )

    if len(attempts) > 0 and "diagnosis_status" in attempts.columns:
        status_counts = attempts["diagnosis_status"].fillna("unknown").value_counts()
        status_desc = ", ".join(f"{idx}:{int(val)}" for idx, val in status_counts.items())
        lines.append(f"- Attempt diagnosis: {status_desc}")

    if len(generations) > 0:
        best_cv = _to_float_series(generations, "best_cv")
        if best_cv is not None and len(best_cv) > 0:
            finite = best_cv[np.isfinite(best_cv)]
            if finite.size > 0:
                lines.append(f"- Generation best_cv_min: {float(np.min(finite)):.6f}")

        feasible_count = _to_float_series(generations, "feasible_count")
        if feasible_count is not None and len(feasible_count) > 0:
            first_feasible_idx = np.where(np.nan_to_num(feasible_count, nan=0.0) > 0.0)[0]
            if first_feasible_idx.size > 0 and "generation" in generations.columns:
                gen_values = pd.to_numeric(generations["generation"], errors="coerce").fillna(0.0).to_numpy()
                lines.append(
                    f"- First feasible generation: {int(gen_values[int(first_feasible_idx[0])] if len(gen_values) > int(first_feasible_idx[0]) else 0)}"
                )

    if len(attempts) > 0 and "operator_actions" in attempts.columns:
        family_counts: Dict[str, int] = defaultdict(int)
        for raw_actions in attempts["operator_actions"].tolist():
            for action in _parse_operator_actions(raw_actions):
                family_counts[_operator_action_family(action)] += 1
        if family_counts:
            ordering = ["geometry", "thermal", "structural", "power", "mission", "other"]
            family_desc = ", ".join(
                f"{name}={int(family_counts.get(name, 0))}"
                for name in ordering
                if int(family_counts.get(name, 0)) > 0
            )
            if family_desc:
                lines.append(f"- Operator family coverage: {family_desc}")

    if len(attempts) > 0:
        mp_columns = [
            ("metric_safety_factor", "safety_factor"),
            ("metric_first_modal_freq", "modal_freq"),
            ("metric_voltage_drop", "voltage_drop"),
            ("metric_power_margin", "power_margin"),
            ("metric_peak_power", "peak_power"),
        ]
        best_row = attempts.tail(1).iloc[0]
        if "is_best_attempt" in attempts.columns:
            best_mask = (
                attempts["is_best_attempt"]
                .astype(str)
                .str.strip()
                .str.lower()
                .isin({"1", "true", "yes", "y"})
            )
            if bool(best_mask.any()):
                best_row = attempts[best_mask].tail(1).iloc[0]
        mp_items: List[str] = []
        for column, label in mp_columns:
            if column not in attempts.columns:
                continue
            value = _safe_float(best_row.get(column), default=np.nan)
            if np.isfinite(value):
                mp_items.append(f"{label}={value:.4f}")
        if mp_items:
            lines.append("- Best-attempt multiphysics: " + ", ".join(mp_items))

    if len(lines) == 1:
        lines.append("- No materialized table data available.")
    return "\n".join(lines)


def _build_vop_controller_summary(
    experiment_dir: str,
    *,
    tables_dir: str,
) -> str:
    """构建 VOP controller 视角摘要。"""
    summary = _load_summary_safely(experiment_dir)
    policies = _read_csv_safely(os.path.join(tables_dir, "policy_tuning.csv"))
    phases = _read_csv_safely(os.path.join(tables_dir, "phases.csv"))
    vop_rounds = _read_csv_safely(os.path.join(tables_dir, "vop_rounds.csv"))
    release_audit = _read_csv_safely(os.path.join(tables_dir, "release_audit.csv"))
    round_overview = _collect_vop_round_overview(vop_rounds, policies, phases)
    latest_round = _latest_vop_round_row(vop_rounds)

    primary_round_index = _safe_int(summary.get("vop_policy_primary_round_index"), -1)
    if primary_round_index < 0 and latest_round is not None:
        primary_round_index = _safe_int(latest_round.get("round_index", -1), -1)
    primary_round_key = str(summary.get("vop_policy_primary_round_key", "") or "").strip()
    if not primary_round_key and latest_round is not None:
        primary_round_key = str(latest_round.get("vop_round_key", "") or "").strip()
    if not primary_round_key and round_overview is not None:
        primary_round_key = str(round_overview.get("latest_round_key", "") or "").strip()

    round_count = _safe_int(summary.get("vop_round_count"), 0)
    if round_count <= 0 and round_overview is not None:
        round_count = _safe_int(round_overview.get("round_count"), 0)

    decision_summary = _coerce_json_like(summary.get("vop_decision_summary", {}))
    if not isinstance(decision_summary, dict):
        decision_summary = {}
    delegated_effect = _coerce_json_like(summary.get("vop_delegated_effect_summary", {}))
    if not isinstance(delegated_effect, dict):
        delegated_effect = {}
    reflective = _coerce_json_like(summary.get("vop_reflective_replanning", {}))
    if not isinstance(reflective, dict):
        reflective = {}

    if latest_round is not None:
        latest_round_dict = latest_round.to_dict()
        change_summary = _coerce_json_like(latest_round_dict.get("change_summary", {}))
        if not isinstance(change_summary, dict):
            change_summary = {}
        if not decision_summary:
            decision_summary = {
                "policy_id": str(latest_round_dict.get("policy_id", "") or ""),
                "selected_operator_program_id": str(
                    latest_round_dict.get("selected_operator_program_id", "") or ""
                ),
                "operator_actions": list(
                    change_summary.get("operator_actions", [])
                    or _parse_operator_actions(latest_round_dict.get("operator_actions", []))
                ),
                "search_space_override": str(
                    latest_round_dict.get("search_space_override", "")
                    or change_summary.get("search_space_override", "")
                    or ""
                ),
                "intent_changes": _coerce_json_like(
                    change_summary.get("intent_changes", {})
                ),
                "runtime_overrides": _coerce_json_like(
                    latest_round_dict.get("runtime_overrides", {})
                    or change_summary.get("runtime_overrides", {})
                ),
                "fidelity_plan": _coerce_json_like(
                    latest_round_dict.get("fidelity_plan", {})
                    or change_summary.get("fidelity_plan", {})
                ),
                "expected_effects": _coerce_json_like(
                    latest_round_dict.get("expected_effects", {})
                ),
                "decision_rationale": str(
                    latest_round_dict.get("decision_rationale", "") or ""
                ),
                "confidence": latest_round_dict.get("confidence", None),
            }
        if not delegated_effect:
            effectiveness = _coerce_json_like(
                latest_round_dict.get("effectiveness_summary", {})
            )
            if not isinstance(effectiveness, dict):
                effectiveness = {}
            delegated_effect = {
                "diagnosis_status": str(effectiveness.get("diagnosis_status", "") or ""),
                "diagnosis_reason": str(effectiveness.get("diagnosis_reason", "") or ""),
                "search_space_effect": str(
                    effectiveness.get("search_space_effect", "") or ""
                ),
                "first_feasible_eval": effectiveness.get("first_feasible_eval", None),
                "comsol_calls_to_first_feasible": effectiveness.get(
                    "comsol_calls_to_first_feasible", None
                ),
                "audit_status": str(effectiveness.get("audit_status", "") or ""),
                "effectiveness_verdict": str(
                    effectiveness.get("effectiveness_verdict", "") or ""
                ),
                "observed_effects": _coerce_json_like(
                    latest_round_dict.get("observed_effects", {})
                ),
            }
        if not reflective:
            reflective = {
                "triggered": bool(
                    str(latest_round_dict.get("trigger_reason", "") or "").strip()
                ),
                "trigger_reason": str(latest_round_dict.get("trigger_reason", "") or ""),
                "final_policy_id": str(latest_round_dict.get("final_policy_id", "") or ""),
                "executed_mass_rerun": str(
                    latest_round_dict.get("mass_rerun_executed", "") or ""
                ).strip().lower()
                in {"1", "true", "yes", "y"},
                "skipped_reason": str(latest_round_dict.get("skipped_reason", "") or ""),
            }

    if len(release_audit) > 0:
        audit_row = release_audit.tail(1).iloc[0]
        delegated_effect.setdefault(
            "audit_status",
            str(audit_row.get("final_audit_status", "") or ""),
        )
        delegated_effect.setdefault(
            "first_feasible_eval",
            audit_row.get("first_feasible_eval", None),
        )
        delegated_effect.setdefault(
            "comsol_calls_to_first_feasible",
            audit_row.get("comsol_calls_to_first_feasible", None),
        )
    lines: List[str] = []
    lines.append("=== VOP Controller Summary ===")
    lines.append(f"- Primary round index: {primary_round_index}")
    lines.append(f"- Primary round key: {primary_round_key or 'n/a'}")
    lines.append(f"- Round count: {round_count}")
    lines.append(
        f"- Policy id: {str(decision_summary.get('policy_id', '') or summary.get('final_policy_id', summary.get('vop_policy_id', '')) or 'n/a')}"
    )
    lines.append(
        f"- Decision rationale: {_compact_json_text(decision_summary.get('decision_rationale', ''))}"
    )
    lines.append(
        "- Search-space override: "
        f"{str(decision_summary.get('search_space_override', '') or 'n/a')}"
    )
    lines.append(
        "- Runtime/fidelity override: "
        f"runtime={_compact_json_text(decision_summary.get('runtime_overrides', {}))}, "
        f"fidelity={_compact_json_text(decision_summary.get('fidelity_plan', {}))}"
    )
    lines.append(
        "- Reflective replan: "
        f"triggered={bool(reflective.get('triggered', False))}, "
        f"reason={str(reflective.get('trigger_reason', '') or reflective.get('skipped_reason', '') or 'n/a')}"
    )
    lines.append(
        "- Expected vs observed effect: "
        f"expected={_compact_json_text(decision_summary.get('expected_effects', {}))}, "
        f"observed={_compact_json_text(delegated_effect.get('observed_effects', {}))}"
    )
    delegated = str(summary.get("delegated_execution_mode", "") or summary.get("execution_mode", "") or "")
    if delegated:
        lines.append(f"- Delegated execution mode: {delegated}")
    lines.append(
        "- Delegated mass final result: "
        f"diagnosis={str(delegated_effect.get('diagnosis_status', '') or 'n/a')}, "
        f"audit={str(delegated_effect.get('audit_status', '') or 'n/a')}, "
        f"verdict={str(delegated_effect.get('effectiveness_verdict', '') or 'n/a')}"
    )
    if delegated_effect.get("first_feasible_eval", None) not in (None, "", "n/a"):
        lines.append(
            "- First feasible eval / COMSOL calls: "
            f"{_compact_json_text(delegated_effect.get('first_feasible_eval'))} / "
            f"{_compact_json_text(delegated_effect.get('comsol_calls_to_first_feasible'))}"
        )
    if latest_round is not None:
        lines.append(
            "- Latest round audit: "
            f"stage={str(latest_round.get('stage', '') or 'n/a')}, "
            f"final_policy={str(latest_round.get('final_policy_id', '') or 'n/a')}, "
            f"mass_rerun_executed={str(latest_round.get('mass_rerun_executed', '') or 'n/a')}"
        )
    runtime_feature_summary = _build_runtime_feature_fingerprint_summary(experiment_dir)
    if runtime_feature_summary:
        lines.extend(["", runtime_feature_summary])
    return "\n".join(lines)


def _collect_vop_round_overview(
    vop_rounds: pd.DataFrame,
    policies: pd.DataFrame,
    phases: pd.DataFrame,
) -> Optional[Dict[str, Any]]:
    policy_keys = set()
    phase_keys = set()
    round_keys = set()
    final_policy_id = ""

    if len(policies) > 0 and "vop_round_key" in policies.columns:
        policy_keys = {
            str(item).strip()
            for item in policies["vop_round_key"].fillna("").tolist()
            if str(item).strip()
        }

    if len(phases) > 0 and "vop_round_key" in phases.columns:
        phase_keys = {
            str(item).strip()
            for item in phases["vop_round_key"].fillna("").tolist()
            if str(item).strip()
        }

    if len(vop_rounds) > 0 and "vop_round_key" in vop_rounds.columns:
        round_keys = {
            str(item).strip()
            for item in vop_rounds["vop_round_key"].fillna("").tolist()
            if str(item).strip()
        }
        if "final_policy_id" in vop_rounds.columns:
            ordered = vop_rounds.copy()
            if "round_index" in ordered.columns:
                ordered["__round_index"] = pd.to_numeric(
                    ordered["round_index"], errors="coerce"
                ).fillna(-1)
                ordered = ordered.sort_values(["__round_index", "vop_round_key"])
            latest_row = ordered.tail(1).iloc[0]
            final_policy_id = str(latest_row.get("final_policy_id", "") or "")

    all_keys = sorted(round_keys | policy_keys | phase_keys)
    if not all_keys:
        return None

    return {
        "round_count": int(len(round_keys) or len(all_keys)),
        "policy_keys": int(len(policy_keys)),
        "phase_keys": int(len(phase_keys)),
        "joined_keys": int(
            len((policy_keys & phase_keys) & round_keys)
            if round_keys
            else len(policy_keys & phase_keys)
        ),
        "latest_round_key": str(all_keys[-1]),
        "final_policy_id": str(final_policy_id or ""),
    }


def _read_jsonl_safely(path: str) -> List[Dict[str, Any]]:
    """安全读取 JSONL 文件。"""
    if not path or not os.path.exists(path):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except Exception:
        return []
    return rows


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全解析浮点值。"""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(parsed):
        return float(default)
    return float(parsed)


def _resolve_layout_snapshot_path(experiment_dir: str, raw_snapshot_path: str) -> Optional[Path]:
    """解析 layout_event 中的 snapshot 路径。"""
    raw = str(raw_snapshot_path or "").strip()
    if not raw:
        return None
    snapshot_path = Path(raw)
    if snapshot_path.exists():
        return snapshot_path
    if not snapshot_path.is_absolute():
        candidate = Path(experiment_dir) / raw
        if candidate.exists():
            return candidate
    return None


def _load_layout_snapshot_records(experiment_dir: str) -> List[Dict[str, Any]]:
    """加载并排序 layout 事件及其对应快照。"""
    events_path = os.path.join(experiment_dir, "events", "layout_events.jsonl")
    events = _read_jsonl_safely(events_path)
    if not events:
        return []

    events = sorted(
        events,
        key=lambda item: (
            int(item.get("sequence", 0) or 0),
            int(item.get("iteration", 0) or 0),
            int(item.get("attempt", 0) or 0),
        ),
    )

    records: List[Dict[str, Any]] = []
    for event in events:
        snapshot_path = _resolve_layout_snapshot_path(
            experiment_dir=experiment_dir,
            raw_snapshot_path=str(event.get("snapshot_path", "") or ""),
        )
        if snapshot_path is None:
            continue
        try:
            snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(snapshot_payload, dict):
            continue
        records.append(
            {
                "event": dict(event or {}),
                "snapshot": snapshot_payload,
                "snapshot_path": str(snapshot_path),
            }
        )
    return records


def _component_centers_from_state_dict(state_dict: Dict[str, Any]) -> Dict[str, np.ndarray]:
    """从状态字典中提取组件中心点。"""
    centers: Dict[str, np.ndarray] = {}
    for comp in list((state_dict or {}).get("components", []) or []):
        if not isinstance(comp, dict):
            continue
        comp_id = str(comp.get("id", "") or "").strip()
        if not comp_id:
            continue
        pos = dict(comp.get("position", {}) or {})
        centers[comp_id] = np.asarray(
            [
                _safe_float(pos.get("x", 0.0)),
                _safe_float(pos.get("y", 0.0)),
                _safe_float(pos.get("z", 0.0)),
            ],
            dtype=float,
        )
    return centers


def _compute_component_displacements(
    reference_state: Dict[str, Any],
    target_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """计算 target 相对 reference 的组件位移。"""
    ref_map = _component_centers_from_state_dict(reference_state)
    tgt_map = _component_centers_from_state_dict(target_state)
    common_ids = sorted(set(ref_map.keys()) & set(tgt_map.keys()))

    rows: List[Dict[str, Any]] = []
    for comp_id in common_ids:
        ref_pos = ref_map[comp_id]
        tgt_pos = tgt_map[comp_id]
        delta = tgt_pos - ref_pos
        dist = float(np.linalg.norm(delta))
        rows.append(
            {
                "component_id": comp_id,
                "dx": float(delta[0]),
                "dy": float(delta[1]),
                "dz": float(delta[2]),
                "dist": dist,
            }
        )
    return rows


def _select_best_candidate_record(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """选取最佳 attempt_candidate 记录（best_cv 最小，序列最早优先）。"""
    candidate_records: List[Tuple[float, int, Dict[str, Any]]] = []
    for record in records:
        event = dict(record.get("event", {}) or {})
        if str(event.get("stage", "") or "") != "attempt_candidate":
            continue
        snapshot = dict(record.get("snapshot", {}) or {})
        metrics = dict(snapshot.get("metrics", {}) or {})
        best_cv = _safe_float(metrics.get("best_cv"), default=np.inf)
        sequence = int(event.get("sequence", 0) or 0)
        candidate_records.append((best_cv, sequence, record))
    if candidate_records:
        candidate_records.sort(key=lambda item: (item[0], item[1]))
        return candidate_records[0][2]
    return records[-1]


def _build_frame_transition_stats(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """计算相邻快照的位移统计。"""
    stats: List[Dict[str, Any]] = []
    for idx in range(1, len(records)):
        prev_record = records[idx - 1]
        curr_record = records[idx]
        prev_snapshot = dict(prev_record.get("snapshot", {}) or {})
        curr_snapshot = dict(curr_record.get("snapshot", {}) or {})
        disps = _compute_component_displacements(
            dict(prev_snapshot.get("design_state", {}) or {}),
            dict(curr_snapshot.get("design_state", {}) or {}),
        )
        if disps:
            values = np.asarray([float(item.get("dist", 0.0)) for item in disps], dtype=float)
            max_dist = float(np.max(values))
            mean_dist = float(np.mean(values))
            moved_count = int(np.sum(values > 1e-6))
        else:
            max_dist = 0.0
            mean_dist = 0.0
            moved_count = 0
        curr_event = dict(curr_record.get("event", {}) or {})
        prev_event = dict(prev_record.get("event", {}) or {})
        stats.append(
            {
                "from_sequence": int(prev_event.get("sequence", 0) or 0),
                "to_sequence": int(curr_event.get("sequence", 0) or 0),
                "max_dist": float(max_dist),
                "mean_dist": float(mean_dist),
                "moved_count": int(moved_count),
            }
        )
    return stats


def _infer_zero_movement_reason(
    records: List[Dict[str, Any]],
    initial_to_best: List[Dict[str, Any]],
    frame_stats: List[Dict[str, Any]],
) -> str:
    """推断位移全零时的原因码。"""
    if not records:
        return "no_layout_records"
    if len(records) <= 1:
        return "single_snapshot_state"

    max_dist = 0.0
    if initial_to_best:
        max_dist = float(np.max(np.asarray([item["dist"] for item in initial_to_best], dtype=float)))
    if max_dist > 1e-6:
        return ""

    hashes = set()
    for record in records:
        snapshot = dict(record.get("snapshot", {}) or {})
        metadata = dict(snapshot.get("metadata", {}) or {})
        state_hash = str(metadata.get("layout_state_hash", "") or "").strip()
        if state_hash:
            hashes.add(state_hash)
    if hashes and len(hashes) == 1:
        return "identical_snapshot_hash"

    if frame_stats and all(float(item.get("max_dist", 0.0)) <= 1e-6 for item in frame_stats):
        branch_actions = [
            str(dict(record.get("event", {}) or {}).get("branch_action", "") or "").strip().lower()
            for record in records
        ]
        if branch_actions and all((not action) or action.startswith("identity") for action in branch_actions):
            return "identity_branch_stable"
        return "frame_to_frame_zero"

    return "unknown"


def _component_map_from_dict(state_dict: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """把 design_state 字典转换为组件映射。"""
    out: Dict[str, Dict[str, Any]] = {}
    for comp in list((state_dict or {}).get("components", []) or []):
        if not isinstance(comp, dict):
            continue
        comp_id = str(comp.get("id", "")).strip()
        if comp_id:
            out[comp_id] = comp
    return out


def _component_min_corner_from_dict(comp: Dict[str, Any]) -> List[float]:
    """把组件中心坐标转换为包围盒最小角（dict 版本）。"""
    pos = dict(comp.get("position", {}) or {})
    dims = dict(comp.get("dimensions", {}) or {})
    cx = float(pos.get("x", 0.0) or 0.0)
    cy = float(pos.get("y", 0.0) or 0.0)
    cz = float(pos.get("z", 0.0) or 0.0)
    dx = float(dims.get("x", 0.0) or 0.0)
    dy = float(dims.get("y", 0.0) or 0.0)
    dz = float(dims.get("z", 0.0) or 0.0)
    return [cx - dx / 2.0, cy - dy / 2.0, cz - dz / 2.0]


def _envelope_bounds_from_state_dict(state_dict: Dict[str, Any]) -> List[float]:
    """返回包络在 XY 平面的边界 [x_min, x_max, y_min, y_max]。"""
    envelope = dict((state_dict or {}).get("envelope", {}) or {})
    outer_size = dict(envelope.get("outer_size", {}) or {})
    sx = float(outer_size.get("x", 0.0) or 0.0)
    sy = float(outer_size.get("y", 0.0) or 0.0)
    origin = str(envelope.get("origin", "center") or "center").strip().lower()
    if sx > 0.0 and sy > 0.0:
        if origin == "center":
            return [-sx / 2.0, sx / 2.0, -sy / 2.0, sy / 2.0]
        return [0.0, sx, 0.0, sy]

    # 兜底：从组件范围估计边界
    min_corner = np.array([np.inf, np.inf], dtype=float)
    max_corner = np.array([-np.inf, -np.inf], dtype=float)
    for comp in list((state_dict or {}).get("components", []) or []):
        if not isinstance(comp, dict):
            continue
        comp_min = np.asarray(_component_min_corner_from_dict(comp)[:2], dtype=float)
        dims = dict(comp.get("dimensions", {}) or {})
        comp_max = comp_min + np.asarray(
            [float(dims.get("x", 0.0) or 0.0), float(dims.get("y", 0.0) or 0.0)],
            dtype=float,
        )
        min_corner = np.minimum(min_corner, comp_min)
        max_corner = np.maximum(max_corner, comp_max)

    if not np.isfinite(min_corner).all():
        return [-100.0, 100.0, -100.0, 100.0]
    margin = max(float(np.max(max_corner - min_corner)) * 0.08, 5.0)
    return [
        float(min_corner[0] - margin),
        float(max_corner[0] + margin),
        float(min_corner[1] - margin),
        float(max_corner[1] + margin),
    ]


def _build_component_thermal_proxy_from_state(
    state_dict: Dict[str, Any],
    metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """基于组件功率密度生成逐组件热代理（dict 版本）。"""
    comp_map = _component_map_from_dict(state_dict)
    if not comp_map:
        return {}

    density: Dict[str, float] = {}
    for comp_id, comp in comp_map.items():
        dims = dict(comp.get("dimensions", {}) or {})
        volume = (
            float(dims.get("x", 0.0) or 0.0)
            * float(dims.get("y", 0.0) or 0.0)
            * float(dims.get("z", 0.0) or 0.0)
        )
        volume = max(volume, 1e-6)
        density[comp_id] = float(comp.get("power", 0.0) or 0.0) / volume

    values = np.asarray(list(density.values()), dtype=float)
    v_min = float(np.min(values))
    v_max = float(np.max(values))
    if abs(v_max - v_min) <= 1e-12:
        normalized = {k: 0.5 for k in density.keys()}
    else:
        normalized = {k: float((v - v_min) / (v_max - v_min)) for k, v in density.items()}

    max_temp = 60.0
    if metrics:
        try:
            max_temp = float(metrics.get("max_temp", max_temp) or max_temp)
        except Exception:
            max_temp = 60.0
    max_temp = float(np.clip(max_temp, 30.0, 140.0))
    t_base = max(18.0, max_temp - 35.0)
    t_span = max(12.0, max_temp - t_base)
    return {k: float(t_base + t_span * v) for k, v in normalized.items()}


def _plot_layout_timeline_frame(
    *,
    event: Dict[str, Any],
    snapshot: Dict[str, Any],
    prev_snapshot: Optional[Dict[str, Any]],
    output_path: str,
) -> None:
    """绘制单帧布局+热力图。"""
    state = dict(snapshot.get("design_state", {}) or {})
    prev_state = dict((prev_snapshot or {}).get("design_state", {}) or {})
    components = _component_map_from_dict(state)
    prev_components = _component_map_from_dict(prev_state)
    metrics = dict(snapshot.get("metrics", {}) or {})
    delta = dict(snapshot.get("delta", {}) or {})

    moved_ids = set(str(item) for item in list(event.get("moved_components", delta.get("moved_components", [])) or []))
    heatsink_ids = set(str(item) for item in list(event.get("added_heatsinks", delta.get("added_heatsinks", [])) or []))
    bracket_ids = set(str(item) for item in list(event.get("added_brackets", delta.get("added_brackets", [])) or []))
    contact_ids = set(str(item) for item in list(event.get("changed_contacts", delta.get("changed_contacts", [])) or []))
    coating_ids = set(str(item) for item in list(event.get("changed_coatings", delta.get("changed_coatings", [])) or []))
    operator_actions = _parse_operator_actions(event.get("operator_actions", []))
    operator_family_counts: Dict[str, int] = defaultdict(int)
    for action in operator_actions:
        operator_family_counts[_operator_action_family(action)] += 1

    x_min, x_max, y_min, y_max = _envelope_bounds_from_state_dict(state)
    thermal_proxy = _build_component_thermal_proxy_from_state(state, metrics=metrics)
    all_temps = np.asarray(list(thermal_proxy.values()) or [30.0], dtype=float)
    t_min = float(np.min(all_temps))
    t_max = float(np.max(all_temps))
    t_span = max(t_max - t_min, 1e-9)

    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    ax_layout = axes[0]
    ax_heat = axes[1]

    # Left: layout evolution annotations.
    env_rect = patches.Rectangle(
        (x_min, y_min),
        max(x_max - x_min, 1.0),
        max(y_max - y_min, 1.0),
        linewidth=1.5,
        edgecolor="#7f8c8d",
        facecolor="#ecf0f1",
        alpha=0.25,
    )
    ax_layout.add_patch(env_rect)

    for comp_id, comp in components.items():
        comp_min = _component_min_corner_from_dict(comp)
        dims = dict(comp.get("dimensions", {}) or {})
        dx = float(dims.get("x", 0.0) or 0.0)
        dy = float(dims.get("y", 0.0) or 0.0)
        category = str(comp.get("category", "unknown") or "unknown").strip().lower()
        color_map = {
            "payload": "#fb8072",
            "power": "#80b1d3",
            "avionics": "#8dd3c7",
            "adcs": "#bebada",
            "thermal": "#fdb462",
            "structure": "#b3de69",
            "propulsion": "#fccde5",
            "comms": "#d9d9d9",
        }
        fill_color = color_map.get(category, "#cbd5e1")
        edge_color = "#2f3640"
        linewidth = 0.9
        if comp_id in moved_ids:
            edge_color = "#c0392b"
            linewidth = 2.2

        rect = patches.Rectangle(
            (comp_min[0], comp_min[1]),
            dx,
            dy,
            linewidth=linewidth,
            edgecolor=edge_color,
            facecolor=fill_color,
            alpha=0.85,
            hatch="//" if comp_id in heatsink_ids else ("\\\\" if comp_id in bracket_ids else None),
        )
        ax_layout.add_patch(rect)

        cx = float(comp.get("position", {}).get("x", comp_min[0] + dx / 2.0) or (comp_min[0] + dx / 2.0))
        cy = float(comp.get("position", {}).get("y", comp_min[1] + dy / 2.0) or (comp_min[1] + dy / 2.0))
        ax_layout.text(cx, cy, comp_id, ha="center", va="center", fontsize=6, color="#1f2937")

        if comp_id in contact_ids:
            ax_layout.plot([comp_min[0] + dx * 0.08], [comp_min[1] + dy * 0.88], marker="o", color="#2980b9", markersize=5)
        if comp_id in coating_ids:
            ax_layout.plot([comp_min[0] + dx * 0.92], [comp_min[1] + dy * 0.88], marker="*", color="#f39c12", markersize=7)

        prev_comp = prev_components.get(comp_id)
        if prev_comp is not None and comp_id in moved_ids:
            prev_pos = dict(prev_comp.get("position", {}) or {})
            x0 = float(prev_pos.get("x", cx) or cx)
            y0 = float(prev_pos.get("y", cy) or cy)
            ax_layout.arrow(
                x0,
                y0,
                cx - x0,
                cy - y0,
                width=0.25,
                head_width=3.5,
                head_length=4.0,
                length_includes_head=True,
                color="#e74c3c",
                alpha=0.8,
            )

    ax_layout.set_xlim(x_min, x_max)
    ax_layout.set_ylim(y_min, y_max)
    ax_layout.set_aspect("equal")
    ax_layout.set_xlabel("X (mm)")
    ax_layout.set_ylabel("Y (mm)")
    ax_layout.grid(True, alpha=0.18)
    ax_layout.set_title("Layout Evolution (Top View)")

    # Right: thermal proxy heatmap + component boxes.
    grid_size = 140
    x_grid = np.linspace(x_min, x_max, grid_size)
    y_grid = np.linspace(y_min, y_max, grid_size)
    X, Y = np.meshgrid(x_grid, y_grid)
    weighted_temp = np.zeros_like(X, dtype=float)
    weights = np.zeros_like(X, dtype=float)
    for comp_id, comp in components.items():
        pos = dict(comp.get("position", {}) or {})
        dims = dict(comp.get("dimensions", {}) or {})
        cx = float(pos.get("x", 0.0) or 0.0)
        cy = float(pos.get("y", 0.0) or 0.0)
        temp = float(thermal_proxy.get(comp_id, t_min))
        sigma = max(float(max(float(dims.get("x", 1.0) or 1.0), float(dims.get("y", 1.0) or 1.0))) * 0.55, 5.0)
        influence = np.exp(-(((X - cx) ** 2 + (Y - cy) ** 2) / (2.0 * sigma ** 2)))
        weighted_temp += influence * temp
        weights += influence
    Z = np.where(weights > 1e-9, weighted_temp / weights, t_min)
    im = ax_heat.contourf(X, Y, Z, levels=24, cmap="inferno")
    cbar = plt.colorbar(im, ax=ax_heat)
    cbar.set_label("Proxy Temp (degC)")

    for comp_id, comp in components.items():
        comp_min = _component_min_corner_from_dict(comp)
        dims = dict(comp.get("dimensions", {}) or {})
        dx = float(dims.get("x", 0.0) or 0.0)
        dy = float(dims.get("y", 0.0) or 0.0)
        edge_color = "#c0392b" if comp_id in moved_ids else "#3c6382"
        rect = patches.Rectangle(
            (comp_min[0], comp_min[1]),
            dx,
            dy,
            linewidth=1.2 if comp_id in moved_ids else 0.8,
            edgecolor=edge_color,
            facecolor="none",
        )
        ax_heat.add_patch(rect)

    ax_heat.set_xlim(x_min, x_max)
    ax_heat.set_ylim(y_min, y_max)
    ax_heat.set_aspect("equal")
    ax_heat.set_xlabel("X (mm)")
    ax_heat.set_ylabel("Y (mm)")
    ax_heat.grid(True, alpha=0.12)
    thermal_source = str(event.get("thermal_source", "") or "")
    ax_heat.set_title(f"Thermal Proxy Heatmap ({thermal_source or 'proxy'})")

    best_cv = metrics.get("best_cv")
    max_temp = metrics.get("max_temp")
    min_clearance = metrics.get("min_clearance")
    summary_title = (
        f"Seq={int(event.get('sequence', 0) or 0):04d} | "
        f"Stage={str(event.get('stage', ''))} | "
        f"Attempt={int(event.get('attempt', 0) or 0)} | "
        f"Status={str(event.get('diagnosis_status', ''))}"
    )
    fig.suptitle(summary_title, fontsize=11)
    fig.text(
        0.01,
        0.02,
        (
            f"Moved={len(moved_ids)} | Heatsink+={len(heatsink_ids)} | Bracket+={len(bracket_ids)} | "
            f"Contacts*={len(contact_ids)} | Coatings*={len(coating_ids)} | "
            f"best_cv={best_cv if best_cv is not None else 'NA'} | "
            f"max_temp={max_temp if max_temp is not None else 'NA'} | "
            f"min_clearance={min_clearance if min_clearance is not None else 'NA'} | "
            f"actions={','.join(operator_actions[:4]) if operator_actions else 'none'}"
        ),
        fontsize=9,
    )
    if operator_family_counts:
        family_order = ["geometry", "thermal", "structural", "power", "mission", "other"]
        family_desc = ", ".join(
            f"{name}={int(operator_family_counts.get(name, 0))}"
            for name in family_order
            if int(operator_family_counts.get(name, 0)) > 0
        )
        if family_desc:
            fig.text(0.01, 0.955, f"operator_families: {family_desc}", fontsize=8)

    plt.tight_layout(rect=[0.0, 0.05, 1.0, 0.95])
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _build_layout_timeline_gif(frame_paths: List[str], output_path: str, duration_ms: int = 650) -> bool:
    """将逐帧 PNG 合成为 GIF（若 Pillow 可用）。"""
    if not frame_paths:
        return False
    try:
        from PIL import Image
    except Exception:
        return False

    images: List[Any] = []
    try:
        for path in frame_paths:
            images.append(Image.open(path))
        images[0].save(
            output_path,
            save_all=True,
            append_images=images[1:],
            duration=int(duration_ms),
            loop=0,
        )
        return True
    except Exception:
        return False
    finally:
        for image in images:
            try:
                image.close()
            except Exception:
                pass


def plot_layout_timeline(experiment_dir: str, viz_dir: str) -> Dict[str, Any]:
    """
    基于 layout_events + snapshots 生成逐迭代布局帧与 GIF。

    Returns:
        产物摘要（frame_count / frames_dir / gif_path / summary_path）
    """
    events_path = os.path.join(experiment_dir, "events", "layout_events.jsonl")
    events = _read_jsonl_safely(events_path)
    records = _load_layout_snapshot_records(experiment_dir)
    if not records:
        return {"frame_count": 0, "frames_dir": "", "gif_path": "", "summary_path": ""}

    frames_dir = os.path.join(viz_dir, "timeline_frames")
    os.makedirs(frames_dir, exist_ok=True)

    frame_paths: List[str] = []
    previous_snapshot: Optional[Dict[str, Any]] = None

    for record in records:
        event = dict(record.get("event", {}) or {})
        snapshot_payload = dict(record.get("snapshot", {}) or {})
        seq = int(event.get("sequence", 0) or 0)
        frame_path = os.path.join(frames_dir, f"frame_{seq:04d}.png")
        _plot_layout_timeline_frame(
            event=event,
            snapshot=snapshot_payload,
            prev_snapshot=previous_snapshot,
            output_path=frame_path,
        )
        frame_paths.append(frame_path)
        previous_snapshot = snapshot_payload

    gif_path = os.path.join(viz_dir, "layout_timeline.gif")
    gif_ok = _build_layout_timeline_gif(frame_paths, gif_path) if frame_paths else False
    if not gif_ok:
        gif_path = ""
    frames_dir_text = serialize_run_path(experiment_dir, frames_dir if frame_paths else "")
    gif_path_text = serialize_run_path(experiment_dir, gif_path)

    summary_path = os.path.join(viz_dir, "layout_timeline_summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("=== Layout Timeline Summary ===\n")
        f.write(f"- Events loaded: {len(events)}\n")
        f.write(f"- Records with valid snapshots: {len(records)}\n")
        f.write(f"- Frames rendered: {len(frame_paths)}\n")
        f.write(f"- Frames dir: {frames_dir_text}\n")
        f.write(f"- GIF: {gif_path_text}\n")

    return {
        "frame_count": int(len(frame_paths)),
        "frames_dir": str(frames_dir if frame_paths else ""),
        "gif_path": str(gif_path),
        "summary_path": str(summary_path),
    }


def plot_layout_evolution_from_snapshots(experiment_dir: str, output_path: str) -> Dict[str, Any]:
    """
    基于 events/snapshots 绘制布局位移图（truth-source）。

    Returns:
        布局位移摘要，用于写入 visualization summary。
    """
    records = _load_layout_snapshot_records(experiment_dir)
    if not records:
        return {
            "record_count": 0,
            "zero_reason": "no_layout_records",
            "moved_count_initial_to_best": 0,
            "max_displacement_initial_to_best": 0.0,
            "mean_displacement_initial_to_best": 0.0,
            "max_component_initial_to_best": "",
            "moved_count_best_to_final": 0,
            "max_displacement_best_to_final": 0.0,
            "frame_transition_count": 0,
            "frame_transition_max": 0.0,
        }

    initial_record = records[0]
    best_record = _select_best_candidate_record(records)
    final_selected_records = [
        item for item in records
        if str(dict(item.get("event", {}) or {}).get("stage", "") or "") == "final_selected"
    ]
    final_record = final_selected_records[-1] if final_selected_records else records[-1]

    initial_state = dict(dict(initial_record.get("snapshot", {}) or {}).get("design_state", {}) or {})
    best_state = dict(dict(best_record.get("snapshot", {}) or {}).get("design_state", {}) or {})
    final_state = dict(dict(final_record.get("snapshot", {}) or {}).get("design_state", {}) or {})

    initial_to_best = _compute_component_displacements(initial_state, best_state)
    best_to_final = _compute_component_displacements(best_state, final_state)
    frame_stats = _build_frame_transition_stats(records)

    initial_dist = np.asarray([float(item.get("dist", 0.0)) for item in initial_to_best], dtype=float)
    best_final_dist = np.asarray([float(item.get("dist", 0.0)) for item in best_to_final], dtype=float)
    frame_max = np.asarray([float(item.get("max_dist", 0.0)) for item in frame_stats], dtype=float)

    moved_initial_to_best = int(np.sum(initial_dist > 1e-6)) if initial_dist.size > 0 else 0
    moved_best_to_final = int(np.sum(best_final_dist > 1e-6)) if best_final_dist.size > 0 else 0
    max_disp_initial_to_best = float(np.max(initial_dist)) if initial_dist.size > 0 else 0.0
    mean_disp_initial_to_best = float(np.mean(initial_dist)) if initial_dist.size > 0 else 0.0
    max_disp_best_to_final = float(np.max(best_final_dist)) if best_final_dist.size > 0 else 0.0
    frame_transition_max = float(np.max(frame_max)) if frame_max.size > 0 else 0.0

    max_component = ""
    if initial_to_best:
        max_row = max(initial_to_best, key=lambda item: float(item.get("dist", 0.0)))
        max_component = str(max_row.get("component_id", "") or "")

    zero_reason = _infer_zero_movement_reason(records, initial_to_best, frame_stats)

    sorted_initial = sorted(initial_to_best, key=lambda item: float(item.get("dist", 0.0)), reverse=True)
    top_dist_rows = sorted_initial[: min(len(sorted_initial), 20)]
    top_axis_rows = sorted_initial[: min(len(sorted_initial), 12)]

    fig, axes = plt.subplots(1, 3, figsize=(20, 6))

    ax0 = axes[0]
    if top_dist_rows:
        labels = [str(item.get("component_id", "")) for item in top_dist_rows]
        values = np.asarray([float(item.get("dist", 0.0)) for item in top_dist_rows], dtype=float)
        colors = ["#d35400" if value > 1e-6 else "#95a5a6" for value in values]
        bars = ax0.bar(labels, values, color=colors, alpha=0.85)
        for bar, value in zip(bars, values):
            ax0.text(
                bar.get_x() + bar.get_width() / 2.0,
                float(value),
                f"{float(value):.1f}",
                ha="center",
                va="bottom",
                fontsize=7,
            )
        ax0.tick_params(axis="x", rotation=45)
    else:
        ax0.text(0.5, 0.5, "No common components", transform=ax0.transAxes, ha="center", va="center")
    ax0.set_ylabel("Displacement (mm)")
    ax0.set_title("Initial -> Best Candidate |d|")
    ax0.grid(True, axis="y", alpha=0.25)

    ax1 = axes[1]
    if top_axis_rows:
        labels = [str(item.get("component_id", "")) for item in top_axis_rows]
        x = np.arange(len(labels), dtype=float)
        width = 0.24
        dx = np.asarray([float(item.get("dx", 0.0)) for item in top_axis_rows], dtype=float)
        dy = np.asarray([float(item.get("dy", 0.0)) for item in top_axis_rows], dtype=float)
        dz = np.asarray([float(item.get("dz", 0.0)) for item in top_axis_rows], dtype=float)
        ax1.bar(x - width, dx, width=width, label="dx", color="#1f77b4", alpha=0.85)
        ax1.bar(x, dy, width=width, label="dy", color="#2ca02c", alpha=0.85)
        ax1.bar(x + width, dz, width=width, label="dz", color="#d62728", alpha=0.85)
        ax1.axhline(0.0, color="#7f8c8d", linewidth=1.0)
        ax1.set_xticks(x)
        ax1.set_xticklabels(labels, rotation=45, ha="right")
        ax1.legend(loc="best", fontsize=8)
    else:
        ax1.text(0.5, 0.5, "No axis displacement data", transform=ax1.transAxes, ha="center", va="center")
    ax1.set_ylabel("Axis Displacement (mm)")
    ax1.set_title("Axis Shift (dx/dy/dz)")
    ax1.grid(True, axis="y", alpha=0.25)

    ax2 = axes[2]
    if frame_stats:
        x = np.arange(1, len(frame_stats) + 1, dtype=float)
        y = np.asarray([float(item.get("max_dist", 0.0)) for item in frame_stats], dtype=float)
        moved = np.asarray([int(item.get("moved_count", 0)) for item in frame_stats], dtype=float)
        ax2.plot(x, y, marker="o", linewidth=1.8, color="#8e44ad", label="max |d|")
        ax2.set_xlabel("Transition Index")
        ax2.set_ylabel("Max Displacement (mm)", color="#8e44ad")
        ax2.tick_params(axis="y", labelcolor="#8e44ad")
        ax2.grid(True, alpha=0.25)
        ax2b = ax2.twinx()
        ax2b.plot(x, moved, marker="s", linewidth=1.5, color="#16a085", label="moved count")
        ax2b.set_ylabel("Moved Components", color="#16a085")
        ax2b.tick_params(axis="y", labelcolor="#16a085")
        handles_a, labels_a = ax2.get_legend_handles_labels()
        handles_b, labels_b = ax2b.get_legend_handles_labels()
        ax2.legend(handles_a + handles_b, labels_a + labels_b, loc="best", fontsize=8)
    else:
        ax2.text(0.5, 0.5, "No frame transitions", transform=ax2.transAxes, ha="center", va="center")
    ax2.set_title("Frame-to-Frame Motion")

    initial_event = dict(initial_record.get("event", {}) or {})
    best_event = dict(best_record.get("event", {}) or {})
    final_event = dict(final_record.get("event", {}) or {})
    fig.suptitle(
        "Layout Evolution (events/snapshots truth-source)\n"
        f"initial_seq={int(initial_event.get('sequence', 0) or 0)}, "
        f"best_seq={int(best_event.get('sequence', 0) or 0)}, "
        f"final_seq={int(final_event.get('sequence', 0) or 0)}",
        fontsize=12,
    )
    summary_line = (
        f"Initial->Best moved={moved_initial_to_best}/{len(initial_to_best)} | "
        f"mean={mean_disp_initial_to_best:.3f} mm | "
        f"max={max_disp_initial_to_best:.3f} mm ({max_component or 'NA'}) | "
        f"Best->Final moved={moved_best_to_final}/{len(best_to_final)} "
        f"(max={max_disp_best_to_final:.3f} mm) | "
        f"FrameMax={frame_transition_max:.3f} mm"
    )
    if zero_reason:
        summary_line = summary_line + f" | zero_reason={zero_reason}"
    fig.text(0.01, 0.01, summary_line, fontsize=9)

    plt.tight_layout(rect=[0.0, 0.05, 1.0, 0.92])
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return {
        "record_count": int(len(records)),
        "reference_stage": str(initial_event.get("stage", "") or ""),
        "best_stage": str(best_event.get("stage", "") or ""),
        "final_stage": str(final_event.get("stage", "") or ""),
        "moved_count_initial_to_best": int(moved_initial_to_best),
        "max_displacement_initial_to_best": float(max_disp_initial_to_best),
        "mean_displacement_initial_to_best": float(mean_disp_initial_to_best),
        "max_component_initial_to_best": str(max_component),
        "moved_count_best_to_final": int(moved_best_to_final),
        "max_displacement_best_to_final": float(max_disp_best_to_final),
        "frame_transition_count": int(len(frame_stats)),
        "frame_transition_max": float(frame_transition_max),
        "zero_reason": str(zero_reason),
    }


def plot_mass_storyboard(tables_dir: str, output_path: str) -> None:
    """
    绘制 mass 单次运行的四宫格故事板（基于 tables/*.csv）。
    """
    try:
        logger.info(f"生成 mass 故事板: {output_path}")

        attempts = _read_csv_safely(os.path.join(tables_dir, "attempts.csv"))
        generations = _read_csv_safely(os.path.join(tables_dir, "generations.csv"))
        policies = _read_csv_safely(os.path.join(tables_dir, "policy_tuning.csv"))
        physics = _read_csv_safely(os.path.join(tables_dir, "physics_budget.csv"))
        phases = _read_csv_safely(os.path.join(tables_dir, "phases.csv"))
        vop_rounds = _read_csv_safely(os.path.join(tables_dir, "vop_rounds.csv"))
        release_audit = _read_csv_safely(os.path.join(tables_dir, "release_audit.csv"))
        candidates = _read_csv_safely(os.path.join(tables_dir, "candidates.csv"))
        layouts = _read_csv_safely(os.path.join(tables_dir, "layout_timeline.csv"))
        layout_deltas = _read_csv_safely(os.path.join(tables_dir, "layout_deltas.csv"))

        if (
            len(attempts) == 0 and len(generations) == 0 and len(policies) == 0
            and len(physics) == 0 and len(phases) == 0 and len(vop_rounds) == 0
            and len(release_audit) == 0 and len(candidates) == 0
            and len(layouts) == 0 and len(layout_deltas) == 0
        ):
            logger.warning("mass 故事板跳过：tables 为空")
            return

        fig, axes = plt.subplots(2, 2, figsize=(16, 10))

        # 1) 代际收敛：best_cv + feasible_ratio
        ax = axes[0, 0]
        if len(generations) > 0 and "generation" in generations.columns:
            gen = pd.to_numeric(generations["generation"], errors="coerce").fillna(0.0)
            panel = pd.DataFrame({"generation": gen})
            panel["best_cv"] = (
                pd.to_numeric(generations.get("best_cv"), errors="coerce")
                if "best_cv" in generations.columns
                else np.nan
            )
            panel["feasible_ratio"] = (
                pd.to_numeric(generations.get("feasible_ratio"), errors="coerce")
                if "feasible_ratio" in generations.columns
                else np.nan
            )
            panel = panel.sort_values("generation")
            grouped = panel.groupby("generation", as_index=False).agg(
                best_cv=("best_cv", "min"),
                feasible_ratio=("feasible_ratio", "max"),
            )
            x = grouped["generation"].to_numpy(dtype=float)
            ax.plot(x, grouped["best_cv"].to_numpy(dtype=float), "r-o", linewidth=1.8, label="best_cv(min)")
            ax.set_xlabel("Generation")
            ax.set_ylabel("Best CV", color="r")
            ax.tick_params(axis="y", labelcolor="r")
            ax.grid(True, alpha=0.25)
            ax2 = ax.twinx()
            ax2.plot(
                x,
                grouped["feasible_ratio"].fillna(0.0).to_numpy(dtype=float),
                "b-s",
                linewidth=1.6,
                label="feasible_ratio(max)",
            )
            ax2.set_ylabel("Feasible Ratio", color="b")
            ax2.set_ylim(-0.05, 1.05)
            ax2.tick_params(axis="y", labelcolor="b")
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, loc="best", fontsize=8)
            ax.set_title("Generation Convergence")
        else:
            ax.text(0.5, 0.5, "No generation table data", transform=ax.transAxes,
                    ha="center", va="center", fontsize=10, color="gray")
            ax.set_title("Generation Convergence")

        # 2) attempt 级求解趋势
        ax = axes[0, 1]
        if len(attempts) > 0 and "attempt" in attempts.columns:
            x = pd.to_numeric(attempts["attempt"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
            best_cv = _to_float_series(attempts, "best_cv")
            aocc_cv = _to_float_series(attempts, "aocc_cv")
            if best_cv is not None:
                ax.plot(x, np.nan_to_num(best_cv, nan=np.nan), "r-o", linewidth=1.8, label="best_cv")
            if aocc_cv is not None:
                ax.plot(x, np.nan_to_num(aocc_cv, nan=np.nan), "k--^", linewidth=1.6, label="aocc_cv")

            best_mask = _parse_bool_series(attempts, "is_best_attempt")
            if best_mask.size == len(x):
                ax.scatter(
                    x[best_mask],
                    np.nan_to_num(best_cv[best_mask], nan=0.0) if best_cv is not None else np.zeros(np.sum(best_mask)),
                    color="#d62728",
                    s=80,
                    marker="*",
                    label="best_attempt",
                    zorder=4,
                )

            ax.set_xlabel("Attempt")
            ax.set_ylabel("CV")
            ax.set_title("Attempt Diagnostics")
            ax.grid(True, alpha=0.25)
            ax.legend(loc="best", fontsize=8)
        else:
            ax.text(0.5, 0.5, "No attempt table data", transform=ax.transAxes,
                    ha="center", va="center", fontsize=10, color="gray")
            ax.set_title("Attempt Diagnostics")

        # 3) 策略/物理调度事件
        ax = axes[1, 0]
        policy_attempt = (
            pd.to_numeric(policies["attempt"], errors="coerce").fillna(0).astype(int)
            if len(policies) > 0 and "attempt" in policies.columns
            else pd.Series([], dtype=int)
        )
        physics_attempt = (
            pd.to_numeric(physics["attempt"], errors="coerce").fillna(0).astype(int)
            if len(physics) > 0 and "attempt" in physics.columns
            else pd.Series([], dtype=int)
        )
        attempt_ids = sorted(set(policy_attempt.tolist()) | set(physics_attempt.tolist()))
        if attempt_ids:
            x = np.asarray(attempt_ids, dtype=float)
            policy_counts = np.asarray([int((policy_attempt == item).sum()) for item in attempt_ids], dtype=float)
            applied = _parse_bool_series(policies, "applied")
            if len(policies) > 0 and applied.size == len(policies):
                policy_applied = np.asarray([
                    int(np.sum(applied[policy_attempt.to_numpy(dtype=int) == item])) for item in attempt_ids
                ], dtype=float)
            else:
                policy_applied = np.zeros_like(policy_counts)
            physics_counts = np.asarray([int((physics_attempt == item).sum()) for item in attempt_ids], dtype=float)

            ax.bar(x - 0.25, policy_counts, width=0.22, color="#4c72b0", alpha=0.75, label="policy_events")
            ax.bar(x, policy_applied, width=0.22, color="#55a868", alpha=0.85, label="policy_applied")
            ax.bar(x + 0.25, physics_counts, width=0.22, color="#dd8452", alpha=0.75, label="physics_events")
            ax.set_xlabel("Attempt")
            ax.set_ylabel("Event Count")
            ax.set_title("Runtime Policy & Physics Events")
            ax.grid(True, axis="y", alpha=0.25)
            ax.legend(loc="best", fontsize=8)
        else:
            ax.text(0.5, 0.5, "No policy/physics events", transform=ax.transAxes,
                    ha="center", va="center", fontsize=10, color="gray")
            ax.set_title("Runtime Policy & Physics Events")
        vop_round_overview = _collect_vop_round_overview(vop_rounds, policies, phases)
        if vop_round_overview is not None:
            overview_text = (
                f"vop_rounds={int(vop_round_overview['round_count'])} "
                f"joined={int(vop_round_overview['joined_keys'])} "
                f"latest={str(vop_round_overview['latest_round_key'])}"
            )
            ax.text(
                0.02,
                0.88,
                overview_text,
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=8,
                color="#1f2937",
                bbox={"boxstyle": "round", "facecolor": "#eef6ff", "alpha": 0.8, "edgecolor": "#b6d4fe"},
            )
        if len(release_audit) > 0:
            audit_row = release_audit.tail(1).iloc[0]
            audit_status = str(audit_row.get("final_audit_status", "") or "").strip() or "n/a"
            first_feasible = "n/a"
            first_feasible_raw = audit_row.get("first_feasible_eval")
            if pd.notna(first_feasible_raw) and str(first_feasible_raw).strip():
                first_feasible = str(first_feasible_raw).strip()
            comsol_calls = "n/a"
            comsol_calls_raw = audit_row.get("comsol_calls_to_first_feasible")
            if pd.notna(comsol_calls_raw) and str(comsol_calls_raw).strip():
                comsol_calls = str(comsol_calls_raw).strip()
            ax.text(
                0.02,
                0.76,
                f"audit={audit_status}\nfirst_feasible={first_feasible}, comsol_calls={comsol_calls}",
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=8,
                color="#1f2937",
                bbox={"boxstyle": "round", "facecolor": "#f6f8fa", "alpha": 0.78, "edgecolor": "#d0d7de"},
            )
        if len(attempts) > 0 and "operator_actions" in attempts.columns:
            family_counts: Dict[str, int] = defaultdict(int)
            for raw_actions in attempts["operator_actions"].tolist():
                for action in _parse_operator_actions(raw_actions):
                    family_counts[_operator_action_family(action)] += 1
            if family_counts:
                family_order = ["geometry", "thermal", "structural", "power", "mission", "other"]
                family_desc = ", ".join(
                    f"{name}:{int(family_counts.get(name, 0))}"
                    for name in family_order
                    if int(family_counts.get(name, 0)) > 0
                )
                if family_desc:
                    ax.text(
                        0.02,
                        0.98,
                        f"operator_families {family_desc}",
                        transform=ax.transAxes,
                        ha="left",
                        va="top",
                        fontsize=8,
                        color="#1f2937",
                        bbox={"boxstyle": "round", "facecolor": "#f8f9fa", "alpha": 0.75, "edgecolor": "#d0d7de"},
                    )

        # 4) 事件漏斗总览
        ax = axes[1, 1]
        labels = ["phase", "attempt", "generation", "policy", "physics", "candidate", "layout", "layout_delta"]
        values = [
            int(len(phases)),
            int(len(attempts)),
            int(len(generations)),
            int(len(policies)),
            int(len(physics)),
            int(len(candidates)),
            int(len(layouts)),
            int(len(layout_deltas)),
        ]
        bars = ax.bar(
            labels,
            values,
            color=["#8172b2", "#4c72b0", "#c44e52", "#55a868", "#dd8452", "#937860", "#64b5cd", "#2a9d8f"],
        )
        ax.set_title("Observability Event Funnel")
        ax.set_ylabel("Rows")
        ax.grid(True, axis="y", alpha=0.25)
        ax.tick_params(axis="x", rotation=15)
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                float(value) + 0.05,
                str(int(value)),
                ha="center",
                va="bottom",
                fontsize=8,
            )

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()
        logger.info("mass 故事板生成成功")

    except Exception as e:
        error_msg = f"生成 mass 故事板失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise VisualizationError(error_msg) from e


def plot_layout_evolution(initial_state, final_state, output_path: str):
    """
    绘制布局演化图：左侧 XY 位移箭头，右侧组件位移量条形图。
    """
    try:
        logger.info(f"生成布局演化图: {output_path}")

        init_map = {c.id: c for c in initial_state.components}
        final_map = {c.id: c for c in final_state.components}
        common_ids = sorted(set(init_map.keys()) & set(final_map.keys()))
        if not common_ids:
            logger.warning("布局演化图跳过：初始与最终状态无公共组件")
            return

        disp_data = []
        for cid in common_ids:
            c0 = init_map[cid]
            c1 = final_map[cid]
            dx = float(c1.position.x - c0.position.x)
            dy = float(c1.position.y - c0.position.y)
            dz = float(c1.position.z - c0.position.z)
            dist = float(np.sqrt(dx * dx + dy * dy + dz * dz))
            disp_data.append((cid, c0.position.x, c0.position.y, dx, dy, dist))

        fig, axes = plt.subplots(1, 2, figsize=(14, 6))

        # 左图：XY 平面箭头
        ax = axes[0]
        for cid, x0, y0, dx, dy, dist in disp_data:
            ax.scatter([x0], [y0], color="tab:blue", s=20)
            ax.arrow(
                x0, y0, dx, dy,
                width=0.2, head_width=3.0, head_length=4.0,
                length_includes_head=True, color="tab:red", alpha=0.7
            )
            if dist > 1e-6:
                ax.text(x0 + dx, y0 + dy, cid, fontsize=8)

        ax.set_title("Layout Shift (XY)")
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        ax.grid(True, alpha=0.3)
        ax.axis("equal")

        # 右图：位移条形图
        ax2 = axes[1]
        disp_sorted = sorted(disp_data, key=lambda x: x[5], reverse=True)
        labels = [item[0] for item in disp_sorted]
        values = [item[5] for item in disp_sorted]
        bars = ax2.bar(labels, values, color="tab:orange", alpha=0.8)
        ax2.set_title("Component Displacement Magnitude")
        ax2.set_ylabel("Displacement (mm)")
        ax2.tick_params(axis='x', rotation=45)
        ax2.grid(True, axis='y', alpha=0.3)

        for bar, v in zip(bars, values):
            ax2.text(bar.get_x() + bar.get_width() / 2.0, bar.get_height(), f"{v:.1f}",
                     ha="center", va="bottom", fontsize=8)

        if np.max(values) <= 1e-6:
            ax2.text(0.5, 0.5, "No component movement detected",
                     transform=ax2.transAxes, ha="center", va="center",
                     fontsize=11, color="gray")

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        logger.info("布局演化图生成成功")

    except Exception as e:
        error_msg = f"生成布局演化图失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise VisualizationError(error_msg) from e


def plot_3d_layout(design_state, output_path: str):
    """
    绘制3D布局图

    Args:
        design_state: 设计状态
        output_path: 输出文件路径
    """
    try:
        logger.info(f"生成3D布局图: {output_path}")

        fig = plt.figure(figsize=(12, 10))
        ax = fig.add_subplot(111, projection='3d')

        # 绘制外壳
        if design_state.envelope:
            env_pos, env_dims = _envelope_bounds(design_state)
            draw_box(
                ax,
                env_pos,
                env_dims,
                color='lightgray',
                alpha=0.08,
                label='Envelope'
            )

        # 绘制组件
        colors = ['red', 'blue', 'green', 'orange', 'purple', 'brown']
        x_points: List[float] = []
        y_points: List[float] = []
        z_points: List[float] = []

        for i, comp in enumerate(design_state.components):
            pos = _component_min_corner(comp)
            dims = [comp.dimensions.x, comp.dimensions.y, comp.dimensions.z]
            color = colors[i % len(colors)]
            draw_box(ax, pos, dims, color=color, alpha=0.6, label=comp.id)
            x_points.extend([pos[0], pos[0] + dims[0]])
            y_points.extend([pos[1], pos[1] + dims[1]])
            z_points.extend([pos[2], pos[2] + dims[2]])

        if design_state.envelope:
            env_pos, env_dims = _envelope_bounds(design_state)
            x_points.extend([env_pos[0], env_pos[0] + env_dims[0]])
            y_points.extend([env_pos[1], env_pos[1] + env_dims[1]])
            z_points.extend([env_pos[2], env_pos[2] + env_dims[2]])

        if x_points and y_points and z_points:
            x_min, x_max = min(x_points), max(x_points)
            y_min, y_max = min(y_points), max(y_points)
            z_min, z_max = min(z_points), max(z_points)

            dx = max(x_max - x_min, 1.0)
            dy = max(y_max - y_min, 1.0)
            dz = max(z_max - z_min, 1.0)
            span = max(dx, dy, dz)

            cx = (x_min + x_max) / 2.0
            cy = (y_min + y_max) / 2.0
            cz = (z_min + z_max) / 2.0

            margin = span * 0.08
            half = span / 2.0 + margin
            ax.set_xlim(cx - half, cx + half)
            ax.set_ylim(cy - half, cy + half)
            ax.set_zlim(cz - half, cz + half)

        # 设置坐标轴
        ax.set_xlabel('X (mm)')
        ax.set_ylabel('Y (mm)')
        ax.set_zlabel('Z (mm)')
        ax.set_title('3D Component Layout')

        # 设置视角
        ax.view_init(elev=20, azim=45)

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        logger.info(f"3D布局图生成成功")

    except Exception as e:
        error_msg = f"生成3D布局图失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise VisualizationError(error_msg) from e


def draw_box(ax, position, dimensions, color='blue', alpha=0.5, label=None):
    """
    在3D坐标系中绘制立方体

    Args:
        ax: 3D坐标轴
        position: 位置 [x, y, z]
        dimensions: 尺寸 [dx, dy, dz]
        color: 颜色
        alpha: 透明度
        label: 标签
    """
    x, y, z = position
    dx, dy, dz = dimensions

    # 定义立方体的8个顶点
    vertices = [
        [x, y, z],
        [x + dx, y, z],
        [x + dx, y + dy, z],
        [x, y + dy, z],
        [x, y, z + dz],
        [x + dx, y, z + dz],
        [x + dx, y + dy, z + dz],
        [x, y + dy, z + dz]
    ]

    # 定义立方体的6个面
    faces = [
        [vertices[0], vertices[1], vertices[5], vertices[4]],  # 前
        [vertices[2], vertices[3], vertices[7], vertices[6]],  # 后
        [vertices[0], vertices[3], vertices[7], vertices[4]],  # 左
        [vertices[1], vertices[2], vertices[6], vertices[5]],  # 右
        [vertices[0], vertices[1], vertices[2], vertices[3]],  # 底
        [vertices[4], vertices[5], vertices[6], vertices[7]]   # 顶
    ]

    # 创建3D多边形集合
    poly = Poly3DCollection(faces, alpha=alpha, facecolor=color,
                           edgecolor='black', linewidth=0.5)
    ax.add_collection3d(poly)

    # 添加标签（在中心位置）
    if label:
        cx = x + dx / 2
        cy = y + dy / 2
        cz = z + dz / 2
        ax.text(cx, cy, cz, label, fontsize=8, ha='center')


def plot_evolution_trace(csv_path: str, output_path: str):
    """
    绘制演化轨迹图

    Args:
        csv_path: CSV文件路径
        output_path: 输出文件路径
    """
    try:
        logger.info(f"生成演化轨迹图: {output_path}")
        df = pd.read_csv(csv_path)

        if len(df) == 0:
            logger.warning("演化轨迹图跳过：没有数据可绘制")
            return

        if "iteration" not in df.columns:
            df["iteration"] = np.arange(1, len(df) + 1, dtype=float)

        iterations = _to_float_series(df, "iteration")
        if iterations is None:
            iterations = np.arange(1, len(df) + 1, dtype=float)

        # 对齐为连续索引，便于显示
        x_ticks = np.array(iterations, dtype=float)

        fig, axes = plt.subplots(2, 2, figsize=(16, 11))

        # 1) 惩罚分与违规数（主图）
        ax = axes[0, 0]
        penalty_total = _to_float_series(df, "penalty_score")
        num_violations = _to_float_series(df, "num_violations")

        if penalty_total is not None:
            penalty_total = np.nan_to_num(penalty_total, nan=0.0)
            ax.plot(x_ticks, penalty_total, color="black", marker="o", linewidth=2.2, label="Penalty Total")

            breakdown_specs = [
                ("penalty_violation", "Violation", "#d62728"),
                ("penalty_temp", "Temp", "#ff7f0e"),
                ("penalty_clearance", "Clearance", "#1f77b4"),
                ("penalty_cg", "CG", "#9467bd"),
                ("penalty_collision", "Collision", "#8c564b"),
            ]
            stack_values = []
            stack_labels = []
            stack_colors = []
            for col, label, color in breakdown_specs:
                values = _to_float_series(df, col)
                if values is None:
                    continue
                values = np.nan_to_num(values, nan=0.0)
                if np.any(np.abs(values) > 1e-12):
                    stack_values.append(values)
                    stack_labels.append(label)
                    stack_colors.append(color)

            if stack_values:
                ax.stackplot(
                    x_ticks,
                    *stack_values,
                    labels=stack_labels,
                    colors=stack_colors,
                    alpha=0.18
                )

            if _is_constant_series(penalty_total):
                ax.text(
                    0.03,
                    0.92,
                    "Penalty nearly constant",
                    transform=ax.transAxes,
                    fontsize=10,
                    color="gray",
                    bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8),
                )
        else:
            max_temp = _to_float_series(df, "max_temp")
            if max_temp is not None:
                ax.plot(x_ticks, max_temp, "r-o", linewidth=2.0, label="Max Temp (Fallback)")

        ax.set_title("Penalty Decomposition")
        ax.set_xlabel("Iteration")
        ax.set_ylabel("Penalty Score")
        ax.grid(True, alpha=0.25)

        ax_r = ax.twinx()
        if num_violations is not None:
            num_violations = np.nan_to_num(num_violations, nan=0.0)
            num_violations = np.clip(num_violations, a_min=0.0, a_max=None)
            v_color = "#d62728"

            # 违规数量主轨迹：粗线 + 阶梯填充，视觉优先级提升
            ax_r.step(
                x_ticks,
                num_violations,
                where="post",
                color=v_color,
                linewidth=3.0,
                linestyle="-",
                label="Violations"
            )
            ax_r.fill_between(
                x_ticks,
                0.0,
                num_violations,
                step="post",
                color=v_color,
                alpha=0.18,
                zorder=4
            )

            # 每轮违规点：随违规数量增大而加粗
            marker_sizes = 46.0 + 34.0 * num_violations
            ax_r.scatter(
                x_ticks,
                num_violations,
                s=marker_sizes,
                color=v_color,
                edgecolors="white",
                linewidths=0.8,
                zorder=20
            )

            # 末轮星标和标注
            final_v = float(num_violations[-1])
            ax_r.scatter(
                [x_ticks[-1]],
                [final_v],
                s=220,
                marker="*",
                color="gold",
                edgecolors=v_color,
                linewidths=1.4,
                zorder=25,
                label=f"Final Violations={int(round(final_v))}"
            )
            ax_r.annotate(
                f"final={int(round(final_v))}",
                xy=(x_ticks[-1], final_v),
                xytext=(8, 8),
                textcoords="offset points",
                fontsize=9,
                color=v_color,
                fontweight="bold"
            )

            # 首次清零高亮：只在从>0下降到0时标注
            first_clear_idx = None
            for i in range(1, len(num_violations)):
                if num_violations[i] <= 0.0 and num_violations[i - 1] > 0.0:
                    first_clear_idx = i
                    break
            if first_clear_idx is not None:
                clear_x = float(x_ticks[first_clear_idx])
                ax.axvline(
                    x=clear_x,
                    color="#2ca02c",
                    linestyle=":",
                    linewidth=2.0,
                    alpha=0.95,
                    zorder=3
                )
                ax_r.scatter(
                    [clear_x],
                    [0.0],
                    s=90,
                    marker="D",
                    color="#2ca02c",
                    edgecolors="white",
                    linewidths=0.8,
                    zorder=24,
                    label=f"Cleared @ iter {int(round(clear_x))}"
                )
                ax_r.annotate(
                    f"cleared@{int(round(clear_x))}",
                    xy=(clear_x, 0.0),
                    xytext=(6, 12),
                    textcoords="offset points",
                    fontsize=9,
                    color="#2ca02c",
                    fontweight="bold"
                )

            y_max_v = max(float(np.max(num_violations)), 1.0)
            ax_r.set_ylim(0.0, y_max_v + 0.6)
            ax_r.set_ylabel("Violations", color=v_color, fontweight="bold")
            ax_r.tick_params(axis='y', colors=v_color, width=1.5)
            ax_r.spines["right"].set_color(v_color)
            ax_r.spines["right"].set_linewidth(2.0)

            if _is_constant_series(num_violations):
                ax_r.text(
                    0.03,
                    0.82,
                    "Violations nearly constant",
                    transform=ax_r.transAxes,
                    fontsize=9,
                    color=v_color,
                    bbox=dict(boxstyle="round", fc="white", ec=v_color, alpha=0.85),
                )

        handles_l, labels_l = ax.get_legend_handles_labels()
        handles_r, labels_r = ax_r.get_legend_handles_labels()
        if handles_l or handles_r:
            ax.legend(handles_l + handles_r, labels_l + labels_r, loc="upper right", fontsize=8)

        # 2) 单轮有效性分数
        ax = axes[0, 1]
        effectiveness = _to_float_series(df, "effectiveness_score")
        if effectiveness is not None:
            effectiveness = np.nan_to_num(effectiveness, nan=0.0)
            colors = ["#2ca02c" if val >= 0 else "#d62728" for val in effectiveness]
            ax.bar(x_ticks, effectiveness, color=colors, alpha=0.75, label="Effectiveness")
            ax.plot(x_ticks, effectiveness, color="black", linewidth=1.0, alpha=0.6)
            ax.axhline(0.0, color="gray", linewidth=1.0, linestyle="--")
            if _is_constant_series(effectiveness):
                ax.text(
                    0.03,
                    0.92,
                    "Effectiveness nearly constant",
                    transform=ax.transAxes,
                    fontsize=10,
                    color="gray",
                    bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8),
                )
        else:
            solver_cost = _to_float_series(df, "solver_cost")
            if solver_cost is not None:
                solver_cost = np.nan_to_num(solver_cost, nan=0.0)
                ax.plot(x_ticks, solver_cost, "m-o", linewidth=2.0, label="Solver Cost (Fallback)")

        ax.set_title("Iteration Effectiveness")
        ax.set_xlabel("Iteration")
        ax.set_ylabel("Score (-100~100)")
        ax.grid(True, alpha=0.25)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(loc="best", fontsize=8)

        # 3) 关键连续指标（温度 / 间隙 / 质心偏移）
        ax = axes[1, 0]
        max_temp = _to_float_series(df, "max_temp")
        min_clearance = _to_float_series(df, "min_clearance")
        cg_offset = _to_float_series(df, "cg_offset")

        if max_temp is not None:
            max_temp = np.nan_to_num(max_temp, nan=0.0)
            ax.plot(x_ticks, max_temp, "r-o", linewidth=2.0, label="Max Temp (degC)")

        ax2 = ax.twinx()
        if min_clearance is not None:
            min_clearance = np.nan_to_num(min_clearance, nan=0.0)
            ax2.plot(x_ticks, min_clearance, "b-s", linewidth=1.8, label="Min Clearance (mm)")
        if cg_offset is not None:
            cg_offset = np.nan_to_num(cg_offset, nan=0.0)
            ax2.plot(x_ticks, cg_offset, color="#6a3d9a", marker="^", linewidth=1.8, label="CG Offset (mm)")

        # 恒定序列提示（避免“看起来没变化”）
        constant_flags = []
        if max_temp is not None:
            constant_flags.append(("max_temp", _is_constant_series(max_temp)))
        if min_clearance is not None:
            constant_flags.append(("min_clearance", _is_constant_series(min_clearance)))
        if cg_offset is not None:
            constant_flags.append(("cg_offset", _is_constant_series(cg_offset)))
        if constant_flags and all(flag for _, flag in constant_flags):
            ax.text(
                0.03,
                0.92,
                "Key metrics nearly constant",
                transform=ax.transAxes,
                fontsize=10,
                color="gray",
                bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8),
            )

        ax.set_title("Thermal & Geometry Key Metrics")
        ax.set_xlabel("Iteration")
        ax.set_ylabel("Temperature (degC)", color="r")
        ax2.set_ylabel("Clearance / CG Offset (mm)", color="b")
        ax.grid(True, alpha=0.25)
        handles_l, labels_l = ax.get_legend_handles_labels()
        handles_r, labels_r = ax2.get_legend_handles_labels()
        if handles_l or handles_r:
            ax.legend(handles_l + handles_r, labels_l + labels_r, loc="best", fontsize=8)

        # 4) 增量指标（每轮变化）
        ax = axes[1, 1]
        delta_specs = [
            ("delta_penalty", "Delta Penalty", "#d62728"),
            ("delta_cg_offset", "Delta CG Offset", "#6a3d9a"),
            ("delta_max_temp", "Delta Max Temp", "#ff7f0e"),
            ("delta_min_clearance", "Delta Min Clearance", "#1f77b4"),
        ]

        plotted = 0
        delta_series_for_constant_check: List[np.ndarray] = []
        for col, label, color in delta_specs:
            values = _to_float_series(df, col)
            if values is None:
                if col == "delta_penalty" and penalty_total is not None:
                    values = np.insert(np.diff(penalty_total), 0, 0.0)
                else:
                    continue
            values = np.nan_to_num(values, nan=0.0)
            ax.plot(x_ticks, values, marker="o", linewidth=1.8, color=color, label=label)
            delta_series_for_constant_check.append(values)
            plotted += 1

        ax.axhline(0.0, color="gray", linestyle="--", linewidth=1.0)
        ax.set_title("Per-Iteration Deltas")
        ax.set_xlabel("Iteration")
        ax.set_ylabel("Delta Value")
        ax.grid(True, alpha=0.25)

        if plotted == 0:
            ax.text(0.5, 0.5, "No delta metrics available", transform=ax.transAxes,
                    ha="center", va="center", fontsize=10, color="gray")
        else:
            if delta_series_for_constant_check and all(
                _is_constant_series(series) for series in delta_series_for_constant_check
            ):
                ax.text(
                    0.03,
                    0.92,
                    "Delta metrics near zero",
                    transform=ax.transAxes,
                    fontsize=10,
                    color="gray",
                    bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8),
                )
            ax.legend(loc="best", fontsize=8)

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        logger.info("演化轨迹图生成成功")

    except Exception as e:
        error_msg = f"生成演化轨迹图失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise VisualizationError(error_msg) from e


def plot_mass_trace(csv_path: str, output_path: str):
    """
    绘制 mass 尝试级轨迹图。
    """
    try:
        logger.info(f"生成 mass 轨迹图: {output_path}")
        df = pd.read_csv(csv_path)
        if len(df) == 0:
            logger.warning("mass 轨迹图跳过：没有数据可绘制")
            return

        if "attempt" not in df.columns:
            df["attempt"] = np.arange(1, len(df) + 1, dtype=float)
        attempts = _to_float_series(df, "attempt")
        if attempts is None:
            attempts = np.arange(1, len(df) + 1, dtype=float)
        x_ticks = np.asarray(attempts, dtype=float)

        best_cv = _to_float_series(df, "best_cv")
        aocc_cv = _to_float_series(df, "aocc_cv")
        aocc_obj = _to_float_series(df, "aocc_objective")
        score = _to_float_series(df, "score")
        solver_cost = _to_float_series(df, "solver_cost")

        fig, axes = plt.subplots(2, 2, figsize=(16, 11))

        # 1) 可行性收敛指标
        ax = axes[0, 0]
        plotted = False
        if best_cv is not None:
            ax.plot(x_ticks, np.nan_to_num(best_cv, nan=np.nan), "r-o", linewidth=2.0, label="best_cv")
            plotted = True
        if aocc_cv is not None:
            ax.plot(x_ticks, np.nan_to_num(aocc_cv, nan=np.nan), "b-s", linewidth=1.8, label="aocc_cv")
            plotted = True
        if aocc_obj is not None:
            ax.plot(x_ticks, np.nan_to_num(aocc_obj, nan=np.nan), "k-^", linewidth=1.6, label="aocc_objective")
            plotted = True
        ax.set_title("Constraint/Objective Convergence")
        ax.set_xlabel("Attempt")
        ax.set_ylabel("Value")
        ax.grid(True, alpha=0.25)
        if plotted:
            ax.legend(loc="best", fontsize=8)

        # 2) score 与 solver cost
        ax = axes[0, 1]
        has_score = False
        has_cost = False
        if score is not None:
            score = np.nan_to_num(score, nan=0.0)
            ax.bar(x_ticks, score, color="#4c72b0", alpha=0.75, label="score")
            has_score = True
        ax.set_title("Attempt Score and Solver Cost")
        ax.set_xlabel("Attempt")
        ax.set_ylabel("Score")
        ax.grid(True, alpha=0.25)
        ax2 = ax.twinx()
        if solver_cost is not None:
            solver_cost = np.nan_to_num(solver_cost, nan=0.0)
            ax2.plot(x_ticks, solver_cost, color="#dd8452", marker="o", linewidth=1.8, label="solver_cost")
            ax2.set_ylabel("Solver Cost (s)")
            has_cost = True
        handles_l, labels_l = ax.get_legend_handles_labels()
        handles_r, labels_r = ax2.get_legend_handles_labels()
        if has_score or has_cost:
            ax.legend(handles_l + handles_r, labels_l + labels_r, loc="best", fontsize=8)

        # 3) 诊断状态分布
        ax = axes[1, 0]
        if "diagnosis_status" in df.columns:
            status_counts = df["diagnosis_status"].fillna("unknown").astype(str).value_counts()
            x_pos = np.arange(len(status_counts), dtype=float)
            ax.bar(x_pos, status_counts.values, color="#55a868", alpha=0.85)
            ax.set_xticks(x_pos)
            ax.set_xticklabels(status_counts.index, rotation=20, ha="right")
            ax.set_title("Diagnosis Status Distribution")
            ax.set_ylabel("Count")
            for idx, val in enumerate(status_counts.values):
                ax.text(idx, float(val) + 0.05, str(int(val)), ha="center", va="bottom", fontsize=9)
        else:
            ax.text(0.5, 0.5, "No diagnosis_status column", transform=ax.transAxes,
                    ha="center", va="center", fontsize=10, color="gray")
            ax.set_title("Diagnosis Status Distribution")
        ax.grid(True, axis="y", alpha=0.25)

        # 4) 分支动作与最优标记
        ax = axes[1, 1]
        if "branch_action" in df.columns:
            branch_actions = df["branch_action"].fillna("").astype(str)
            unique_actions = sorted([item for item in branch_actions.unique() if item != ""])
            action_to_idx = {name: idx for idx, name in enumerate(unique_actions)}
            y_values = np.array([action_to_idx.get(name, -1) for name in branch_actions], dtype=float)
            colors = np.full(len(y_values), "#8c8c8c", dtype=object)
            if "is_best_attempt" in df.columns:
                best_mask = (
                    df["is_best_attempt"]
                    .astype(str)
                    .str.strip()
                    .str.lower()
                    .isin({"1", "true", "yes"})
                )
                colors[best_mask.to_numpy()] = "#d62728"
            ax.scatter(x_ticks, y_values, c=colors, s=55, alpha=0.9)
            ax.set_yticks(list(action_to_idx.values()))
            ax.set_yticklabels(list(action_to_idx.keys()))
            ax.set_title("Branch Action Timeline")
            ax.set_xlabel("Attempt")
            ax.set_ylabel("Branch Action")
        else:
            ax.text(0.5, 0.5, "No branch_action column", transform=ax.transAxes,
                    ha="center", va="center", fontsize=10, color="gray")
            ax.set_title("Branch Action Timeline")
        ax.grid(True, alpha=0.2)

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()
        logger.info("mass 轨迹图生成成功")

    except Exception as e:
        error_msg = f"生成 mass 轨迹图失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise VisualizationError(error_msg) from e


def plot_thermal_heatmap(design_state, thermal_data: Dict[str, float], output_path: str):
    """
    绘制热图

    Args:
        design_state: 设计状态
        thermal_data: 热数据字典 {component_id: temperature}
        output_path: 输出文件路径
    """
    try:
        logger.info(f"生成热图: {output_path}")

        fig = plt.figure(figsize=(14, 10))

        # 创建3D视图
        ax = fig.add_subplot(121, projection='3d')

        # 绘制外壳
        if design_state.envelope:
            env_pos, env_dims = _envelope_bounds(design_state)
            draw_box(
                ax,
                env_pos,
                env_dims,
                color='lightgray',
                alpha=0.05,
                label='Envelope'
            )

        # 获取温度范围用于颜色映射
        temps = list(thermal_data.values())
        if temps:
            min_temp = min(temps)
            max_temp = max(temps)
            temp_range = max_temp - min_temp if max_temp > min_temp else 1
        else:
            min_temp, max_temp, temp_range = 0, 100, 100

        # 绘制组件（根据温度着色）
        for comp in design_state.components:
            pos = _component_min_corner(comp)
            dims = [comp.dimensions.x, comp.dimensions.y, comp.dimensions.z]

            # 获取温度并映射到颜色
            temp = thermal_data.get(comp.id, min_temp)
            normalized_temp = (temp - min_temp) / temp_range
            color = plt.cm.hot(normalized_temp)

            draw_box(ax, pos, dims, color=color, alpha=0.7, label=f"{comp.id}\n{temp:.1f}°C")

        # 设置3D轴范围，避免因数据稀疏导致图像压缩
        x_points: List[float] = []
        y_points: List[float] = []
        z_points: List[float] = []
        for comp in design_state.components:
            pos = _component_min_corner(comp)
            dims = [comp.dimensions.x, comp.dimensions.y, comp.dimensions.z]
            x_points.extend([pos[0], pos[0] + dims[0]])
            y_points.extend([pos[1], pos[1] + dims[1]])
            z_points.extend([pos[2], pos[2] + dims[2]])
        if design_state.envelope:
            env_pos, env_dims = _envelope_bounds(design_state)
            x_points.extend([env_pos[0], env_pos[0] + env_dims[0]])
            y_points.extend([env_pos[1], env_pos[1] + env_dims[1]])
            z_points.extend([env_pos[2], env_pos[2] + env_dims[2]])
        if x_points and y_points and z_points:
            x_min, x_max = min(x_points), max(x_points)
            y_min, y_max = min(y_points), max(y_points)
            z_min, z_max = min(z_points), max(z_points)
            span = max(x_max - x_min, y_max - y_min, z_max - z_min, 1.0)
            cx = (x_min + x_max) / 2.0
            cy = (y_min + y_max) / 2.0
            cz = (z_min + z_max) / 2.0
            half = span / 2.0 + span * 0.08
            ax.set_xlim(cx - half, cx + half)
            ax.set_ylim(cy - half, cy + half)
            ax.set_zlim(cz - half, cz + half)

        ax.set_xlabel('X (mm)')
        ax.set_ylabel('Y (mm)')
        ax.set_zlabel('Z (mm)')
        ax.set_title('3D Thermal Distribution')
        ax.view_init(elev=20, azim=45)

        # 创建2D俯视图热图
        ax2 = fig.add_subplot(122)

        # 创建网格
        if design_state.envelope:
            env_pos, env_dims = _envelope_bounds(design_state)
            x_min, y_min = env_pos[0], env_pos[1]
            x_max, y_max = env_pos[0] + env_dims[0], env_pos[1] + env_dims[1]
        else:
            min_corner = np.array([np.inf, np.inf], dtype=float)
            max_corner = np.array([-np.inf, -np.inf], dtype=float)
            for comp in design_state.components:
                cmin = np.array(_component_min_corner(comp)[:2], dtype=float)
                cmax = cmin + np.array([comp.dimensions.x, comp.dimensions.y], dtype=float)
                min_corner = np.minimum(min_corner, cmin)
                max_corner = np.maximum(max_corner, cmax)
            if not np.isfinite(min_corner).all():
                min_corner = np.array([-100.0, -100.0], dtype=float)
                max_corner = np.array([100.0, 100.0], dtype=float)
            x_min, y_min = float(min_corner[0]), float(min_corner[1])
            x_max, y_max = float(max_corner[0]), float(max_corner[1])

        grid_size = 120
        x_grid = np.linspace(x_min, x_max, grid_size)
        y_grid = np.linspace(y_min, y_max, grid_size)
        X, Y = np.meshgrid(x_grid, y_grid)
        weighted_temp = np.zeros_like(X, dtype=float)
        weights = np.zeros_like(X, dtype=float)

        # 使用高斯核构建平滑二维热代理场（确定性）
        for comp in design_state.components:
            cx = float(comp.position.x)
            cy = float(comp.position.y)
            temp = float(thermal_data.get(comp.id, min_temp))
            sigma = max(float(max(comp.dimensions.x, comp.dimensions.y)) * 0.55, 5.0)
            influence = np.exp(-(((X - cx) ** 2 + (Y - cy) ** 2) / (2.0 * sigma ** 2)))
            weighted_temp += influence * temp
            weights += influence

        Z = np.where(weights > 1e-9, weighted_temp / weights, min_temp)

        # 绘制热图
        im = ax2.contourf(X, Y, Z, levels=24, cmap='hot')
        plt.colorbar(im, ax=ax2, label='Temperature (°C)')

        # 绘制组件边界与标签
        for comp in design_state.components:
            min_pos = _component_min_corner(comp)
            rect = patches.Rectangle(
                (min_pos[0], min_pos[1]),
                comp.dimensions.x, comp.dimensions.y,
                linewidth=1, edgecolor='cyan', facecolor='none'
            )
            ax2.add_patch(rect)

            temp = float(thermal_data.get(comp.id, min_temp))
            ax2.text(
                float(comp.position.x),
                float(comp.position.y),
                f"{comp.id}\n{temp:.1f}°C",
                ha='center',
                va='center',
                fontsize=7,
                color='white',
                weight='bold'
            )

        ax2.set_xlabel('X (mm)')
        ax2.set_ylabel('Y (mm)')
        ax2.set_title('Top View Thermal Heatmap')
        ax2.set_aspect('equal')

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        logger.info(f"热图生成成功")

    except Exception as e:
        error_msg = f"生成热图失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise VisualizationError(error_msg) from e


def generate_visualizations(experiment_dir: str):
    """
    为实验生成所有可视化

    Args:
        experiment_dir: 实验目录路径
    """
    print("\n生成可视化...")
    logger.info(f"开始生成可视化: {experiment_dir}")

    viz_dir = os.path.join(experiment_dir, 'visualizations')
    os.makedirs(viz_dir, exist_ok=True)
    optimization_mode = _detect_optimization_mode(experiment_dir)
    logger.info(f"可视化模式检测: {optimization_mode}")

    # 1. 模式化轨迹图
    csv_path = _resolve_run_artifact_path(
        experiment_dir,
        index_key="agent_loop_trace_csv",
        fallback="evolution_trace.csv",
    )
    mass_csv_path = _resolve_run_artifact_path(
        experiment_dir,
        index_key="mass_trace_csv",
        fallback="mass_trace.csv",
    )
    tables_dir = os.path.join(experiment_dir, "tables")
    if optimization_mode == "mass":
        timeline_report = render_mass_artifacts(
            mass_csv_path=mass_csv_path,
            tables_dir=tables_dir,
            viz_dir=viz_dir,
            experiment_dir=experiment_dir,
            plot_mass_trace=plot_mass_trace,
            plot_mass_storyboard=plot_mass_storyboard,
            plot_layout_timeline=plot_layout_timeline,
        )
    elif optimization_mode == "vop_maas":
        timeline_report = render_vop_maas_artifacts(
            delegated_mass_csv_path=mass_csv_path,
            tables_dir=tables_dir,
            viz_dir=viz_dir,
            experiment_dir=experiment_dir,
            plot_mass_trace=plot_mass_trace,
            plot_mass_storyboard=plot_mass_storyboard,
            plot_layout_timeline=plot_layout_timeline,
        )
    else:
        timeline_report = render_agent_loop_artifacts(
            csv_path=csv_path,
            viz_dir=viz_dir,
            plot_evolution_trace=plot_evolution_trace,
        )

    # 2. 布局与热图（需要设计状态文件）
    import glob
    import json
    from core.protocol import DesignState

    design_files = glob.glob(os.path.join(experiment_dir, 'design_state_iter_*.json'))

    def _iteration_from_filename(path: str) -> int:
        stem = Path(path).stem
        try:
            return int(stem.split("_")[-1])
        except Exception:
            return -1

    initial_state = None
    final_state = None
    thermal_data: Dict[str, float] = {}
    layout_evolution_report: Dict[str, Any] = {}
    snapshot_records: List[Dict[str, Any]] = []

    if optimization_mode in {"mass", "vop_maas"}:
        snapshot_records = _load_layout_snapshot_records(experiment_dir)
        if snapshot_records:
            try:
                first_snapshot = dict(snapshot_records[0].get("snapshot", {}) or {})
                final_snapshot = dict(snapshot_records[-1].get("snapshot", {}) or {})
                initial_state = DesignState(
                    **dict(first_snapshot.get("design_state", {}) or {})
                )
                final_state = DesignState(
                    **dict(final_snapshot.get("design_state", {}) or {})
                )
            except Exception as e:
                logger.warning("mass snapshot state parse failed: %s", e)
                initial_state = None
                final_state = None

    if final_state is None and design_files:
        try:
            design_files = sorted(design_files, key=_iteration_from_filename)
            first_file = design_files[0]
            latest_file = design_files[-1]

            with open(first_file, 'r', encoding='utf-8') as f:
                initial_data = json.load(f)
            with open(latest_file, 'r', encoding='utf-8') as f:
                latest_data = json.load(f)

            initial_state = DesignState(**initial_data)
            final_state = DesignState(**latest_data)
        except Exception as e:
            print(f"  [FAIL] 可视化生成失败: {e}")
            logger.error(f"可视化生成失败: {e}", exc_info=True)

    if final_state is not None:
        try:
            # 最终3D布局图
            output_path = os.path.join(viz_dir, 'final_layout_3d.png')
            plot_3d_layout(final_state, output_path)
            print(f"  [OK] 3D布局图: {output_path}")

            # 布局演化图
            output_path = os.path.join(viz_dir, 'layout_evolution.png')
            if optimization_mode in {"mass", "vop_maas"}:
                layout_evolution_report = plot_layout_evolution_from_snapshots(
                    experiment_dir,
                    output_path,
                )
                if int(layout_evolution_report.get("record_count", 0) or 0) > 0:
                    print(f"  [OK] 布局演化图(events/snapshots): {output_path}")
                elif initial_state is not None:
                    plot_layout_evolution(initial_state, final_state, output_path)
                    print(f"  [OK] 布局演化图(fallback): {output_path}")
            elif initial_state is not None:
                plot_layout_evolution(initial_state, final_state, output_path)
                print(f"  [OK] 布局演化图: {output_path}")

            # 热代理图（确定性，基于功率密度）
            thermal_data = build_power_density_proxy(
                final_state,
                csv_path=mass_csv_path if optimization_mode in {"mass", "vop_maas"} else csv_path,
            )
            output_path = os.path.join(viz_dir, 'thermal_heatmap.png')
            plot_thermal_heatmap(final_state, thermal_data, output_path)
            print(f"  [OK] 热图: {output_path}")
        except Exception as e:
            print(f"  [FAIL] 可视化生成失败: {e}")
            logger.error(f"可视化生成失败: {e}", exc_info=True)

    # 3. 可视化摘要文本
    try:
        if optimization_mode == "mass":
            summary_text = _build_mass_visualization_summary(mass_csv_path)
            if os.path.isdir(tables_dir):
                summary_text = summary_text + "\n" + _build_mass_tables_summary(tables_dir)
            if timeline_report:
                summary_text = summary_text + "\n=== Layout Timeline Artifacts ==="
                summary_text = summary_text + (
                    f"\n- Frames rendered: {int(timeline_report.get('frame_count', 0) or 0)}"
                )
                frames_dir = str(timeline_report.get("frames_dir", "") or "")
                gif_path = str(timeline_report.get("gif_path", "") or "")
                if frames_dir:
                    summary_text = summary_text + (
                        f"\n- Frames dir: {serialize_run_path(experiment_dir, frames_dir)}"
                    )
                if gif_path:
                    summary_text = summary_text + (
                        f"\n- GIF: {serialize_run_path(experiment_dir, gif_path)}"
                    )
            if layout_evolution_report:
                summary_text = summary_text + "\n=== Layout Evolution Truth-Source ==="
                summary_text = summary_text + (
                    f"\n- Snapshot records: {int(layout_evolution_report.get('record_count', 0) or 0)}"
                )
                summary_text = summary_text + (
                    f"\n- Initial->Best moved: {int(layout_evolution_report.get('moved_count_initial_to_best', 0) or 0)}"
                    f", mean={float(layout_evolution_report.get('mean_displacement_initial_to_best', 0.0) or 0.0):.4f} mm"
                    f", max={float(layout_evolution_report.get('max_displacement_initial_to_best', 0.0) or 0.0):.4f} mm"
                )
                summary_text = summary_text + (
                    f"\n- Best->Final moved: {int(layout_evolution_report.get('moved_count_best_to_final', 0) or 0)}"
                    f", max={float(layout_evolution_report.get('max_displacement_best_to_final', 0.0) or 0.0):.4f} mm"
                )
                summary_text = summary_text + (
                    f"\n- Frame transitions: {int(layout_evolution_report.get('frame_transition_count', 0) or 0)}"
                    f", frame_max={float(layout_evolution_report.get('frame_transition_max', 0.0) or 0.0):.4f} mm"
                )
                zero_reason = str(layout_evolution_report.get("zero_reason", "") or "").strip()
                if zero_reason:
                    summary_text = summary_text + f"\n- Zero-movement reason: {zero_reason}"
        elif optimization_mode == "vop_maas":
            summary_text = _build_vop_controller_summary(
                experiment_dir,
                tables_dir=tables_dir,
            )
            summary_text = summary_text + "\n" + _build_mass_visualization_summary(mass_csv_path)
            if os.path.isdir(tables_dir):
                summary_text = summary_text + "\n" + _build_mass_tables_summary(tables_dir)
            if timeline_report:
                summary_text = summary_text + "\n=== Delegated Mass Timeline Artifacts ==="
                summary_text = summary_text + (
                    f"\n- Frames rendered: {int(timeline_report.get('frame_count', 0) or 0)}"
                )
                frames_dir = str(timeline_report.get("frames_dir", "") or "")
                gif_path = str(timeline_report.get("gif_path", "") or "")
                if frames_dir:
                    summary_text = summary_text + (
                        f"\n- Frames dir: {serialize_run_path(experiment_dir, frames_dir)}"
                    )
                if gif_path:
                    summary_text = summary_text + (
                        f"\n- GIF: {serialize_run_path(experiment_dir, gif_path)}"
                    )
            if layout_evolution_report:
                summary_text = summary_text + "\n=== Delegated Mass Layout Evolution ==="
                summary_text = summary_text + (
                    f"\n- Snapshot records: {int(layout_evolution_report.get('record_count', 0) or 0)}"
                )
                summary_text = summary_text + (
                    f"\n- Initial->Best moved: {int(layout_evolution_report.get('moved_count_initial_to_best', 0) or 0)}"
                    f", mean={float(layout_evolution_report.get('mean_displacement_initial_to_best', 0.0) or 0.0):.4f} mm"
                    f", max={float(layout_evolution_report.get('max_displacement_initial_to_best', 0.0) or 0.0):.4f} mm"
                )
        else:
            summary_text = _build_visualization_summary(
                csv_path=csv_path,
                initial_state=initial_state,
                final_state=final_state,
                thermal_data=thermal_data if thermal_data else None,
            )
        summary_text = f"Optimization mode: {optimization_mode}\n" + summary_text
        summary_path = os.path.join(viz_dir, "visualization_summary.txt")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(summary_text + "\n")
        print(f"  [OK] 可视化摘要: {summary_path}")
    except Exception as e:
        print(f"  [FAIL] 可视化摘要生成失败: {e}")
        logger.error(f"可视化摘要生成失败: {e}", exc_info=True)

    print("可视化生成完成")
    logger.info("可视化生成完成")


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        experiment_dir = sys.argv[1]
        generate_visualizations(experiment_dir)
    else:
        print("用法: python visualization.py <experiment_dir>")
