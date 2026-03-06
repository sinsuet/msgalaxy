#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
L1 基础全栈测试 - 可显式指定优化模式与仿真后端。

默认使用:
- optimization.mode = mass
- simulation.backend = comsol
"""

import argparse
import io
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import yaml

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from run.stack_contract import enforce_mode_stack_contract

# 修复 Windows GBK 编码问题
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def _load_workflow_orchestrator():
    """延迟导入编排器。"""
    from workflow.orchestrator import WorkflowOrchestrator

    return WorkflowOrchestrator


MASS_LEVEL_PROFILE_PATH = project_root / "config" / "system" / "mass" / "level_profiles_l1_l4.yaml"
SUPPORTED_LEVEL_TAGS = ("L1", "L2", "L3", "L4")


def _normalize_level_tag(level_tag: str) -> str:
    normalized = str(level_tag or "").strip().upper()
    if normalized in SUPPORTED_LEVEL_TAGS:
        return normalized
    return "L1"


def _load_level_profiles(profile_file: str) -> Dict[str, Any]:
    profile_path = Path(str(profile_file or "")).resolve() if str(profile_file or "").strip() else MASS_LEVEL_PROFILE_PATH
    if not profile_path.exists():
        return {}
    try:
        loaded = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _resolve_level_profile(level_tag: str, profile_file: str) -> Dict[str, Any]:
    root = _load_level_profiles(profile_file)
    levels = root.get("levels", {}) if isinstance(root, dict) else {}
    if not isinstance(levels, dict):
        return {}
    normalized = _normalize_level_tag(level_tag)
    selected = levels.get(normalized, {})
    return dict(selected) if isinstance(selected, dict) else {}


def _level_defaults(level_tag: str) -> Dict[str, float]:
    profile = _resolve_level_profile(level_tag, str(MASS_LEVEL_PROFILE_PATH))
    return {
        "max_iterations": float(profile.get("max_iterations", 5)),
        "deterministic_move_ratio": float(profile.get("deterministic_move_ratio", 0.18)),
        "deterministic_min_delta_mm": float(profile.get("deterministic_min_delta_mm", 12.0)),
        "deterministic_max_delta_mm": float(profile.get("deterministic_max_delta_mm", 80.0)),
    }


def _append_objective_if_missing(intent: Any, *, name: str, metric_key: str, direction: str, weight: float) -> None:
    from optimization.protocol import ModelingObjective

    objective_keys = {
        str(getattr(item, "metric_key", "") or "").strip().lower()
        for item in list(getattr(intent, "objectives", []) or [])
    }
    if metric_key.strip().lower() in objective_keys:
        return
    intent.objectives.append(
        ModelingObjective(
            name=name,
            metric_key=metric_key,
            direction=direction,
            weight=float(weight),
        )
    )


def _append_constraint_if_missing(
    intent: Any,
    *,
    name: str,
    metric_key: str,
    relation: str,
    target_value: float,
    category: str,
    unit: str,
) -> None:
    from optimization.protocol import ModelingConstraint

    constraint_names = {
        str(getattr(item, "name", "") or "").strip().lower()
        for item in list(getattr(intent, "hard_constraints", []) or [])
    }
    constraint_keys = {
        str(getattr(item, "metric_key", "") or "").strip().lower()
        for item in list(getattr(intent, "hard_constraints", []) or [])
    }
    lowered_name = name.strip().lower()
    lowered_key = metric_key.strip().lower()
    if lowered_name in constraint_names or lowered_key in constraint_keys:
        return
    intent.hard_constraints.append(
        ModelingConstraint(
            name=name,
            metric_key=metric_key,
            relation=relation,
            target_value=float(target_value),
            category=category,
            unit=unit,
        )
    )


def _augment_deterministic_intent_for_level(intent: Any, runtime_constraints: dict, *, level_tag: str) -> Any:
    level = _normalize_level_tag(level_tag)
    max_temp_c = float(runtime_constraints.get("max_temp_c", 55.0))
    min_clearance_mm = float(runtime_constraints.get("min_clearance_mm", 5.0))
    max_cg_offset_mm = float(runtime_constraints.get("max_cg_offset_mm", 90.0))
    min_safety_factor = float(runtime_constraints.get("min_safety_factor", 2.0))
    min_modal_freq_hz = float(runtime_constraints.get("min_modal_freq_hz", 55.0))
    max_voltage_drop_v = float(runtime_constraints.get("max_voltage_drop_v", 0.5))
    min_power_margin_pct = float(runtime_constraints.get("min_power_margin_pct", 10.0))
    max_power_w = float(runtime_constraints.get("max_power_w", 500.0))

    # Base contract: geometry + thermal + cg.
    _append_constraint_if_missing(
        intent,
        name="g_collision",
        metric_key="num_collisions",
        relation="<=",
        target_value=0.0,
        category="geometry",
        unit="count",
    )
    _append_constraint_if_missing(
        intent,
        name="g_clearance",
        metric_key="min_clearance",
        relation=">=",
        target_value=min_clearance_mm,
        category="geometry",
        unit="mm",
    )
    _append_constraint_if_missing(
        intent,
        name="g_boundary",
        metric_key="boundary_violation",
        relation="<=",
        target_value=0.0,
        category="geometry",
        unit="mm",
    )
    _append_constraint_if_missing(
        intent,
        name="g_temp",
        metric_key="max_temp",
        relation="<=",
        target_value=max_temp_c,
        category="thermal",
        unit="C",
    )
    _append_constraint_if_missing(
        intent,
        name="g_cg",
        metric_key="cg_offset",
        relation="<=",
        target_value=max_cg_offset_mm,
        category="geometry",
        unit="mm",
    )

    if level in {"L1", "L2", "L3", "L4"}:
        _append_constraint_if_missing(
            intent,
            name="g_power_vdrop",
            metric_key="voltage_drop",
            relation="<=",
            target_value=max_voltage_drop_v,
            category="power",
            unit="V",
        )
        _append_constraint_if_missing(
            intent,
            name="g_power_margin",
            metric_key="power_margin",
            relation=">=",
            target_value=min_power_margin_pct,
            category="power",
            unit="%",
        )
        _append_constraint_if_missing(
            intent,
            name="g_power_peak",
            metric_key="peak_power",
            relation="<=",
            target_value=max_power_w,
            category="power",
            unit="W",
        )
        _append_objective_if_missing(
            intent,
            name="max_power_margin",
            metric_key="power_margin",
            direction="maximize",
            weight=0.4,
        )
        _append_objective_if_missing(
            intent,
            name="min_voltage_drop",
            metric_key="voltage_drop",
            direction="minimize",
            weight=0.4,
        )

    if level in {"L1", "L2", "L3", "L4"}:
        _append_constraint_if_missing(
            intent,
            name="g_struct_safety",
            metric_key="safety_factor",
            relation=">=",
            target_value=min_safety_factor,
            category="structural",
            unit="dimensionless",
        )
        _append_constraint_if_missing(
            intent,
            name="g_struct_modal",
            metric_key="first_modal_freq",
            relation=">=",
            target_value=min_modal_freq_hz,
            category="structural",
            unit="Hz",
        )
        _append_objective_if_missing(
            intent,
            name="max_safety_factor",
            metric_key="safety_factor",
            direction="maximize",
            weight=0.5,
        )
        _append_objective_if_missing(
            intent,
            name="max_modal_freq",
            metric_key="first_modal_freq",
            direction="maximize",
            weight=0.5,
        )

    if level in {"L1", "L2", "L3", "L4"}:
        _append_constraint_if_missing(
            intent,
            name="g_mission_keepout",
            metric_key="mission_keepout_violation",
            relation="<=",
            target_value=0.0,
            category="mission",
            unit="mm",
        )
        _append_objective_if_missing(
            intent,
            name="min_mission_keepout_violation",
            metric_key="mission_keepout_violation",
            direction="minimize",
            weight=0.3,
        )
        assumptions = list(getattr(intent, "assumptions", []) or [])
        if not any("mission_keepout_proxy" in str(item) for item in assumptions):
            assumptions.append("mission_keepout_proxy:mission_keepout_violation<=0")
            intent.assumptions = assumptions

    notes = str(getattr(intent, "notes", "") or "").strip()
    suffix = f"{level.lower()}_multiphysics_contract"
    if suffix not in notes:
        intent.notes = f"{notes}|{suffix}" if notes else suffix
    return intent


def _build_cli_parser() -> argparse.ArgumentParser:
    l1_defaults = _level_defaults("L1")
    parser = argparse.ArgumentParser(description="MsGalaxy L1 基础全栈测试")
    parser.add_argument(
        "--mode",
        choices=["mass"],
        default="mass",
        help="优化模式（默认: %(default)s）",
    )
    parser.add_argument(
        "--backend",
        choices=["comsol", "simplified"],
        default="comsol",
        help="仿真后端（默认: %(default)s）",
    )
    parser.add_argument(
        "--thermal-evaluator-mode",
        choices=["proxy", "online_comsol"],
        default=None,
        help="mass 热评估模式（默认: comsol->online_comsol, 其他->proxy）",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=int(l1_defaults["max_iterations"]),
        help="最大迭代次数（默认: %(default)s）",
    )
    parser.add_argument(
        "--bom-file",
        default=str(project_root / "config" / "bom" / "mass" / "level_L1_foundation_stack.json"),
        help="BOM 文件路径",
    )
    parser.add_argument(
        "--base-config",
        default=str(project_root / "config" / "system" / "mass" / "base.yaml"),
        help="基础配置文件路径",
    )
    parser.add_argument(
        "--disable-physics-audit",
        action="store_true",
        help="关闭 mass Top-K 物理审计",
    )
    parser.add_argument(
        "--disable-semantic",
        action="store_true",
        help="关闭知识库语义检索，减少不确定外部依赖",
    )
    parser.add_argument(
        "--deterministic-intent",
        action="store_true",
        help="使用脚本内置 ModelingIntent（不调用 LLM）",
    )
    parser.add_argument(
        "--deterministic-move-ratio",
        type=float,
        default=float(l1_defaults["deterministic_move_ratio"]),
        help="deterministic-intent 自适应边界: 搜索半径占包络跨度比例（默认: %(default)s）",
    )
    parser.add_argument(
        "--deterministic-min-delta-mm",
        type=float,
        default=float(l1_defaults["deterministic_min_delta_mm"]),
        help="deterministic-intent 自适应边界: 每轴最小搜索半径 mm（默认: %(default)s）",
    )
    parser.add_argument(
        "--deterministic-max-delta-mm",
        type=float,
        default=float(l1_defaults["deterministic_max_delta_mm"]),
        help="deterministic-intent 自适应边界: 每轴最大搜索半径 mm（默认: %(default)s）",
    )
    parser.add_argument(
        "--level-tag",
        choices=list(SUPPORTED_LEVEL_TAGS),
        default="L1",
        help="场景等级标签（用于注入对应多物理约束契约）",
    )
    parser.add_argument(
        "--level-profile",
        default=str(MASS_LEVEL_PROFILE_PATH),
        help="L1-L4 等级配置文件路径",
    )
    parser.add_argument(
        "--log-base-dir",
        default=None,
        help="覆盖 logging.base_dir",
    )
    parser.add_argument(
        "--run-label",
        default="",
        help="覆盖 logging.run_label（默认根据 BOM 文件名推导）",
    )
    parser.add_argument(
        "--run-naming-strategy",
        choices=["compact", "verbose"],
        default="compact",
        help="运行目录命名策略（默认: %(default)s）",
    )
    return parser


def _sanitize_deterministic_bound_args(
    *,
    movement_ratio: float,
    min_delta_mm: float,
    max_delta_mm: float,
) -> tuple[float, float, float]:
    ratio = max(float(movement_ratio), 0.01)
    min_delta = max(float(min_delta_mm), 1.0)
    max_delta = max(float(max_delta_mm), min_delta)
    return ratio, min_delta, max_delta


def _resolve_thermal_mode(mode: str, backend: str, requested: str | None) -> str:
    if requested:
        selected = requested
    else:
        selected = "online_comsol" if backend == "comsol" else "proxy"

    if backend != "comsol" and selected == "online_comsol":
        print("[WARN] 非 COMSOL backend 不建议使用 online_comsol，自动回退为 proxy")
        selected = "proxy"
    if mode != "mass":
        selected = "proxy"
    return selected


def _derive_run_label_from_bom(bom_file: str) -> str:
    stem = Path(str(bom_file or "")).stem.strip()
    if not stem:
        return ""
    lowered = stem.lower()
    if lowered.startswith("bom_") or lowered.startswith("bom-"):
        stem = stem[4:]
        lowered = stem.lower()
    level_match = re.search(r"(?:^|_)(l\d+)(?:_|$)", lowered)
    if lowered.startswith("level_") and level_match is not None:
        return str(level_match.group(1))
    compact = lowered
    for suffix in ("_simple", "_intermediate", "_complex", "_extreme"):
        if compact.endswith(suffix) and compact.startswith("l") and "_" in compact:
            compact = compact[: -len(suffix)]
            break
    return compact


def _write_runtime_config(args, tmp_dir: Path) -> Path:
    enforce_mode_stack_contract(
        mode=str(args.mode),
        bom_file=str(args.bom_file),
        base_config=str(args.base_config),
        project_root=project_root,
        context="run/mass/run_L1",
    )
    base_config_path = Path(args.base_config).resolve()
    config = yaml.safe_load(base_config_path.read_text(encoding="utf-8"))

    config.setdefault("optimization", {})
    config.setdefault("simulation", {})
    config.setdefault("knowledge", {})
    config.setdefault("logging", {})
    config.setdefault("openai", {})

    config["optimization"]["mode"] = args.mode
    config["optimization"]["max_iterations"] = int(args.max_iterations)
    config["simulation"]["backend"] = args.backend
    config["openai"]["model"] = "qwen3-max"
    api_key = str(config["openai"].get("api_key", "") or os.environ.get("OPENAI_API_KEY", "")).strip()
    if api_key:
        config["openai"]["api_key"] = api_key
    elif bool(getattr(args, "deterministic_intent", False)):
        # Deterministic mode patches intent generation after orchestrator init,
        # so a local placeholder avoids failing before the patch is installed.
        config["openai"]["api_key"] = "deterministic_local_placeholder"

    level_tag = _normalize_level_tag(getattr(args, "level_tag", "L1"))
    level_profile = _resolve_level_profile(
        level_tag=level_tag,
        profile_file=str(getattr(args, "level_profile", str(MASS_LEVEL_PROFILE_PATH))),
    )
    profile_runtime_overrides = dict(level_profile.get("runtime_overrides", {}) or {})
    for key, value in profile_runtime_overrides.items():
        config["optimization"][str(key)] = value
    mandatory_groups = list(level_profile.get("mandatory_groups", []) or [])
    if mandatory_groups:
        config["optimization"]["mass_mandatory_hard_constraints"] = [
            str(item) for item in mandatory_groups if str(item).strip()
        ]
    config["optimization"]["mass_level_tag"] = str(level_tag)
    config["optimization"]["mass_level_profile_file"] = str(
        getattr(args, "level_profile", str(MASS_LEVEL_PROFILE_PATH))
    )

    if args.disable_semantic:
        config["knowledge"]["enable_semantic"] = False
        config["optimization"]["mass_enable_semantic_zones"] = False
    elif "mass_enable_semantic_zones" not in config["optimization"]:
        config["optimization"]["mass_enable_semantic_zones"] = True
    if args.log_base_dir:
        config["logging"]["base_dir"] = str(args.log_base_dir)
    run_label = str(getattr(args, "run_label", "") or "").strip()
    if not run_label:
        run_label = _derive_run_label_from_bom(str(getattr(args, "bom_file", "")))
    if run_label:
        config["logging"]["run_label"] = run_label
    config["logging"]["run_naming_strategy"] = str(
        getattr(args, "run_naming_strategy", "compact")
    )

    if args.mode == "mass":
        config["optimization"].setdefault("mass_rag_runtime_ingest_enabled", True)
        config["optimization"].setdefault("mass_rag_runtime_ingest_max_items", 4)
        thermal_mode = _resolve_thermal_mode(
            mode=args.mode,
            backend=args.backend,
            requested=args.thermal_evaluator_mode,
        )
        config["optimization"]["mass_thermal_evaluator_mode"] = thermal_mode
        if args.disable_physics_audit:
            config["optimization"]["mass_enable_physics_audit"] = False

    runtime_cfg = tmp_dir / "system_l1_runtime.yaml"
    runtime_cfg.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return runtime_cfg


def _collect_component_ids_from_bom(bom_file: str) -> list[str]:
    from core.bom_parser import BOMParser

    parsed = BOMParser.parse(bom_file)
    component_ids: list[str] = []
    for item in parsed:
        qty = max(int(item.quantity or 1), 1)
        if qty == 1:
            component_ids.append(str(item.id))
            continue
        for idx in range(1, qty + 1):
            component_ids.append(f"{item.id}_{idx:02d}")
    return component_ids


def _derive_adaptive_bounds_from_state(
    design_state: Any,
    *,
    movement_ratio: float = 0.18,
    min_delta_mm: float = 12.0,
    max_delta_mm: float = 80.0,
) -> dict[tuple[str, str], tuple[float, float]]:
    """
    基于初始可行布局生成自适应变量边界。

    原则：
    - 以当前可行位置为中心做局部搜索，减少全域随机搜索导致的大量几何不可行；
    - 同时严格受包络硬边界限制，避免明显越界状态。
    """
    envelope = getattr(design_state, "envelope", None)
    if envelope is None or getattr(envelope, "inner_size", None) is None:
        return {}

    inner = envelope.inner_size
    half_env = {
        "x": float(inner.x) * 0.5,
        "y": float(inner.y) * 0.5,
        "z": float(inner.z) * 0.5,
    }

    bounds: dict[tuple[str, str], tuple[float, float]] = {}
    for comp in getattr(design_state, "components", []):
        comp_id = str(getattr(comp, "id", ""))
        if not comp_id:
            continue

        comp_half = {
            "x": float(comp.dimensions.x) * 0.5,
            "y": float(comp.dimensions.y) * 0.5,
            "z": float(comp.dimensions.z) * 0.5,
        }
        comp_pos = {
            "x": float(comp.position.x),
            "y": float(comp.position.y),
            "z": float(comp.position.z),
        }

        for axis in ("x", "y", "z"):
            hard_low = -half_env[axis] + comp_half[axis]
            hard_high = half_env[axis] - comp_half[axis]

            if hard_high <= hard_low:
                center = float(comp_pos[axis])
                delta = float(max(min_delta_mm, 1.0))
                bounds[(comp_id, axis)] = (center - delta, center + delta)
                continue

            span = hard_high - hard_low
            local_delta = max(min(span * movement_ratio, max_delta_mm), min_delta_mm)

            center = float(comp_pos[axis])
            low = max(hard_low, center - local_delta)
            high = min(hard_high, center + local_delta)

            if high - low < 2.0:
                # 避免退化区间导致变量失效
                pad = min(2.0, max((hard_high - hard_low) * 0.1, 0.5))
                low = max(hard_low, center - pad)
                high = min(hard_high, center + pad)

            bounds[(comp_id, axis)] = (float(low), float(high))

    return bounds


def _build_deterministic_intent(
    component_ids: list[str],
    runtime_constraints: dict,
    variable_bounds: dict[tuple[str, str], tuple[float, float]] | None = None,
):
    from optimization.protocol import (
        ModelingConstraint,
        ModelingIntent,
        ModelingObjective,
        ModelingVariable,
    )

    variables: list[ModelingVariable] = []
    for comp_id in component_ids:
        for axis in ("x", "y", "z"):
            low, high = (-80.0, 80.0)
            if variable_bounds is not None:
                pair = variable_bounds.get((comp_id, axis))
                if pair is not None:
                    low = float(pair[0])
                    high = float(pair[1])
                    if high <= low:
                        high = low + 1.0
            variables.append(
                ModelingVariable(
                    name=f"{comp_id}_{axis}",
                    component_id=comp_id,
                    variable_type="continuous",
                    lower_bound=low,
                    upper_bound=high,
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
        ),
        ModelingObjective(
            name="min_max_temp",
            metric_key="max_temp",
            direction="minimize",
            weight=1.0,
        ),
    ]

    constraints = [
        ModelingConstraint(
            name="g_temp",
            metric_key="max_temp",
            relation="<=",
            target_value=float(runtime_constraints.get("max_temp_c", 50.0)),
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
        intent_id="INTENT_L1_DETERMINISTIC",
        iteration=1,
        problem_type="multi_objective",
        variables=variables,
        objectives=objectives,
        hard_constraints=constraints,
        soft_constraints=[],
        assumptions=[],
        notes="deterministic_l1_intent_adaptive_bounds",
    )


def _print_visualization_summary(orchestrator) -> None:
    """
    打印可视化摘要，帮助快速判断迭代有效性。
    """
    summary_path = Path(orchestrator.logger.run_dir) / "visualizations" / "visualization_summary.txt"
    if not summary_path.exists():
        print("[WARN] 可视化摘要文件不存在，跳过摘要输出")
        return

    try:
        content = summary_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(f"[WARN] 读取可视化摘要失败: {e}")
        return

    if not content:
        print("[WARN] 可视化摘要为空")
        return

    print()
    print("[SUMMARY] 可视化对比摘要:")
    print("-" * 80)
    print(content)
    print("-" * 80)


def main(argv=None):
    """运行 L1 基础全栈测试"""
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    print("=" * 80)
    print("🚀 MsGalaxy L1 基础全栈测试 (Foundation)")
    print("=" * 80)
    print("📦 组件数量: 6个")
    print("🎯 测试目标: 验证全物理场 strict-contract 下的基础可行性与算子覆盖")
    print(f"🧠 优化模式: {args.mode}")
    print(f"🧪 仿真后端: {args.backend}")
    print(f"🔄 最大迭代: {args.max_iterations}")
    print("=" * 80)
    print()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[WARN] OPENAI_API_KEY not set")
        print("       若未启用 --deterministic-intent，LLM 相关功能会失败")
        print()
    else:
        print(f"[OK] API Key loaded: {api_key[:10]}...{api_key[-4:]}")
        print()

    print("[INIT] Initializing workflow orchestrator...")
    orchestrator = None
    last_iteration = 0
    try:
        WorkflowOrchestrator = _load_workflow_orchestrator()
        with tempfile.TemporaryDirectory(prefix="msgalaxy_l1_") as tmp:
            tmp_dir = Path(tmp)
            runtime_cfg = _write_runtime_config(args, tmp_dir)
            orchestrator = WorkflowOrchestrator(str(runtime_cfg))

            if args.deterministic_intent and args.mode == "mass":
                component_ids = _collect_component_ids_from_bom(args.bom_file)
                preview_state = orchestrator._initialize_design_state(str(args.bom_file))
                ratio, min_delta, max_delta = _sanitize_deterministic_bound_args(
                    movement_ratio=args.deterministic_move_ratio,
                    min_delta_mm=args.deterministic_min_delta_mm,
                    max_delta_mm=args.deterministic_max_delta_mm,
                )
                adaptive_bounds = _derive_adaptive_bounds_from_state(
                    preview_state,
                    movement_ratio=ratio,
                    min_delta_mm=min_delta,
                    max_delta_mm=max_delta,
                )
                print(
                    f"[OK] Deterministic bounds prepared: "
                    f"{len(adaptive_bounds)} variables from initial feasible layout "
                    f"(ratio={ratio:.3f}, min_delta={min_delta:.1f}mm, max_delta={max_delta:.1f}mm)"
                )

                def _patched_generate_modeling_intent(context, runtime_constraints=None, requirement_text=""):
                    intent = _build_deterministic_intent(
                        component_ids,
                        runtime_constraints or {},
                        variable_bounds=adaptive_bounds,
                    )
                    intent = _augment_deterministic_intent_for_level(
                        intent,
                        runtime_constraints or {},
                        level_tag=str(getattr(args, "level_tag", "L1")),
                    )
                    intent.intent_id = f"INTENT_{_normalize_level_tag(getattr(args, 'level_tag', 'L1'))}_DETERMINISTIC"
                    return intent

                orchestrator.meta_reasoner.generate_modeling_intent = _patched_generate_modeling_intent
                print("[OK] Deterministic ModelingIntent enabled (LLM bypass)")

            print("[OK] Orchestrator initialized")
            print(f"     - LLM model: {orchestrator.config['openai']['model']}")
            print(f"     - Optimization mode: {orchestrator.optimization_mode}")
            print(f"     - Simulation backend: {orchestrator.config['simulation']['backend']}")
            if orchestrator.optimization_mode == "mass":
                print(
                    "     - Thermal evaluator mode: "
                    f"{orchestrator.config['optimization'].get('mass_thermal_evaluator_mode', 'proxy')}"
                )
                print(
                    "     - Physics audit: "
                    f"{orchestrator.config['optimization'].get('mass_enable_physics_audit', True)}"
                )
            print()

            print("[START] Running L1 optimization...")
            print("-" * 80)
            final_state = orchestrator.run_optimization(
                bom_file=str(args.bom_file),
                max_iterations=int(args.max_iterations),
            )
            last_iteration = int(getattr(final_state, "iteration", 0))

            print()
            print("-" * 80)
            print("[SUCCESS] L1 测试完成！")
            print()
            print("[RESULT] Final design state:")
            print(f"         - Iteration: {final_state.iteration}")
            print(f"         - Components: {len(final_state.components)}")

            metadata = dict(final_state.metadata or {})
            if metadata:
                print(f"         - Optimization mode: {metadata.get('optimization_mode', 'unknown')}")
                if metadata.get("optimization_mode") == "mass":
                    diagnosis = dict(metadata.get("solver_diagnosis", {}))
                    print(f"         - Diagnosis: {diagnosis.get('status', 'n/a')}")
                    print(f"         - Attempts: {metadata.get('maas_attempt_count', 'n/a')}")
                    print(f"         - Thermal evaluator: {metadata.get('thermal_evaluator_mode', 'n/a')}")

            if 'last_simulation' in metadata:
                sim_result = metadata['last_simulation']
                print(f"         - Max temp: {sim_result.get('max_temp', 'N/A')} °C")
                print(f"         - Violations: {len(sim_result.get('violations', []))}")

            _print_visualization_summary(orchestrator)
            print()
            print("✅ L1 基础全栈测试成功！")
            return 0

    except KeyboardInterrupt:
        if orchestrator is not None:
            try:
                orchestrator.logger.save_summary(
                    status="INTERRUPTED",
                    final_iteration=int(last_iteration),
                    notes="L1 run interrupted by user",
                    extra={
                        "optimization_mode": str(getattr(orchestrator, "optimization_mode", "unknown")),
                        "interrupted": True,
                    },
                )
            except Exception as summary_error:
                print(f"[WARN] 写入中断 summary 失败: {summary_error}")
        print("\n[INTERRUPTED] Test interrupted by user")
        return 130
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        if orchestrator is not None:
            try:
                if hasattr(orchestrator, "sim_driver") and hasattr(orchestrator.sim_driver, "disconnect"):
                    orchestrator.sim_driver.disconnect()
            except Exception as disconnect_error:
                print(f"[WARN] 释放仿真连接失败: {disconnect_error}")


if __name__ == "__main__":
    sys.exit(main())


