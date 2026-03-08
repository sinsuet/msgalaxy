#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Shared runner helpers for agent_loop L1-L4 entries.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from run.stack_contract import enforce_mode_stack_contract
from run.mass.run_L1 import _load_openai_config_preview, _print_active_llm_profile


def _load_workflow_orchestrator():
    from workflow.orchestrator import WorkflowOrchestrator

    return WorkflowOrchestrator


def _build_cli_parser(*, title: str, default_bom: str, default_iterations: int) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=title)
    parser.add_argument(
        "--mode",
        choices=["agent_loop"],
        default="agent_loop",
        help="optimization mode (fixed to agent_loop)",
    )
    parser.add_argument(
        "--backend",
        choices=["comsol", "simplified"],
        default="comsol",
        help="simulation backend",
    )
    parser.add_argument("--max-iterations", type=int, default=int(default_iterations))
    parser.add_argument("--bom-file", default=str(default_bom), help="BOM file path")
    parser.add_argument(
        "--base-config",
        default=str(PROJECT_ROOT / "config" / "system" / "agent_loop" / "base.yaml"),
        help="base system config path",
    )
    parser.add_argument("--llm-profile", default="", help="override openai.default_text_profile")
    parser.add_argument("--disable-semantic", action="store_true")
    parser.add_argument("--deterministic-intent", action="store_true")
    parser.add_argument("--disable-physics-audit", action="store_true")
    parser.add_argument("--log-base-dir", default=None, help="override logging.base_dir")
    parser.add_argument(
        "--run-label",
        default="",
        help="override logging.run_label (default derives from BOM stem)",
    )
    parser.add_argument(
        "--run-naming-strategy",
        choices=["compact", "verbose"],
        default="compact",
        help="override logging.run_naming_strategy",
    )
    return parser


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


def _resolve_llm_api_key(config_openai: dict | None = None) -> tuple[str, str]:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    openai_cfg = dict(config_openai or {})
    try:
        from optimization.llm.gateway import LLMProfileResolver

        resolver = LLMProfileResolver(openai_cfg)
        selected_profile = str(openai_cfg.get("default_text_profile", "") or "").strip()
        profile = resolver.resolve_text_profile(selected_profile)
        if str(getattr(profile, "api_key", "") or "").strip():
            return str(profile.api_key), str(getattr(profile, "api_key_source", "") or "")
    except Exception:
        pass
    candidates = [
        ("config.openai.api_key", str(openai_cfg.get("api_key", "") or "").strip()),
        ("OPENAI_RELAY_API_KEY", str(os.environ.get("OPENAI_RELAY_API_KEY", "") or "").strip()),
        ("DASHSCOPE_API_KEY", str(os.environ.get("DASHSCOPE_API_KEY", "") or "").strip()),
        ("OPENAI_API_KEY", str(os.environ.get("OPENAI_API_KEY", "") or "").strip()),
    ]
    for source, value in candidates:
        if value:
            return value, source
    return "", ""


def _write_runtime_config(args, tmp_dir: Path, *, context: str) -> Path:
    enforce_mode_stack_contract(
        mode=str(args.mode),
        bom_file=str(args.bom_file),
        base_config=str(args.base_config),
        project_root=PROJECT_ROOT,
        context=context,
    )

    base_config_path = Path(args.base_config).resolve()
    config = yaml.safe_load(base_config_path.read_text(encoding="utf-8"))
    config.setdefault("optimization", {})
    config.setdefault("simulation", {})
    config.setdefault("knowledge", {})
    config.setdefault("logging", {})
    config.setdefault("openai", {})

    config["optimization"]["mode"] = "agent_loop"
    config["optimization"]["max_iterations"] = int(args.max_iterations)
    config["simulation"]["backend"] = str(args.backend)
    selected_profile = str(getattr(args, "llm_profile", "") or "").strip()
    if selected_profile:
        config["openai"]["default_text_profile"] = selected_profile
    api_key, _api_key_source = _resolve_llm_api_key(config.get("openai", {}))
    if _api_key_source == "config.openai.api_key" and api_key:
        config["openai"]["api_key"] = api_key
    else:
        config["openai"].pop("api_key", None)
    if not api_key and bool(getattr(args, "deterministic_intent", False)):
        config["openai"]["api_key"] = "deterministic_local_placeholder"
    if bool(args.disable_semantic):
        config["knowledge"]["enable_semantic"] = False
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

    runtime_cfg = tmp_dir / "system_agent_loop_runtime.yaml"
    runtime_cfg.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return runtime_cfg


