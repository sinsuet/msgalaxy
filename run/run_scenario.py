#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import io
import json
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
    parser = argparse.ArgumentParser(description="MsGalaxy scenario runner")
    parser.add_argument("--stack", required=True, choices=["mass"])
    parser.add_argument("--scenario", required=True, help="scenario id from registry")
    parser.add_argument("--base-config", default="", help="optional stack base config override")
    parser.add_argument("--run-label", default="", help="optional run label suffix")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _load_registry(path: Path) -> Dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _resolve_registry_entry(registry: Dict[str, Any], *, stack: str, scenario: str) -> Dict[str, Any]:
    stacks = dict(registry.get("stacks", {}) or {})
    stack_cfg = dict(stacks.get(str(stack), {}) or {})
    if not stack_cfg:
        raise ValueError(f"unknown_stack:{stack}")
    scenarios = dict(stack_cfg.get("scenarios", {}) or {})
    scenario_cfg = dict(scenarios.get(str(scenario), {}) or {})
    if not scenario_cfg:
        raise ValueError(f"unknown_scenario:{scenario}")
    return {
        "stack": str(stack),
        "mode": str(stack_cfg.get("mode", stack) or stack),
        "base_config": str(stack_cfg.get("base_config", "") or ""),
        "scenario": str(scenario_cfg.get("scenario", "") or ""),
        "description": str(scenario_cfg.get("description", "") or ""),
    }


def _resolve_abs_path(raw_path: str) -> Path:
    path = Path(str(raw_path))
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _load_executor(stack: str):
    if stack != "mass":
        raise ValueError(f"unsupported_stack:{stack}")
    from workflow.modes.mass.pipeline_service import MaaSPipelineService

    return MaaSPipelineService


def main(argv: List[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    registry = _load_registry(REGISTRY_PATH)
    entry = _resolve_registry_entry(registry, stack=args.stack, scenario=args.scenario)
    scenario_path = _resolve_abs_path(entry["scenario"])
    base_config_path = _resolve_abs_path(args.base_config or entry["base_config"])

    if bool(args.dry_run):
        print(json.dumps(
            {
                "stack": entry["stack"],
                "mode": entry["mode"],
                "scenario": args.scenario,
                "scenario_path": str(scenario_path),
                "base_config": str(base_config_path),
                "description": entry["description"],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    from workflow.scenario_runtime import load_runtime_config

    executor_cls = _load_executor(args.stack)
    executor = executor_cls(
        config=load_runtime_config(base_config_path),
        run_label=str(args.run_label or ""),
    )
    result = executor.run_scenario(scenario_path=str(scenario_path))
    print(json.dumps(
        {
            "status": result.summary.get("status", "UNKNOWN"),
            "execution_stage": result.summary.get("execution_stage", ""),
            "run_dir": str(result.run_dir),
            "summary_path": str(result.run_dir / "summary.json"),
            "effective_profile": result.summary.get("effective_physics_profile", ""),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
