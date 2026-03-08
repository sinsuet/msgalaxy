#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Shared CLI utilities for the experimental vop_maas stack.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
from pathlib import Path

import yaml
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from run.stack_contract import enforce_mode_stack_contract
from run.mass.run_L1 import (
    MASS_LEVEL_PROFILE_PATH,
    _augment_deterministic_intent_for_level,
    _build_deterministic_intent,
    _collect_component_ids_from_bom,
    _derive_adaptive_bounds_from_state,
    _derive_run_label_from_bom,
    _level_defaults,
    _load_openai_config_preview,
    _load_workflow_orchestrator,
    _normalize_level_tag,
    _print_active_llm_profile,
    _resolve_level_profile,
    _resolve_llm_runtime_profile,
    _sanitize_deterministic_bound_args,
)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def _resolve_thermal_mode(mode: str, backend: str, requested: str | None) -> str:
    if requested:
        selected = requested
    else:
        selected = "online_comsol" if backend == "comsol" else "proxy"

    if backend != "comsol" and selected == "online_comsol":
        print("[WARN] 非 COMSOL backend 不建议使用 online_comsol，自动回退为 proxy")
        selected = "proxy"
    if mode not in {"mass", "vop_maas"}:
        selected = "proxy"
    return selected


def _default_bom(level_tag: str) -> str:
    normalized = _normalize_level_tag(level_tag)
    mapping = {
        "L1": "level_L1_foundation_stack.json",
        "L2": "level_L2_thermal_power_stack.json",
        "L3": "level_L3_structural_mission_stack.json",
        "L4": "level_L4_full_stack_operator.json",
    }
    return str(project_root / "config" / "bom" / "mass" / mapping[normalized])


def _resolve_llm_api_key(config_openai: dict | None = None) -> tuple[str, str]:
    load_dotenv(project_root / ".env", override=False)
    openai_cfg = dict(config_openai or {})
    profile, _error = _resolve_llm_runtime_profile(openai_cfg)
    if profile is not None and str(getattr(profile, "api_key", "") or "").strip():
        return str(profile.api_key), str(getattr(profile, "api_key_source", "") or "")
    config_api_key = str(openai_cfg.get("api_key", "") or "").strip()
    if (
        config_api_key.startswith("${")
        and config_api_key.endswith("}")
    ) or config_api_key.startswith("$"):
        config_api_key = ""
    candidates = [
        ("config.openai.api_key", config_api_key),
        ("OPENAI_RELAY_API_KEY", str(os.environ.get("OPENAI_RELAY_API_KEY", "") or "").strip()),
        ("DASHSCOPE_API_KEY", str(os.environ.get("DASHSCOPE_API_KEY", "") or "").strip()),
        ("OPENAI_API_KEY", str(os.environ.get("OPENAI_API_KEY", "") or "").strip()),
    ]
    for source, value in candidates:
        if value:
            return value, source
    return "", ""


