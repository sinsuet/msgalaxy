#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Unified stack/level scenario dispatcher.
"""

from __future__ import annotations

import argparse
import importlib
import io
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "scenarios" / "registry.yaml"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MsGalaxy unified scenario runner")
    parser.add_argument("--stack", required=True, help="stack key in scenario registry")
    parser.add_argument("--level", required=True, help="level key in scenario registry")
    parser.add_argument("--mode", default="", help="optional mode override")
    parser.add_argument(
        "--backend",
        default="simplified",
        choices=["comsol", "simplified"],
        help="simulation backend override",
    )
    parser.add_argument(
        "--thermal-evaluator-mode",
        default=None,
        choices=["proxy", "online_comsol"],
        help="thermal evaluator override for MaaS modes",
    )
    parser.add_argument("--bom-file", default="", help="optional BOM override")
    parser.add_argument("--base-config", default="", help="optional base config override")
    parser.add_argument(
        "--llm-profile",
        default="",
        help="optional LLM profile override (passed to stack runner)",
    )
    parser.add_argument(
        "--run-label",
        default="",
        help="optional run label override (passed to stack runner)",
    )
    parser.add_argument(
        "--run-naming-strategy",
        default="",
        choices=["", "compact", "verbose"],
        help="optional run naming strategy override",
    )
    parser.add_argument("--max-iterations", type=int, default=0, help="optional max-iterations override")
    parser.add_argument("--disable-physics-audit", action="store_true")
    parser.add_argument("--disable-semantic", action="store_true")
    parser.add_argument("--deterministic-intent", action="store_true")
    parser.add_argument("--llm-intent", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _load_registry(path: Path) -> Dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return dict(payload or {})


def _resolve_entry(registry: Dict[str, Any], *, stack: str, level: str) -> Dict[str, Any]:
    stacks = dict(registry.get("stacks", {}) or {})
    stack_key = str(stack).strip()
    level_key = str(level).strip().upper()
    if stack_key not in stacks:
        raise ValueError(f"unknown stack '{stack_key}', available={sorted(stacks.keys())}")

    stack_cfg = dict(stacks.get(stack_key, {}) or {})
    levels = dict(stack_cfg.get("levels", {}) or {})
    if level_key not in levels:
        raise ValueError(
            f"unknown level '{level_key}' for stack '{stack_key}', "
            f"available={sorted(levels.keys())}"
        )

    level_cfg = dict(levels.get(level_key, {}) or {})
    resolved = {
        "stack": stack_key,
        "level": level_key,
        "mode": str(stack_cfg.get("mode", "")).strip(),
        "base_config": str(stack_cfg.get("base_config", "")).strip(),
    }
    resolved.update(level_cfg)
    return resolved


def _normalize_path_for_compare(path_value: str) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    path_obj = Path(raw)
    if not path_obj.is_absolute():
        path_obj = (PROJECT_ROOT / path_obj).resolve()
    else:
        path_obj = path_obj.resolve()
    return path_obj.as_posix().lower()


def _expected_bom_prefix(stack: str) -> str:
    if stack == "agent_loop":
        return "config/bom/agent_loop"
    if stack == "vop_maas":
        return "config/bom/mass"
    return "config/bom/mass"


def _expected_base_prefix(stack: str) -> str:
    return f"config/system/{stack}"


def _resolve_script_args(args: Any, entry: Dict[str, Any]) -> List[str]:
    stack = str(entry.get("stack", "")).strip()
    level = str(entry.get("level", "")).strip().upper()
    expected_mode = str(entry.get("mode", "")).strip()
    requested_mode = str(getattr(args, "mode", "") or "").strip()
    if requested_mode and requested_mode != expected_mode:
        raise ValueError(
            f"stack '{stack}' expects mode '{expected_mode}', got '{requested_mode}'"
        )
    resolved_mode = requested_mode or expected_mode

    registry_bom = str(entry.get("bom", "")).strip()
    requested_bom = str(getattr(args, "bom_file", "") or "").strip()
    bom_prefix = _expected_bom_prefix(stack)
    if requested_bom:
        requested_bom_norm = _normalize_path_for_compare(requested_bom)
        expected_bom_norm = _normalize_path_for_compare(bom_prefix)
        if not requested_bom_norm.startswith(expected_bom_norm):
            raise ValueError(
                f"stack '{stack}' expects bom path under '{bom_prefix}', got '{requested_bom}'"
            )
        resolved_bom = requested_bom
    else:
        resolved_bom = registry_bom

    registry_base = str(entry.get("base_config", "")).strip()
    requested_base = str(getattr(args, "base_config", "") or "").strip()
    base_prefix = _expected_base_prefix(stack)
    if requested_base:
        requested_base_norm = _normalize_path_for_compare(requested_base)
        expected_base_norm = _normalize_path_for_compare(base_prefix)
        if not requested_base_norm.startswith(expected_base_norm):
            raise ValueError(
                f"stack '{stack}' expects base_config path under '{base_prefix}', got '{requested_base}'"
            )
        resolved_base = requested_base
    else:
        resolved_base = registry_base

    configured_iterations = int(entry.get("max_iterations", 0) or 0)
    requested_iterations = int(getattr(args, "max_iterations", 0) or 0)
    resolved_iterations = requested_iterations if requested_iterations > 0 else configured_iterations

    script_args: List[str] = [
        "--mode",
        str(resolved_mode),
        "--backend",
        str(getattr(args, "backend", "simplified")),
        "--bom-file",
        str(resolved_bom),
        "--base-config",
        str(resolved_base),
    ]
    if resolved_iterations > 0:
        script_args.extend(["--max-iterations", str(int(resolved_iterations))])

    thermal_mode = getattr(args, "thermal_evaluator_mode", None)
    if thermal_mode:
        script_args.extend(["--thermal-evaluator-mode", str(thermal_mode)])

    if bool(getattr(args, "disable_physics_audit", False)):
        script_args.append("--disable-physics-audit")
    if bool(getattr(args, "disable_semantic", False)):
        script_args.append("--disable-semantic")

    deterministic_default = bool(entry.get("deterministic_intent", False))
    if deterministic_default or bool(getattr(args, "deterministic_intent", False)):
        script_args.append("--deterministic-intent")

    if (
        bool(getattr(args, "llm_intent", False))
        and level != "L1"
        and resolved_mode == "mass"
    ):
        script_args.append("--use-llm-intent")

    run_label = str(getattr(args, "run_label", "") or "").strip()
    if run_label:
        script_args.extend(["--run-label", run_label])
    naming_strategy = str(getattr(args, "run_naming_strategy", "") or "").strip().lower()
    if naming_strategy in {"compact", "verbose"}:
        script_args.extend(["--run-naming-strategy", naming_strategy])
    llm_profile = str(getattr(args, "llm_profile", "") or "").strip()
    if llm_profile:
        script_args.extend(["--llm-profile", llm_profile])

    return script_args


def _invoke_runner(runner_module_path: str, script_args: List[str]) -> int:
    module = importlib.import_module(runner_module_path)
    main_fn = getattr(module, "main", None)
    if main_fn is None:
        raise ValueError(f"runner module '{runner_module_path}' has no main(argv) entry")
    return int(main_fn(script_args))


def main(argv: List[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    registry = _load_registry(REGISTRY_PATH)
    entry = _resolve_entry(registry, stack=args.stack, level=args.level)
    script_args = _resolve_script_args(args, entry)

    if bool(args.dry_run):
        print("=" * 80)
        print("MsGalaxy Unified Scenario Runner")
        print("=" * 80)
        print(
            f"stack={entry['stack']} level={entry['level']} "
            f"runner={entry.get('runner', '')}"
        )
        print(f"mode={entry.get('mode', '')} backend={args.backend}")
        print(f"bom={entry.get('bom', '')}")
        print(f"base_config={entry.get('base_config', '')}")
        print(f"argv={' '.join(script_args)}")
        print("=" * 80)
        return 0

    return _invoke_runner(str(entry.get("runner", "")), script_args)


if __name__ == "__main__":
    sys.exit(main())