def _print_visualization_summary(orchestrator) -> None:
    summary_path = Path(orchestrator.logger.run_dir) / "visualizations" / "visualization_summary.txt"
    if not summary_path.exists():
        return
    try:
        content = summary_path.read_text(encoding="utf-8").strip()
    except Exception:
        return
    if not content:
        return
    print()
    print("[SUMMARY] visualization summary:")
    print("-" * 80)
    print(content)
    print("-" * 80)


def run_agent_loop_level(
    *,
    argv,
    title: str,
    level_label: str,
    component_count: int,
    target_note: str,
    default_bom: str,
    default_iterations: int,
) -> int:
    parser = _build_cli_parser(
        title=title,
        default_bom=default_bom,
        default_iterations=default_iterations,
    )
    args = parser.parse_args(argv)

    print("=" * 80)
    print(title)
    print("=" * 80)
    print(f"components: {component_count}")
    print(f"target: {target_note}")
    print(f"optimization mode: {args.mode}")
    print(f"simulation backend: {args.backend}")
    print(f"max iterations: {args.max_iterations}")
    print("=" * 80)
    print()

    openai_preview = _load_openai_config_preview(
        str(getattr(args, "base_config", "")),
        llm_profile=str(getattr(args, "llm_profile", "") or ""),
    )
    api_key, api_key_source = _resolve_llm_api_key(openai_preview)
    if api_key:
        print(f"[OK] Active LLM key source preview: {api_key_source}")
    else:
        print("[WARN] No active LLM API key resolved for selected profile")
        print("       可检查 DASHSCOPE_API_KEY / OPENAI_RELAY_API_KEY / OPENAI_API_KEY")
    print()

    orchestrator = None
    last_iteration = 0
    try:
        workflow_orchestrator_cls = _load_workflow_orchestrator()
        with tempfile.TemporaryDirectory(prefix=f"msgalaxy_agent_{level_label.lower()}_") as tmp:
            tmp_dir = Path(tmp)
            runtime_cfg = _write_runtime_config(
                args,
                tmp_dir,
                context=f"run/agent_loop/run_{level_label}",
            )
            orchestrator = workflow_orchestrator_cls(str(runtime_cfg))
            print("[OK] Orchestrator initialized")
            _print_active_llm_profile(orchestrator)
            print(f"     - Optimization mode: {orchestrator.optimization_mode}")
            print(f"     - Simulation backend: {orchestrator.config['simulation']['backend']}")
            print()
            print("[START] running optimization...")
            final_state = orchestrator.run_optimization(
                bom_file=str(args.bom_file),
                max_iterations=int(args.max_iterations),
            )
            last_iteration = int(getattr(final_state, "iteration", 0))

            print("[SUCCESS] run completed")
            print(f"iteration: {int(getattr(final_state, 'iteration', 0))}")
            print(f"components: {len(getattr(final_state, 'components', []) or [])}")

            metadata = dict(getattr(final_state, "metadata", {}) or {})
            if metadata:
                print(f"optimization mode: {metadata.get('optimization_mode', 'unknown')}")
                last_simulation = dict(metadata.get("last_simulation", {}) or {})
                if last_simulation:
                    print(f"max temp: {last_simulation.get('max_temp', 'N/A')} C")
                    print(f"violations: {len(list(last_simulation.get('violations', []) or []))}")

            _print_visualization_summary(orchestrator)
            return 0
    except KeyboardInterrupt:
        if orchestrator is not None:
            try:
                orchestrator.logger.save_summary(
                    status="INTERRUPTED",
                    final_iteration=int(last_iteration),
                    notes=f"{level_label} run interrupted by user",
                    extra={"optimization_mode": "agent_loop", "interrupted": True},
                )
            except Exception:
                pass
        print("[INTERRUPTED] run interrupted")
        return 130
    except Exception as exc:
        print(f"[ERROR] run failed: {exc}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        if orchestrator is not None:
            try:
                if hasattr(orchestrator, "sim_driver") and hasattr(orchestrator.sim_driver, "disconnect"):
                    orchestrator.sim_driver.disconnect()
            except Exception:
                pass
