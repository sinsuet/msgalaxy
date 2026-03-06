#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
L3 结构-任务测试 - 可显式指定优化模式与仿真后端。

默认使用:
- optimization.mode = mass
- simulation.backend = comsol
- bom = config/bom/mass/level_L3_structural_mission_stack.json
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
    _load_workflow_orchestrator,
    _print_visualization_summary,
    _sanitize_deterministic_bound_args,
    _write_runtime_config,
)


def _build_cli_parser() -> argparse.ArgumentParser:
    level_defaults = _level_defaults("L3")
    parser = _build_l1_cli_parser()
    parser.description = "MsGalaxy L3 结构-任务测试"
    parser.set_defaults(
        mode="mass",
        backend="comsol",
        max_iterations=int(level_defaults["max_iterations"]),
        bom_file=str(project_root / "config" / "bom" / "mass" / "level_L3_structural_mission_stack.json"),
        deterministic_intent=True,
        disable_semantic=True,
        deterministic_move_ratio=float(level_defaults["deterministic_move_ratio"]),
        deterministic_min_delta_mm=float(level_defaults["deterministic_min_delta_mm"]),
        deterministic_max_delta_mm=float(level_defaults["deterministic_max_delta_mm"]),
        level_tag="L3",
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
    parser.add_argument(
        "--llm-proof",
        action="store_true",
        help=(
            "强制 LLM 建模并执行验真：要求 modeling_intent 来源为 llm_api 且未触发 fallback，"
            "否则流程返回失败码"
        ),
    )
    parser.add_argument(
        "--llm-proof-strict",
        action="store_true",
        help=(
            "在 --llm-proof 基础上增加可执行性闸门："
            "parsed_variables>0、dropped_constraints=0、unsupported_metrics=0。"
        ),
    )
    return parser


def main(argv=None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    if bool(getattr(args, "use_llm_intent", False)):
        args.deterministic_intent = False
    if bool(getattr(args, "llm_proof", False)):
        args.use_llm_intent = True
        args.deterministic_intent = False
    if bool(getattr(args, "llm_proof_strict", False)):
        args.llm_proof = True
        args.use_llm_intent = True
        args.deterministic_intent = False
    if bool(getattr(args, "enable_semantic", False)):
        args.disable_semantic = False

    print("=" * 80)
    print("MsGalaxy L3 结构-任务测试 (Structural-Mission)")
    print("=" * 80)
    print("组件数量: 9")
    print("目标: 结构、功率与 mission keepout 在高密度载荷下协同收敛")
    print(f"优化模式: {args.mode}")
    print(f"仿真后端: {args.backend}")
    print(f"最大迭代: {args.max_iterations}")
    print(f"LLM验真: {'ON' if bool(args.llm_proof) else 'OFF'}")
    print(f"LLM严格验真: {'ON' if bool(args.llm_proof_strict) else 'OFF'}")
    print(
        "Deterministic 边界: "
        f"ratio={float(args.deterministic_move_ratio):.2f}, "
        f"min_delta={float(args.deterministic_min_delta_mm):.1f}mm, "
        f"max_delta={float(args.deterministic_max_delta_mm):.1f}mm"
    )
    print("=" * 80)
    print()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("[WARN] OPENAI_API_KEY not set")
        print("       若启用 --use-llm-intent，LLM 相关功能会失败")
        if bool(getattr(args, "llm_proof", False)):
            print("       --llm-proof 将以运行后 diagnostics 验证是否发生真实 LLM API 调用")
        print()
    else:
        print(f"[OK] API Key loaded: {api_key[:10]}...{api_key[-4:]}")
        print()

    print("[INIT] Initializing workflow orchestrator...")
    orchestrator = None
    last_iteration = 0
    try:
        WorkflowOrchestrator = _load_workflow_orchestrator()
        with tempfile.TemporaryDirectory(prefix="msgalaxy_l3_") as tmp:
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
                        level_tag="L3",
                    )
                    intent.intent_id = "INTENT_L3_DETERMINISTIC"
                    intent.notes = "deterministic_l3_intent_adaptive_bounds"
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

            print("[START] Running L3 optimization...")
            print("-" * 80)
            final_state = orchestrator.run_optimization(
                bom_file=str(args.bom_file),
                max_iterations=int(args.max_iterations),
            )
            last_iteration = int(getattr(final_state, "iteration", 0))

            print()
            print("-" * 80)
            print("[SUCCESS] L3 测试完成")
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
                    modeling_diag = dict(metadata.get("modeling_intent_diagnostics", {}) or {})
                    if modeling_diag:
                        print(f"         - ModelingIntent source: {modeling_diag.get('source', 'unknown')}")
                        print(
                            "         - ModelingIntent API: "
                            f"attempted={bool(modeling_diag.get('api_call_attempted', False))}, "
                            f"succeeded={bool(modeling_diag.get('api_call_succeeded', False))}, "
                            f"fallback={bool(modeling_diag.get('used_fallback', False))}"
                        )
                        if modeling_diag.get("fallback_reason"):
                            print(
                                "         - ModelingIntent fallback reason: "
                                f"{modeling_diag.get('fallback_reason')}"
                            )
                    if bool(getattr(args, "llm_proof", False)):
                        source = str(modeling_diag.get("source", "") or "").strip().lower()
                        api_succeeded = bool(modeling_diag.get("api_call_succeeded", False))
                        used_fallback = bool(modeling_diag.get("used_fallback", False))
                        if not (source.startswith("llm_api") and api_succeeded and not used_fallback):
                            print("[ERROR] LLM验真失败：未检测到稳定的真实 LLM 建模链路")
                            print(
                                "        要求: source=llm_api* 且 api_call_succeeded=True 且 used_fallback=False"
                            )
                            return 3
                        print("[OK] LLM验真通过：本次 ModelingIntent 来自真实 LLM API")
                    if bool(getattr(args, "llm_proof_strict", False)):
                        compile_report = dict(metadata.get("compile_report", {}) or {})
                        llm_effective = dict(metadata.get("llm_effective_report", {}) or {})
                        parsed_variables = int(
                            llm_effective.get(
                                "parsed_variables",
                                compile_report.get("parsed_variables", 0),
                            )
                            or 0
                        )
                        dropped_constraints = list(
                            llm_effective.get(
                                "dropped_constraints",
                                compile_report.get("dropped_constraints", []) or [],
                            )
                            or []
                        )
                        unsupported_metrics = list(
                            llm_effective.get(
                                "unsupported_metrics",
                                compile_report.get("unsupported_metrics", []) or [],
                            )
                            or []
                        )
                        compile_warnings = list(compile_report.get("warnings", []) or [])
                        variable_mapping_warning = bool(
                            llm_effective.get(
                                "variable_mapping_warning",
                                any(
                                    "No valid variable mapping found in ModelingIntent" in str(item)
                                    for item in compile_warnings
                                ),
                            )
                        )
                        strict_errors = []
                        if parsed_variables <= 0:
                            strict_errors.append("parsed_variables<=0")
                        if dropped_constraints:
                            strict_errors.append(
                                "dropped_constraints="
                                + ",".join(str(item) for item in dropped_constraints)
                            )
                        if unsupported_metrics:
                            strict_errors.append(
                                "unsupported_metrics="
                                + ",".join(str(item) for item in unsupported_metrics)
                            )
                        if variable_mapping_warning:
                            strict_errors.append("variable_mapping_fallback_warning_detected")

                        if strict_errors:
                            print("[ERROR] LLM严格验真失败：LLM输出未稳定进入可执行链路")
                            print(
                                "        要求: parsed_variables>0 且 dropped_constraints=0 且 unsupported_metrics=0"
                            )
                            for item in strict_errors:
                                print(f"        - {item}")
                            return 4
                        print("[OK] LLM严格验真通过：LLM输出已进入可执行优化链路")

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
                print("L3 可行解已找到。")
            else:
                print("L3 流程执行完成，但当前未找到可行解。")
            return 0

    except KeyboardInterrupt:
        if orchestrator is not None:
            try:
                orchestrator.logger.save_summary(
                    status="INTERRUPTED",
                    final_iteration=int(last_iteration),
                    notes="L3 run interrupted by user",
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







