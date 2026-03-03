#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
L1 入门级测试 - 可显式指定优化模式与仿真后端。

默认使用:
- optimization.mode = pymoo_maas
- simulation.backend = comsol
"""

import argparse
import io
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 修复 Windows GBK 编码问题
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def _inject_structural_physics_compat():
    """
    兼容旧版 simulation/__init__.py 对 StructuralPhysics 类的导入。
    """
    module_name = "simulation.structural_physics"
    existing = sys.modules.get(module_name)
    if existing is not None and hasattr(existing, "StructuralPhysics"):
        return

    module_path = project_root / "simulation" / "structural_physics.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载兼容模块: {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "StructuralPhysics"):
        module.StructuralPhysics = type(
            "StructuralPhysics",
            (),
            {
                "calculate_center_of_mass": staticmethod(module.calculate_center_of_mass),
                "calculate_cg_offset": staticmethod(module.calculate_cg_offset),
                "calculate_moment_of_inertia": staticmethod(module.calculate_moment_of_inertia),
                "analyze_mass_distribution": staticmethod(module.analyze_mass_distribution),
            },
        )
    sys.modules[module_name] = module


def _load_workflow_orchestrator():
    """
    延迟导入编排器，并对 StructuralPhysics 导入错误做兼容修复。
    """
    try:
        from workflow.orchestrator import WorkflowOrchestrator
        return WorkflowOrchestrator
    except ImportError as exc:
        err_text = str(exc)
        if "StructuralPhysics" not in err_text:
            raise
        print("[WARN] 检测到 StructuralPhysics 导入异常，应用运行时兼容补丁后重试...")
        _inject_structural_physics_compat()
        from workflow.orchestrator import WorkflowOrchestrator
        return WorkflowOrchestrator


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MsGalaxy L1 入门级测试")
    parser.add_argument(
        "--mode",
        choices=["agent_loop", "pymoo_maas"],
        default="pymoo_maas",
        help="优化模式（默认: pymoo_maas）",
    )
    parser.add_argument(
        "--backend",
        choices=["comsol", "simplified", "matlab"],
        default="comsol",
        help="仿真后端（默认: comsol）",
    )
    parser.add_argument(
        "--thermal-evaluator-mode",
        choices=["proxy", "online_comsol"],
        default=None,
        help="pymoo_maas 热评估模式（默认: comsol->online_comsol, 其他->proxy）",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=5,
        help="最大迭代次数（默认: 5）",
    )
    parser.add_argument(
        "--bom-file",
        default=str(project_root / "config" / "bom_L1_simple.json"),
        help="BOM 文件路径",
    )
    parser.add_argument(
        "--base-config",
        default=str(project_root / "config" / "system.yaml"),
        help="基础配置文件路径",
    )
    parser.add_argument(
        "--disable-physics-audit",
        action="store_true",
        help="关闭 pymoo_maas Top-K 物理审计",
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
        default=0.18,
        help="deterministic-intent 自适应边界: 搜索半径占包络跨度比例（默认: 0.18）",
    )
    parser.add_argument(
        "--deterministic-min-delta-mm",
        type=float,
        default=12.0,
        help="deterministic-intent 自适应边界: 每轴最小搜索半径 mm（默认: 12.0）",
    )
    parser.add_argument(
        "--deterministic-max-delta-mm",
        type=float,
        default=80.0,
        help="deterministic-intent 自适应边界: 每轴最大搜索半径 mm（默认: 80.0）",
    )
    parser.add_argument(
        "--log-base-dir",
        default=None,
        help="覆盖 logging.base_dir",
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
    if mode != "pymoo_maas":
        selected = "proxy"
    return selected


def _write_runtime_config(args, tmp_dir: Path) -> Path:
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

    if args.disable_semantic:
        config["knowledge"]["enable_semantic"] = False
        config["optimization"]["pymoo_maas_enable_semantic_zones"] = False
    elif "pymoo_maas_enable_semantic_zones" not in config["optimization"]:
        config["optimization"]["pymoo_maas_enable_semantic_zones"] = True
    if args.log_base_dir:
        config["logging"]["base_dir"] = str(args.log_base_dir)

    if args.mode == "pymoo_maas":
        thermal_mode = _resolve_thermal_mode(
            mode=args.mode,
            backend=args.backend,
            requested=args.thermal_evaluator_mode,
        )
        config["optimization"]["pymoo_maas_thermal_evaluator_mode"] = thermal_mode
        if args.disable_physics_audit:
            config["optimization"]["pymoo_maas_enable_physics_audit"] = False

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
    """运行 L1 入门级测试"""
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    print("=" * 80)
    print("🚀 MsGalaxy L1 入门级测试 (Simple)")
    print("=" * 80)
    print("📦 组件数量: 2个")
    print("🎯 测试目标: 验证 BOM -> 优化求解 -> 仿真评估链路")
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

            if args.deterministic_intent and args.mode == "pymoo_maas":
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
                    return _build_deterministic_intent(
                        component_ids,
                        runtime_constraints or {},
                        variable_bounds=adaptive_bounds,
                    )

                orchestrator.meta_reasoner.generate_modeling_intent = _patched_generate_modeling_intent
                print("[OK] Deterministic ModelingIntent enabled (LLM bypass)")

            print("[OK] Orchestrator initialized")
            print(f"     - LLM model: {orchestrator.config['openai']['model']}")
            print(f"     - Optimization mode: {orchestrator.optimization_mode}")
            print(f"     - Simulation backend: {orchestrator.config['simulation']['backend']}")
            if orchestrator.optimization_mode == "pymoo_maas":
                print(
                    "     - Thermal evaluator mode: "
                    f"{orchestrator.config['optimization'].get('pymoo_maas_thermal_evaluator_mode', 'proxy')}"
                )
                print(
                    "     - Physics audit: "
                    f"{orchestrator.config['optimization'].get('pymoo_maas_enable_physics_audit', True)}"
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
                if metadata.get("optimization_mode") == "pymoo_maas":
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
            print("✅ L1 入门级测试成功！系统基础功能验证通过。")
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
