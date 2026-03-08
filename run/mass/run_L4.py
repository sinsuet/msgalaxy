#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
L4 全栈算子测试 - 可显式指定优化模式与仿真后端。

默认使用:
- optimization.mode = mass
- simulation.backend = comsol
- bom = config/bom/mass/level_L4_full_stack_operator.json
"""

import argparse
import io
import os
import sys
import tempfile
from pathlib import Path

# Add project root to import path.
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

# Ensure UTF-8 console on Windows.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from run.mass.run_L1 import (  # noqa: E402
    _augment_deterministic_intent_for_level,
    _build_cli_parser as _build_l1_cli_parser,
    _build_deterministic_intent,
    _collect_component_ids_from_bom,
    _derive_adaptive_bounds_from_state,
    _level_defaults,
    _load_openai_config_preview,
    _load_workflow_orchestrator,
    _print_active_llm_profile,
    _print_visualization_summary,
    _resolve_llm_api_key,
    _sanitize_deterministic_bound_args,
    _write_runtime_config,
)


def _build_cli_parser() -> argparse.ArgumentParser:
    level_defaults = _level_defaults("L4")
    parser = _build_l1_cli_parser()
    parser.description = "MsGalaxy L4 全栈算子测试"
    parser.set_defaults(
        mode="mass",
        backend="comsol",
        max_iterations=int(level_defaults["max_iterations"]),
        bom_file=str(project_root / "config" / "bom" / "mass" / "level_L4_full_stack_operator.json"),
        deterministic_intent=True,
        disable_semantic=True,
        deterministic_move_ratio=float(level_defaults["deterministic_move_ratio"]),
        deterministic_min_delta_mm=float(level_defaults["deterministic_min_delta_mm"]),
        deterministic_max_delta_mm=float(level_defaults["deterministic_max_delta_mm"]),
        level_tag="L4",
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


def main(argv=None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    if bool(getattr(args, "use_llm_intent", False)):
        args.deterministic_intent = False
    if bool(getattr(args, "enable_semantic", False)):
        args.disable_semantic = False

    print("=" * 80)
    print("MsGalaxy L4 全栈算子测试 (Full-Stack Operator)")
    print("=" * 80)
    print("组件数量: 10")
    print("目标: 当前可执行全物理场与全算子家族下的高密度可行性验证")
    print(f"优化模式: {args.mode}")
    print(f"仿真后端: {args.backend}")
    print(f"最大迭代: {args.max_iterations}")
    print(
        "Deterministic 边界: "
        f"ratio={float(args.deterministic_move_ratio):.2f}, "
        f"min_delta={float(args.deterministic_min_delta_mm):.1f}mm, "
        f"max_delta={float(args.deterministic_max_delta_mm):.1f}mm"
    )
    print("=" * 80)
    print()

    openai_preview = _load_openai_config_preview(
        str(getattr(args, "base_config", "")),
        llm_profile=str(getattr(args, "llm_profile", "") or ""),
    )
    api_key, api_key_source = _resolve_llm_api_key(openai_preview)
    if not api_key:
        print("[WARN] No active LLM API key resolved for selected profile")
        print("       可检查 DASHSCOPE_API_KEY / OPENAI_RELAY_API_KEY / OPENAI_API_KEY")
        print("       若启用 --use-llm-intent，LLM 相关功能会失败")
        print()
    else:
        print(f"[OK] Active LLM key source preview: {api_key_source}")
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
                    "[OK] Deterministic bounds prepared: "
                    f"{len(adaptive_bounds)} variables "
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
                        level_tag="L4",
                    )
                    intent.intent_id = "INTENT_L4_DETERMINISTIC"
                    intent.notes = "deterministic_l4_intent_adaptive_bounds"
                    return intent

                orchestrator.meta_reasoner.generate_modeling_intent = _patched_generate_modeling_intent
                print("[OK] Deterministic ModelingIntent enabled (LLM bypass)")

            print("[OK] Orchestrator initialized")
            _print_active_llm_profile(orchestrator)
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

            print("[START] Running L4 optimization...")
            print("-" * 80)
            final_state = orchestrator.run_optimization(
                bom_file=str(args.bom_file),
                max_iterations=int(args.max_iterations),
            )
            last_iteration = int(getattr(final_state, "iteration", 0))

            print()
            print("-" * 80)
            print("[SUCCESS] L4 测试完成")
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

            if "last_simulation" in metadata:
                sim_result = metadata["last_simulation"]
                print(f"         - Max temp: {sim_result.get('max_temp', 'N/A')} °C")
                print(f"         - Violations: {len(sim_result.get('violations', []))}")

            _print_visualization_summary(orchestrator)
            print()
            diagnosis_status = "n/a"
            if metadata.get("optimization_mode") == "mass":
                diagnosis_status = str(
                    dict(metadata.get("solver_diagnosis", {})).get("status", "n/a")
                ).strip().lower()
            if diagnosis_status == "feasible":
                print("L4 可行解已找到。")
            else:
                print("L4 流程执行完成，但当前未找到可行解。")
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