def build_cli_parser(level_tag: str) -> argparse.ArgumentParser:
    defaults = _level_defaults(level_tag)
    normalized = _normalize_level_tag(level_tag)
    parser = argparse.ArgumentParser(
        description=f"MsGalaxy {normalized} VOP-MaaS experimental runner"
    )
    parser.add_argument(
        "--mode",
        choices=["vop_maas"],
        default="vop_maas",
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
        help="VOP-MaaS 委托 mass 时使用的热评估模式",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=int(defaults["max_iterations"]),
        help="最大迭代次数（默认: %(default)s）",
    )
    parser.add_argument(
        "--bom-file",
        default=_default_bom(normalized),
        help="BOM 文件路径",
    )
    parser.add_argument(
        "--base-config",
        default=str(project_root / "config" / "system" / "vop_maas" / "base.yaml"),
        help="基础配置文件路径",
    )
    parser.add_argument("--llm-profile", default="", help="覆盖 openai.default_text_profile")
    parser.add_argument(
        "--disable-physics-audit",
        action="store_true",
        help="关闭 Top-K 物理审计",
    )
    parser.add_argument(
        "--disable-semantic",
        action="store_true",
        help="关闭知识库语义检索",
    )
    parser.add_argument(
        "--deterministic-intent",
        action="store_true",
        help="使用脚本内置 ModelingIntent，隔离 VOP policy 评估",
    )
    parser.add_argument(
        "--deterministic-move-ratio",
        type=float,
        default=float(defaults["deterministic_move_ratio"]),
        help="deterministic-intent 自适应边界比例",
    )
    parser.add_argument(
        "--deterministic-min-delta-mm",
        type=float,
        default=float(defaults["deterministic_min_delta_mm"]),
        help="deterministic-intent 最小搜索半径 mm",
    )
    parser.add_argument(
        "--deterministic-max-delta-mm",
        type=float,
        default=float(defaults["deterministic_max_delta_mm"]),
        help="deterministic-intent 最大搜索半径 mm",
    )
    parser.add_argument(
        "--mock-policy",
        action="store_true",
        help="启用本地 mock VOP policy，便于无 API 模式验线",
    )
    parser.add_argument(
        "--level-tag",
        choices=["L1", "L2", "L3", "L4"],
        default=normalized,
        help="场景等级标签",
    )
    parser.add_argument(
        "--level-profile",
        default=str(MASS_LEVEL_PROFILE_PATH),
        help="沿用 mass 的 L1-L4 level profile",
    )
    parser.add_argument("--log-base-dir", default=None, help="覆盖 logging.base_dir")
    parser.add_argument("--run-label", default="", help="覆盖 logging.run_label")
    parser.add_argument(
        "--run-naming-strategy",
        choices=["compact", "verbose"],
        default="compact",
        help="运行目录命名策略（默认: %(default)s）",
    )
    return parser


