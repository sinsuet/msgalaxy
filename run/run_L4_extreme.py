#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
L4 极限级测试 - 可显式指定优化模式与仿真后端。

默认使用:
- optimization.mode = pymoo_maas
- simulation.backend = comsol
- bom = config/bom_L4_extreme.json
"""

import argparse
import io
import os
import sys
import tempfile
from pathlib import Path

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

from run.run_L1_simple import (  # noqa: E402
    _build_cli_parser as _build_l1_cli_parser,
    _build_deterministic_intent,
    _collect_component_ids_from_bom,
    _derive_adaptive_bounds_from_state,
    _load_workflow_orchestrator,
    _print_visualization_summary,
    _sanitize_deterministic_bound_args,
    _write_runtime_config,
)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = _build_l1_cli_parser()
    parser.description = "MsGalaxy L4 极限级测试"
    parser.set_defaults(
        mode="pymoo_maas",
        backend="comsol",
        max_iterations=15,
        bom_file=str(project_root / "config" / "bom_L4_extreme.json"),
        deterministic_intent=True,
        disable_semantic=True,
        deterministic_move_ratio=0.45,
        deterministic_min_delta_mm=20.0,
        deterministic_max_delta_mm=220.0,
    )
    parser.add_argument(
        "--use-llm-intent",
        action="store_true",
        help="使用 LLM 生成 ModelingIntent（覆盖默认 deterministic intent）",
    )
    parser.add_argument(
        "--enable-semantic",
        action="store_true",
        help="启用知识库语义检索（覆盖默认 disable-semantic）",
    )
    return parser


def main(argv=None):
    """运行 L4 极限级测试"""
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    if bool(getattr(args, "use_llm_intent", False)):
        args.deterministic_intent = False
    if bool(getattr(args, "enable_semantic", False)):
        args.disable_semantic = False

    print("=" * 80)
    print("🚀 MsGalaxy L4 极限级测试 (Extreme)")
    print("=" * 80)
    print("📦 组件数量: 10个")
    print("🎯 测试目标: 双高热源+装填极限，验证系统极限鲁棒性")
    print(f"🧠 优化模式: {args.mode}")
    print(f"🧪 仿真后端: {args.backend}")
    print(f"🔄 最大迭代: {args.max_iterations}")
    print(
        "🧭 Deterministic边界: "
        f"ratio={float(args.deterministic_move_ratio):.2f}, "
        f"min_delta={float(args.deterministic_min_delta_mm):.1f}mm, "
        f"max_delta={float(args.deterministic_max_delta_mm):.1f}mm"
    )
    print("⚠️  警告: 测试成本高，建议优先在 proxy/simplified 验证参数")
    print("=" * 80)
    print()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[WARN] OPENAI_API_KEY not set")
        print("       若启用 --use-llm-intent，LLM 相关功能会失败")
        print()
    else:
        print(f"[OK] API Key loaded: {api_key[:10]}...{api_key[-4:]}")
        print()

    print("[INIT] Initializing workflow orchestrator...")
    orchestrator = None
    last_iteration = 0
    try:
        WorkflowOrchestrator = _load_workflow_orchestrator()
        with tempfile.TemporaryDirectory(prefix="msgalaxy_l4_") as tmp:
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
                    intent = _build_deterministic_intent(
                        component_ids,
                        runtime_constraints or {},
                        variable_bounds=adaptive_bounds,
                    )
                    intent.intent_id = "INTENT_L4_DETERMINISTIC"
                    intent.notes = "deterministic_l4_intent_adaptive_bounds"
                    return intent

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

            print("[START] Running L4 optimization...")
            print("-" * 80)
            final_state = orchestrator.run_optimization(
                bom_file=str(args.bom_file),
                max_iterations=int(args.max_iterations),
            )
            last_iteration = int(getattr(final_state, "iteration", 0))

            print()
            print("-" * 80)
            print("[SUCCESS] L4 测试完成！")
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
            diagnosis_status = "n/a"
            if metadata.get("optimization_mode") == "pymoo_maas":
                diagnosis_status = str(
                    dict(metadata.get("solver_diagnosis", {})).get("status", "n/a")
                ).strip().lower()
            if diagnosis_status == "feasible":
                print("✅ L4 极限级测试成功！系统极限鲁棒性验证通过。")
            else:
                print("⚠️  L4 流程执行完成，但当前未找到可行解。")
            return 0

    except KeyboardInterrupt:
        if orchestrator is not None:
            try:
                orchestrator.logger.save_summary(
                    status="INTERRUPTED",
                    final_iteration=int(last_iteration),
                    notes="L4 run interrupted by user",
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
