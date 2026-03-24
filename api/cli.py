"""
Scenario-driven CLI helpers.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from api.experiment_index import (
    find_experiment_dir,
    iter_experiment_dirs,
    load_json_if_exists,
    load_latest_index,
    resolve_experiments_root,
    serialize_experiment_dir,
)
from run.run_scenario import (
    REGISTRY_PATH,
    _load_executor,
    _load_registry,
    _resolve_abs_path,
    _resolve_registry_entry,
)
from workflow.scenario_runtime import load_runtime_config


if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def _discover_artifacts(exp_path: Path) -> list[tuple[str, str]]:
    summary = load_json_if_exists(exp_path / "summary.json")
    artifacts = dict(summary.get("artifacts", {}) or {})
    discovered: list[tuple[str, str]] = []
    for label, key in (
        ("Layout", "layout_final_figure"),
        ("Seed Layout", "layout_seed_figure"),
        ("STEP", "step_path"),
    ):
        raw = str(artifacts.get(key, "") or "")
        if raw:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = exp_path / candidate
            if candidate.exists():
                discovered.append((label, str(candidate)))
    return discovered


def cmd_optimize(args):
    registry = _load_registry(REGISTRY_PATH)
    entry = _resolve_registry_entry(registry, stack=args.stack, scenario=args.scenario)
    scenario_path = _resolve_abs_path(entry["scenario"])
    base_config_path = _resolve_abs_path(args.base_config or entry["base_config"])

    if bool(args.dry_run):
        print(
            json.dumps(
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
            )
        )
        return

    executor_cls = _load_executor(args.stack)
    executor = executor_cls(
        config=load_runtime_config(base_config_path),
        run_label=str(args.run_label or ""),
    )
    result = executor.run_scenario(scenario_path=str(scenario_path))
    print(
        json.dumps(
            {
                "status": result.summary.get("status", "UNKNOWN"),
                "run_dir": str(result.run_dir),
                "summary_path": str(result.run_dir / "summary.json"),
                "effective_profile": result.summary.get("effective_physics_profile", ""),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_list_experiments(args):
    exp_dir = resolve_experiments_root(args.exp_dir)

    if not exp_dir.exists():
        print(f"Experiments directory not found: {exp_dir}")
        return

    experiments = iter_experiment_dirs(exp_dir)
    experiments.sort(
        key=lambda path: (
            str(load_json_if_exists(path / "summary.json").get("run_started_at", "") or ""),
            serialize_experiment_dir(exp_dir, path),
        ),
        reverse=True,
    )

    if not experiments:
        print("No experiments found.")
        return

    print(f"\nFound {len(experiments)} experiments:\n")
    for exp in experiments[: args.limit]:
        summary = load_json_if_exists(exp / "summary.json")
        print(f"  - {serialize_experiment_dir(exp_dir, exp)}")
        print(f"    status: {str(summary.get('status', '') or 'UNKNOWN')}")
        print(f"    scenario: {str(summary.get('scenario_id', '') or 'n/a')}")
        print(f"    stack: {str(summary.get('stack', '') or summary.get('run_mode', '') or 'mass')}")


def cmd_show_experiment(args):
    exp_root = resolve_experiments_root(args.exp_dir)
    exp_path = find_experiment_dir(exp_root, args.exp_name)

    if exp_path is None or not exp_path.exists():
        print(f"Experiment not found: {args.exp_name}")
        return

    print(f"Experiment: {serialize_experiment_dir(exp_root, exp_path)}")

    summary = load_json_if_exists(exp_path / "summary.json")
    if summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("Summary not found.")

    report_file = exp_path / "report.md"
    if report_file.exists():
        print(f"\nReport: {serialize_experiment_dir(exp_root, report_file)}")

    for label, artifact_path in _discover_artifacts(exp_path):
        print(f"{label}: {serialize_experiment_dir(exp_root, artifact_path)}")


def cmd_latest_experiment(args):
    latest_payload = load_latest_index(args.exp_dir)
    if not latest_payload:
        print("Latest experiment not found.")
        return
    print(json.dumps(latest_payload, ensure_ascii=False, indent=2))


def cmd_add_knowledge(args):
    from optimization.knowledge.mass import MassRAGSystem

    rag = MassRAGSystem(
        api_key=args.api_key or "",
        knowledge_base_path=args.kb_path,
        enable_semantic=False,
    )

    item = rag.add_knowledge(
        title=args.title,
        content=args.content,
        category=args.category,
        metadata={},
    )

    print(f"Knowledge added: {item.item_id}")


def main():
    parser = argparse.ArgumentParser(description="MsGalaxy scenario CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    parser_opt = subparsers.add_parser("optimize", help="Run a scenario")
    parser_opt.add_argument("--stack", required=True, choices=["mass"])
    parser_opt.add_argument("--scenario", required=True, help="scenario id from registry")
    parser_opt.add_argument("--base-config", default="", help="optional stack base config override")
    parser_opt.add_argument("--run-label", default="", help="optional run label suffix")
    parser_opt.add_argument("--dry-run", action="store_true")
    parser_opt.set_defaults(func=cmd_optimize)

    parser_list = subparsers.add_parser("list", help="List experiments")
    parser_list.add_argument("--exp-dir", default="experiments", help="Experiments directory")
    parser_list.add_argument("--limit", type=int, default=10, help="Number of experiments to show")
    parser_list.set_defaults(func=cmd_list_experiments)

    parser_show = subparsers.add_parser("show", help="Show experiment details")
    parser_show.add_argument("exp_name", help="Experiment leaf name / relative path / 'latest'")
    parser_show.add_argument("--exp-dir", default="experiments", help="Experiments directory")
    parser_show.set_defaults(func=cmd_show_experiment)

    parser_latest = subparsers.add_parser("latest", help="Show latest experiment index")
    parser_latest.add_argument("--exp-dir", default="experiments", help="Experiments directory")
    parser_latest.set_defaults(func=cmd_latest_experiment)

    parser_kb = subparsers.add_parser("add-knowledge", help="Add knowledge to knowledge base")
    parser_kb.add_argument("--title", required=True, help="Knowledge title")
    parser_kb.add_argument("--content", required=True, help="Knowledge content")
    parser_kb.add_argument(
        "--category",
        choices=["standard", "case", "formula", "heuristic"],
        required=True,
        help="Knowledge category",
    )
    parser_kb.add_argument("--api-key", default="", help="Optional API key")
    parser_kb.add_argument("--kb-path", default="data/knowledge_base", help="Knowledge base path")
    parser_kb.set_defaults(func=cmd_add_knowledge)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