def _write_runtime_config(args, tmp_dir: Path) -> Path:
    enforce_mode_stack_contract(
        mode=str(args.mode),
        bom_file=str(args.bom_file),
        base_config=str(args.base_config),
        project_root=project_root,
        context="run/vop_maas/common",
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
    selected_profile = str(getattr(args, "llm_profile", "") or "").strip()
    if selected_profile:
        config["openai"]["default_text_profile"] = selected_profile

    api_key, api_key_source = _resolve_llm_api_key(config.get("openai", {}))
    if api_key_source == "config.openai.api_key" and api_key:
        config["openai"]["api_key"] = api_key
    else:
        config["openai"].pop("api_key", None)
    if not api_key:
        config["openai"]["api_key"] = "vop_local_placeholder"

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
    config["optimization"]["vop_execution_package_scope"] = "m0_nsga3_only"

    if args.disable_semantic:
        config["knowledge"]["enable_semantic"] = False
        config["optimization"]["mass_enable_semantic_zones"] = False
    elif "mass_enable_semantic_zones" not in config["optimization"]:
        config["optimization"]["mass_enable_semantic_zones"] = True

    config["optimization"]["vop_mock_policy_enabled"] = bool(getattr(args, "mock_policy", False))
    config["optimization"].setdefault("vop_policy_max_candidates", 3)
    config["optimization"].setdefault("vop_policy_screening_enabled", True)
    config["optimization"].setdefault("vop_policy_screening_top_k", 1)
    config["optimization"].setdefault("vop_policy_validation_strict", False)
    config["optimization"].setdefault("vop_reflective_replan_enabled", True)
    config["optimization"].setdefault("vop_reflective_replan_max_rounds", 1)
    config["optimization"].setdefault("vop_feedback_aware_fidelity_enabled", True)

    thermal_mode = _resolve_thermal_mode(
        mode=args.mode,
        backend=args.backend,
        requested=args.thermal_evaluator_mode,
    )
    config["optimization"]["mass_thermal_evaluator_mode"] = thermal_mode
    if args.disable_physics_audit:
        config["optimization"]["mass_enable_physics_audit"] = False

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

    runtime_cfg = tmp_dir / (
        f"system_{_normalize_level_tag(getattr(args, 'level_tag', 'L1')).lower()}_runtime.yaml"
    )
    runtime_cfg.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return runtime_cfg


def run_level(level_tag: str, argv=None) -> int:
    parser = build_cli_parser(level_tag)
    args = parser.parse_args(argv)
    normalized = _normalize_level_tag(getattr(args, "level_tag", level_tag))

    print("=" * 80)
    print(f"MsGalaxy {normalized} VOP-MaaS experimental runner")
    print("=" * 80)
    print(f"优化模式: {args.mode}")
    print(f"仿真后端: {args.backend}")
    print(f"最大迭代: {args.max_iterations}")
    print(f"Mock policy: {'ON' if bool(getattr(args, 'mock_policy', False)) else 'OFF'}")
    print(
        f"Deterministic intent: {'ON' if bool(getattr(args, 'deterministic_intent', False)) else 'OFF'}"
    )
    print("=" * 80)
    print()

    openai_preview = _load_openai_config_preview(
        str(getattr(args, "base_config", "")),
        llm_profile=str(getattr(args, "llm_profile", "") or ""),
    )
    api_key, api_key_source = _resolve_llm_api_key(openai_preview)
    if not api_key:
        print(
            "[WARN] No active LLM API key resolved for selected profile; "
            "runtime will use placeholder and may fallback to mass"
        )
        print("       可检查 DASHSCOPE_API_KEY / OPENAI_RELAY_API_KEY / OPENAI_API_KEY")
        print()
    else:
        print(f"[OK] Active LLM key source preview: {api_key_source}")
        print()

    orchestrator = None
    try:
        WorkflowOrchestrator = _load_workflow_orchestrator()
        with tempfile.TemporaryDirectory(prefix=f"msgalaxy_vop_{normalized.lower()}_") as tmp:
            tmp_dir = Path(tmp)
            runtime_cfg = _write_runtime_config(args, tmp_dir)
            orchestrator = WorkflowOrchestrator(str(runtime_cfg))

            if bool(args.deterministic_intent):
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

                def _patched_generate_modeling_intent(context, runtime_constraints=None, requirement_text=""):
                    intent = _build_deterministic_intent(
                        component_ids,
                        runtime_constraints or {},
                        variable_bounds=adaptive_bounds,
                    )
                    intent = _augment_deterministic_intent_for_level(
                        intent,
                        runtime_constraints or {},
                        level_tag=normalized,
                    )
                    intent.intent_id = f"INTENT_{normalized}_VOP_DETERMINISTIC"
                    intent.notes = f"{getattr(intent, 'notes', '')}|vop_deterministic".strip("|")
                    return intent

                orchestrator.meta_reasoner.generate_modeling_intent = _patched_generate_modeling_intent
                print("[OK] Deterministic ModelingIntent enabled for VOP-MaaS")

            print("[OK] Orchestrator initialized")
            _print_active_llm_profile(orchestrator)
            print(f"     - Optimization mode: {orchestrator.optimization_mode}")
            print(f"     - Simulation backend: {orchestrator.config['simulation']['backend']}")
            print(
                "     - Thermal evaluator mode: "
                f"{orchestrator.config['optimization'].get('mass_thermal_evaluator_mode', 'proxy')}"
            )
            print()

            final_state = orchestrator.run_optimization(
                bom_file=str(args.bom_file),
                max_iterations=int(args.max_iterations),
            )

            print()
            print("[SUCCESS] VOP-MaaS run completed")
            metadata = dict(final_state.metadata or {})
            reflective = dict(metadata.get("vop_reflective_replanning", {}) or {})
            print(f"         - Optimization mode: {metadata.get('optimization_mode', 'unknown')}")
            print(f"         - VOP policy applied: {metadata.get('vop_policy_applied', False)}")
            print(
                "         - VOP fallback reason: "
                f"{metadata.get('vop_policy_fallback_reason', '') or 'n/a'}"
            )
            print(f"         - Reflective replan: {reflective.get('triggered', False)}")
            print(
                "         - Replan reason: "
                f"{reflective.get('trigger_reason', '') or reflective.get('skipped_reason', '') or 'n/a'}"
            )
            print(
                "         - Feedback-aware fidelity: "
                f"{bool(metadata.get('vop_feedback_aware_fidelity_plan', {}))}"
            )
            return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        raise
    finally:
        if orchestrator is not None:
            try:
                if hasattr(orchestrator, "sim_driver") and hasattr(orchestrator.sim_driver, "disconnect"):
                    orchestrator.sim_driver.disconnect()
            except Exception as disconnect_error:
                print(f"[WARN] 释放仿真连接失败: {disconnect_error}")
