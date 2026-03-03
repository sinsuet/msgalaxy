#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Run profile x level x seed benchmark matrix for pymoo_maas and summarize metrics.

Example:
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/run_pymoo_maas_benchmark_matrix.py \
    --backend simplified \
    --levels L3,L4 \
    --profiles baseline,meta_policy,operator_program,multi_fidelity \
    --seeds 42,43 \
    --max-iterations 4 \
    --pymoo-pop-size 24 \
    --pymoo-n-gen 12
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import yaml

# Add project root.
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Force UTF-8 stdout/stderr on Windows.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


LEVEL_BOM_MAP: Dict[str, str] = {
    "L1": "config/bom_L1_simple.json",
    "L2": "config/bom_L2_intermediate.json",
    "L3": "config/bom_L3_complex.json",
    "L4": "config/bom_L4_extreme.json",
    "L5": "config/bom_L5_stress.json",
    "L6": "config/bom_L6_heavy.json",
    "L7": "config/bom_L7_dense.json",
    "L8": "config/bom_L8_40components.json",
}

FEASIBLE_DIAG_STATUSES = {"feasible", "feasible_but_stalled"}
SUPPORTED_ALGORITHMS = {"nsga2", "nsga3", "moead"}


@dataclass
class RunOutcome:
    row: Dict[str, Any]
    summary: Dict[str, Any]


def _parse_csv_tokens(raw: str) -> List[str]:
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _parse_seed_list(raw: str) -> List[int]:
    seeds: List[int] = []
    for token in _parse_csv_tokens(raw):
        seeds.append(int(token))
    return seeds


def profile_overrides(profile: str) -> Dict[str, Any]:
    normalized = str(profile).strip().lower()
    if normalized == "baseline":
        return {
            "pymoo_maas_enable_meta_policy": False,
            "pymoo_maas_meta_policy_apply_runtime": False,
            "pymoo_maas_enable_operator_program": False,
            "pymoo_maas_search_space": "coordinate",
            "pymoo_maas_online_comsol_schedule_mode": "budget_only",
            "pymoo_maas_enable_mcts": False,
            "pymoo_maas_auto_relax": False,
            "pymoo_maas_retry_on_stall": False,
        }
    if normalized == "meta_policy":
        return {
            "pymoo_maas_enable_meta_policy": True,
            "pymoo_maas_meta_policy_apply_runtime": True,
            "pymoo_maas_enable_operator_program": False,
            "pymoo_maas_search_space": "coordinate",
            "pymoo_maas_online_comsol_schedule_mode": "budget_only",
            "pymoo_maas_enable_mcts": True,
            "pymoo_maas_auto_relax": True,
            "pymoo_maas_retry_on_stall": True,
        }
    if normalized == "operator_program":
        return {
            "pymoo_maas_enable_meta_policy": True,
            "pymoo_maas_meta_policy_apply_runtime": True,
            "pymoo_maas_enable_operator_program": True,
            "pymoo_maas_enable_operator_seed_population": True,
            "pymoo_maas_enable_operator_credit_bias": True,
            "pymoo_maas_search_space": "operator_program",
            "pymoo_maas_online_comsol_schedule_mode": "budget_only",
        }
    if normalized == "operator_program_seed_off":
        return {
            "pymoo_maas_enable_meta_policy": True,
            "pymoo_maas_meta_policy_apply_runtime": True,
            "pymoo_maas_enable_operator_program": True,
            "pymoo_maas_enable_operator_seed_population": False,
            "pymoo_maas_enable_operator_credit_bias": True,
            "pymoo_maas_search_space": "operator_program",
            "pymoo_maas_online_comsol_schedule_mode": "budget_only",
        }
    if normalized == "operator_program_credit_off":
        return {
            "pymoo_maas_enable_meta_policy": True,
            "pymoo_maas_meta_policy_apply_runtime": True,
            "pymoo_maas_enable_operator_program": True,
            "pymoo_maas_enable_operator_seed_population": True,
            "pymoo_maas_enable_operator_credit_bias": False,
            "pymoo_maas_search_space": "operator_program",
            "pymoo_maas_online_comsol_schedule_mode": "budget_only",
        }
    if normalized == "hybrid":
        return {
            "pymoo_maas_enable_meta_policy": True,
            "pymoo_maas_meta_policy_apply_runtime": True,
            "pymoo_maas_enable_operator_program": True,
            "pymoo_maas_search_space": "hybrid",
            "pymoo_maas_online_comsol_schedule_mode": "budget_only",
        }
    if normalized == "multi_fidelity":
        return {
            "pymoo_maas_enable_meta_policy": True,
            "pymoo_maas_meta_policy_apply_runtime": True,
            "pymoo_maas_enable_operator_program": True,
            "pymoo_maas_search_space": "hybrid",
            "pymoo_maas_online_comsol_schedule_mode": "ucb_topk",
        }
    raise ValueError(f"Unknown profile: {profile}")


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not (parsed == parsed and parsed != float("inf") and parsed != -float("inf")):
        return None
    return parsed


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    filtered = [float(v) for v in values if v is not None]
    if not filtered:
        return None
    return float(sum(filtered) / len(filtered))


def aggregate_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row.get("profile", "")),
                str(row.get("algorithm", "")),
                str(row.get("level", "")),
            )
        ].append(row)

    outputs: List[Dict[str, Any]] = []
    for (profile, algorithm, level), items in sorted(grouped.items()):
        total = int(len(items))
        completed = [x for x in items if str(x.get("status", "")).upper() != "ERROR"]
        feasible = [x for x in items if bool(x.get("diagnosis_feasible", False))]
        best_cv_valid_count = int(
            sum(1 for item in items if _safe_float(item.get("best_cv_min")) is not None)
        )
        best_cv_missing_count = int(max(total - best_cv_valid_count, 0))
        outputs.append(
            {
                "profile": profile,
                "algorithm": algorithm,
                "level": level,
                "runs_total": total,
                "runs_completed": int(len(completed)),
                "runs_feasible": int(len(feasible)),
                "feasible_ratio": (float(len(feasible)) / float(total)) if total > 0 else 0.0,
                "best_cv_min_mean": _mean(_safe_float(x.get("best_cv_min")) for x in items),
                "first_feasible_eval_mean": _mean(_safe_float(x.get("first_feasible_eval")) for x in items),
                "comsol_calls_to_first_feasible_mean": _mean(
                    _safe_float(x.get("comsol_calls_to_first_feasible")) for x in items
                ),
                "comsol_calls_per_feasible_attempt_mean": _mean(
                    _safe_float(x.get("comsol_calls_per_feasible_attempt")) for x in items
                ),
                "best_cv_valid_count": int(best_cv_valid_count),
                "best_cv_missing_count": int(best_cv_missing_count),
                "best_cv_missing_ratio": (
                    float(best_cv_missing_count) / float(total)
                    if total > 0 else 0.0
                ),
            }
        )
    return outputs


def _load_l1_helpers():
    from run.run_L1_simple import (  # local import to keep module import lightweight
        _build_deterministic_intent,
        _collect_component_ids_from_bom,
        _derive_adaptive_bounds_from_state,
        _load_workflow_orchestrator,
        _sanitize_deterministic_bound_args,
    )

    return {
        "build_intent": _build_deterministic_intent,
        "collect_ids": _collect_component_ids_from_bom,
        "derive_bounds": _derive_adaptive_bounds_from_state,
        "load_orchestrator": _load_workflow_orchestrator,
        "sanitize_bounds": _sanitize_deterministic_bound_args,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="pymoo_maas benchmark matrix runner")
    parser.add_argument(
        "--profiles",
        default=(
            "baseline,meta_policy,operator_program,operator_program_seed_off,"
            "operator_program_credit_off,hybrid,multi_fidelity"
        ),
        help="Comma-separated profiles",
    )
    parser.add_argument(
        "--levels",
        default="L1,L2,L3,L4,L5,L6,L7",
        help="Comma-separated levels",
    )
    parser.add_argument(
        "--algorithms",
        default="nsga2,nsga3,moead",
        help="Comma-separated pymoo algorithms",
    )
    parser.add_argument(
        "--seeds",
        default="42,43,44,45,46",
        help="Comma-separated integer seeds",
    )
    parser.add_argument(
        "--base-config",
        default=str(PROJECT_ROOT / "config" / "system.yaml"),
        help="Base config YAML path",
    )
    parser.add_argument(
        "--backend",
        choices=["comsol", "simplified", "matlab"],
        default="simplified",
        help="Simulation backend for benchmark runs",
    )
    parser.add_argument(
        "--thermal-evaluator-mode",
        choices=["proxy", "online_comsol"],
        default="online_comsol",
        help="Thermal evaluator mode for pymoo_maas",
    )
    parser.add_argument("--max-iterations", type=int, default=10, help="Optimization max iterations")
    parser.add_argument("--pymoo-pop-size", type=int, default=64, help="NSGA-II population size")
    parser.add_argument("--pymoo-n-gen", type=int, default=40, help="NSGA-II generations")
    parser.add_argument(
        "--online-comsol-eval-budget",
        type=int,
        default=24,
        help="online_comsol evaluation budget (<=0 means unlimited)",
    )
    parser.add_argument(
        "--schedule-top-fraction",
        type=float,
        default=0.20,
        help="Scheduler top fraction for ucb_topk profile",
    )
    parser.add_argument(
        "--schedule-explore-prob",
        type=float,
        default=0.05,
        help="Scheduler explore probability for ucb_topk profile",
    )
    parser.add_argument(
        "--schedule-uncertainty-weight",
        type=float,
        default=0.35,
        help="Scheduler uncertainty weight for ucb_topk profile",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "docs" / "benchmarks"),
        help="Directory to write benchmark report artifacts",
    )
    parser.add_argument(
        "--experiment-tag",
        default="",
        help="Optional run tag for output folder naming",
    )
    parser.add_argument(
        "--skip-dashboard",
        action="store_true",
        help="Skip post-run dashboard rendering",
    )
    parser.add_argument(
        "--dashboard-output-dir",
        default="",
        help="Optional output directory for dashboard artifacts (default: benchmark output dir)",
    )
    parser.add_argument(
        "--log-base-dir",
        default="",
        help="Optional override for logging.base_dir in runtime config",
    )
    parser.add_argument(
        "--enable-semantic",
        action="store_true",
        help="Enable semantic knowledge retrieval (default off)",
    )
    parser.add_argument(
        "--enable-physics-audit",
        action="store_true",
        help="Enable top-k physics audit (default off for speed)",
    )
    parser.add_argument(
        "--use-llm-intent",
        action="store_true",
        help="Use LLM modeling intent (default deterministic intent)",
    )
    parser.add_argument(
        "--deterministic-move-ratio",
        type=float,
        default=0.45,
        help="Deterministic intent local movement ratio",
    )
    parser.add_argument(
        "--deterministic-min-delta-mm",
        type=float,
        default=20.0,
        help="Deterministic intent min delta (mm)",
    )
    parser.add_argument(
        "--deterministic-max-delta-mm",
        type=float,
        default=220.0,
        help="Deterministic intent max delta (mm)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan and skip execution")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop matrix on first execution error")
    return parser


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers: List[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                headers.append(str(key))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_markdown_report(
    path: Path,
    *,
    args: argparse.Namespace,
    run_rows: List[Dict[str, Any]],
    aggregate: List[Dict[str, Any]],
) -> None:
    lines: List[str] = []
    lines.append("# Pymoo MaaS Benchmark Matrix")
    lines.append("")
    lines.append(f"- generated_at: {datetime.now().isoformat()}")
    lines.append(f"- backend: {args.backend}")
    lines.append(f"- thermal_evaluator_mode: {args.thermal_evaluator_mode}")
    lines.append(f"- profiles: {','.join(_parse_csv_tokens(args.profiles))}")
    lines.append(f"- levels: {','.join(_parse_csv_tokens(args.levels))}")
    lines.append(f"- seeds: {','.join(str(s) for s in _parse_seed_list(args.seeds))}")
    lines.append("")
    lines.append("## Aggregate (profile x algorithm x level)")
    lines.append("")
    lines.append(
        "| profile | algorithm | level | runs_total | runs_feasible | feasible_ratio | "
        "best_cv_min_mean | best_cv_missing | first_feasible_eval_mean | comsol_calls_to_first_feasible_mean |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in aggregate:
        lines.append(
            "| {profile} | {algorithm} | {level} | {runs_total} | {runs_feasible} | {feasible_ratio:.3f} | "
            "{best_cv_min_mean} | {best_cv_missing} | {first_feasible_eval_mean} | {comsol_calls_to_first_feasible_mean} |".format(
                profile=row.get("profile", ""),
                algorithm=row.get("algorithm", ""),
                level=row.get("level", ""),
                runs_total=int(row.get("runs_total", 0)),
                runs_feasible=int(row.get("runs_feasible", 0)),
                feasible_ratio=float(row.get("feasible_ratio", 0.0)),
                best_cv_min_mean=(
                    f"{float(row['best_cv_min_mean']):.4f}"
                    if row.get("best_cv_min_mean") is not None
                    else "-"
                ),
                best_cv_missing=(
                    f"{int(row.get('best_cv_missing_count', 0))}/{int(row.get('runs_total', 0))}"
                ),
                first_feasible_eval_mean=(
                    f"{float(row['first_feasible_eval_mean']):.3f}"
                    if row.get("first_feasible_eval_mean") is not None
                    else "-"
                ),
                comsol_calls_to_first_feasible_mean=(
                    f"{float(row['comsol_calls_to_first_feasible_mean']):.3f}"
                    if row.get("comsol_calls_to_first_feasible_mean") is not None
                    else "-"
                ),
            )
        )
    lines.append("")
    lines.append(f"## Raw Runs ({len(run_rows)})")
    lines.append("")
    lines.append("| profile | algorithm | level | seed | status | diagnosis_status | best_cv_min | first_feasible_eval | run_dir |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for row in run_rows:
        lines.append(
            "| {profile} | {algorithm} | {level} | {seed} | {status} | {diagnosis} | {best_cv} | {first_feasible} | {run_dir} |".format(
                profile=row.get("profile", ""),
                algorithm=row.get("algorithm", ""),
                level=row.get("level", ""),
                seed=row.get("seed", ""),
                status=row.get("status", ""),
                diagnosis=row.get("diagnosis_status", ""),
                best_cv=row.get("best_cv_min", "-"),
                first_feasible=row.get("first_feasible_eval", "-"),
                run_dir=row.get("run_dir", ""),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_dashboard_section(md_path: Path, artifacts: Dict[str, str]) -> None:
    if not artifacts:
        return
    lines = []
    lines.append("")
    lines.append("## Dashboard Artifacts")
    lines.append("")
    for key in sorted(artifacts.keys()):
        lines.append(f"- {key}: {artifacts[key]}")
    with md_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _render_dashboard_if_enabled(args: argparse.Namespace, output_root: Path) -> Dict[str, str]:
    if bool(getattr(args, "skip_dashboard", False)):
        return {}

    try:
        from run.render_pymoo_maas_benchmark_dashboard import render_dashboard
    except Exception:
        return {}

    output_dir_raw = str(getattr(args, "dashboard_output_dir", "") or "").strip()
    dashboard_output_dir = Path(output_dir_raw).resolve() if output_dir_raw else output_root
    dashboard_output_dir.mkdir(parents=True, exist_ok=True)
    try:
        return dict(
            render_dashboard(
                benchmark_dir=output_root,
                output_dir=dashboard_output_dir,
            )
            or {}
        )
    except Exception:
        return {}


def _build_runtime_config(
    *,
    base_config_path: Path,
    args: argparse.Namespace,
    profile: str,
    algorithm: str,
    seed: int,
    runtime_config_path: Path,
) -> Dict[str, Any]:
    cfg = yaml.safe_load(base_config_path.read_text(encoding="utf-8"))
    cfg.setdefault("optimization", {})
    cfg.setdefault("simulation", {})
    cfg.setdefault("knowledge", {})
    cfg.setdefault("logging", {})
    cfg.setdefault("openai", {})

    opt_cfg = cfg["optimization"]
    sim_cfg = cfg["simulation"]
    know_cfg = cfg["knowledge"]
    log_cfg = cfg["logging"]
    openai_cfg = cfg["openai"]

    opt_cfg["mode"] = "pymoo_maas"
    opt_cfg["max_iterations"] = int(args.max_iterations)
    opt_cfg["pymoo_seed"] = int(seed)
    opt_cfg["pymoo_algorithm"] = str(algorithm).strip().lower()
    opt_cfg["pymoo_pop_size"] = int(max(2, args.pymoo_pop_size))
    opt_cfg["pymoo_n_gen"] = int(max(1, args.pymoo_n_gen))
    opt_cfg["pymoo_maas_thermal_evaluator_mode"] = str(args.thermal_evaluator_mode)
    opt_cfg["pymoo_maas_enable_physics_audit"] = bool(args.enable_physics_audit)
    opt_cfg["pymoo_maas_online_comsol_eval_budget"] = int(args.online_comsol_eval_budget)
    opt_cfg["pymoo_maas_online_comsol_schedule_top_fraction"] = float(
        max(0.01, min(1.0, args.schedule_top_fraction))
    )
    opt_cfg["pymoo_maas_online_comsol_schedule_explore_prob"] = float(
        max(0.0, min(1.0, args.schedule_explore_prob))
    )
    opt_cfg["pymoo_maas_online_comsol_schedule_uncertainty_weight"] = float(
        max(0.0, min(5.0, args.schedule_uncertainty_weight))
    )
    opt_cfg["pymoo_maas_enable_semantic_zones"] = bool(args.enable_semantic)
    know_cfg["enable_semantic"] = bool(args.enable_semantic)
    sim_cfg["backend"] = str(args.backend)
    openai_cfg["model"] = "qwen3-max"

    profile_cfg = profile_overrides(profile)
    for key, value in profile_cfg.items():
        opt_cfg[key] = value

    if args.log_base_dir:
        log_cfg["base_dir"] = str(args.log_base_dir)

    runtime_config_path.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return cfg


def _read_summary(run_dir: Path) -> Dict[str, Any]:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return {}
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_deterministic_patch(
    *,
    orchestrator: Any,
    bom_file: str,
    level: str,
    profile: str,
    seed: int,
    helpers: Dict[str, Callable[..., Any]],
    args: argparse.Namespace,
) -> None:
    component_ids = helpers["collect_ids"](bom_file)
    preview_state = orchestrator._initialize_design_state(str(bom_file))
    ratio, min_delta, max_delta = helpers["sanitize_bounds"](
        movement_ratio=args.deterministic_move_ratio,
        min_delta_mm=args.deterministic_min_delta_mm,
        max_delta_mm=args.deterministic_max_delta_mm,
    )
    adaptive_bounds = helpers["derive_bounds"](
        preview_state,
        movement_ratio=ratio,
        min_delta_mm=min_delta,
        max_delta_mm=max_delta,
    )

    def _patched_generate_modeling_intent(context, runtime_constraints=None, requirement_text=""):
        intent = helpers["build_intent"](
            component_ids,
            runtime_constraints or {},
            variable_bounds=adaptive_bounds,
        )
        intent.intent_id = f"INTENT_{level}_{profile}_SEED_{seed}"
        intent.notes = f"benchmark_deterministic_{level}_{profile}"
        return intent

    orchestrator.meta_reasoner.generate_modeling_intent = _patched_generate_modeling_intent


def _run_one(
    *,
    args: argparse.Namespace,
    level: str,
    profile: str,
    algorithm: str,
    seed: int,
    output_root: Path,
    helpers: Dict[str, Callable[..., Any]],
) -> RunOutcome:
    bom_rel = LEVEL_BOM_MAP[level]
    bom_file = str((PROJECT_ROOT / bom_rel).resolve())
    started_at = time.perf_counter()
    run_error = ""
    summary: Dict[str, Any] = {}
    run_dir = ""
    status = "ERROR"
    diagnosis_status = ""
    diagnosis_reason = ""
    maas_attempt_count: Optional[int] = None
    final_iteration: Optional[int] = None

    if args.dry_run:
        row = {
            "profile": profile,
            "algorithm": algorithm,
            "level": level,
            "seed": int(seed),
            "status": "DRY_RUN",
            "run_dir": "",
            "bom_file": bom_file,
            "diagnosis_status": "",
            "diagnosis_reason": "",
            "diagnosis_feasible": False,
            "feasible_rate": None,
            "best_cv_min": None,
            "first_feasible_eval": None,
            "comsol_calls_to_first_feasible": None,
            "comsol_calls_per_feasible_attempt": None,
            "physics_pass_rate_topk": None,
            "meta_policy_runtime_events": None,
            "meta_policy_runtime_applied_events": None,
            "maas_attempt_count": None,
            "final_iteration": None,
            "elapsed_sec": 0.0,
            "error": "",
        }
        return RunOutcome(row=row, summary={})

    orchestrator = None
    try:
        with tempfile.TemporaryDirectory(prefix="msgalaxy_benchmark_cfg_") as tmp:
            runtime_cfg = Path(tmp) / f"runtime_{level}_{profile}_{seed}.yaml"
            _build_runtime_config(
                base_config_path=Path(args.base_config).resolve(),
                args=args,
                profile=profile,
                algorithm=algorithm,
                seed=seed,
                runtime_config_path=runtime_cfg,
            )
            WorkflowOrchestrator = helpers["load_orchestrator"]()
            orchestrator = WorkflowOrchestrator(str(runtime_cfg))
            if not args.use_llm_intent:
                _build_deterministic_patch(
                    orchestrator=orchestrator,
                    bom_file=bom_file,
                    level=level,
                    profile=profile,
                    seed=seed,
                    helpers=helpers,
                    args=args,
                )

            final_state = orchestrator.run_optimization(
                bom_file=bom_file,
                max_iterations=int(args.max_iterations),
            )
            run_dir = str(Path(orchestrator.logger.run_dir).resolve())
            summary = _read_summary(Path(run_dir))
            status = str(summary.get("status", "UNKNOWN"))

            metadata = dict(getattr(final_state, "metadata", {}) or {})
            diagnosis = dict(metadata.get("solver_diagnosis", {}) or {})
            diagnosis_status = str(summary.get("diagnosis_status", diagnosis.get("status", "")))
            diagnosis_reason = str(summary.get("diagnosis_reason", diagnosis.get("reason", "")))
            maas_attempt_count = int(
                summary.get(
                    "maas_attempt_count",
                    metadata.get("maas_attempt_count", 0),
                )
                or 0
            )
            final_iteration = int(getattr(final_state, "iteration", summary.get("final_iteration", 0)) or 0)
    except Exception as exc:
        run_error = str(exc)
    finally:
        if orchestrator is not None:
            try:
                if hasattr(orchestrator, "sim_driver") and hasattr(orchestrator.sim_driver, "disconnect"):
                    orchestrator.sim_driver.disconnect()
            except Exception:
                pass

    elapsed = time.perf_counter() - started_at
    diagnosis_feasible = str(diagnosis_status).strip().lower() in FEASIBLE_DIAG_STATUSES
    row = {
        "profile": profile,
        "algorithm": algorithm,
        "level": level,
        "seed": int(seed),
        "status": status if not run_error else "ERROR",
        "run_dir": run_dir,
        "bom_file": bom_file,
        "diagnosis_status": diagnosis_status,
        "diagnosis_reason": diagnosis_reason,
        "diagnosis_feasible": bool(diagnosis_feasible),
        "feasible_rate": summary.get("feasible_rate"),
        "best_cv_min": summary.get("best_cv_min"),
        "best_cv_min_source": summary.get("best_cv_min_source"),
        "first_feasible_eval": summary.get("first_feasible_eval"),
        "comsol_calls_to_first_feasible": summary.get("comsol_calls_to_first_feasible"),
        "comsol_calls_per_feasible_attempt": summary.get("comsol_calls_per_feasible_attempt"),
        "physics_pass_rate_topk": summary.get("physics_pass_rate_topk"),
        "meta_policy_runtime_events": summary.get("meta_policy_runtime_events"),
        "meta_policy_runtime_applied_events": summary.get("meta_policy_runtime_applied_events"),
        "maas_attempt_count": maas_attempt_count,
        "final_iteration": final_iteration,
        "elapsed_sec": round(float(elapsed), 4),
        "error": run_error,
    }
    return RunOutcome(row=row, summary=summary)


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    profiles = [p.lower() for p in _parse_csv_tokens(args.profiles)]
    algorithms = [a.lower() for a in _parse_csv_tokens(args.algorithms)]
    levels = [lvl.upper() for lvl in _parse_csv_tokens(args.levels)]
    seeds = _parse_seed_list(args.seeds)

    for profile in profiles:
        profile_overrides(profile)
    unknown_algorithms = [algo for algo in algorithms if algo not in SUPPORTED_ALGORITHMS]
    if unknown_algorithms:
        raise ValueError(
            f"Unknown algorithms: {unknown_algorithms}. Supported: {sorted(SUPPORTED_ALGORITHMS)}"
        )
    unknown_levels = [lvl for lvl in levels if lvl not in LEVEL_BOM_MAP]
    if unknown_levels:
        raise ValueError(f"Unknown levels: {unknown_levels}. Supported: {sorted(LEVEL_BOM_MAP.keys())}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = str(args.experiment_tag).strip() or timestamp
    output_root = Path(args.output_dir).resolve() / f"pymoo_maas_benchmark_{tag}"
    output_root.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print("pymoo_maas benchmark matrix")
    print("=" * 80)
    print(f"output: {output_root}")
    print(f"backend: {args.backend}")
    print(f"thermal_evaluator_mode: {args.thermal_evaluator_mode}")
    print(f"profiles: {profiles}")
    print(f"algorithms: {algorithms}")
    print(f"levels: {levels}")
    print(f"seeds: {seeds}")
    print(f"dry_run: {args.dry_run}")
    print("=" * 80)

    helpers = _load_l1_helpers()
    rows: List[Dict[str, Any]] = []

    total_runs = len(profiles) * len(algorithms) * len(levels) * len(seeds)
    run_index = 0
    for profile in profiles:
        for algorithm in algorithms:
            for level in levels:
                for seed in seeds:
                    run_index += 1
                    print(
                        f"[{run_index}/{total_runs}] profile={profile} "
                        f"algorithm={algorithm} level={level} seed={seed}"
                    )
                    outcome = _run_one(
                        args=args,
                        level=level,
                        profile=profile,
                        algorithm=algorithm,
                        seed=seed,
                        output_root=output_root,
                        helpers=helpers,
                    )
                    row = dict(outcome.row)
                    rows.append(row)
                    print(
                        "  -> status={status}, diagnosis={diag}, best_cv_min={cv}, "
                        "first_feasible_eval={ffe}, run_dir={run_dir}".format(
                            status=row.get("status", ""),
                            diag=row.get("diagnosis_status", ""),
                            cv=row.get("best_cv_min", None),
                            ffe=row.get("first_feasible_eval", None),
                            run_dir=row.get("run_dir", ""),
                        )
                    )
                    if row.get("error"):
                        print(f"  -> error: {row['error']}")
                        if args.stop_on_error:
                            print("stop_on_error enabled; terminating benchmark early.")
                            break
                if args.stop_on_error and rows and rows[-1].get("error"):
                    break
            if args.stop_on_error and rows and rows[-1].get("error"):
                break
        if args.stop_on_error and rows and rows[-1].get("error"):
            break

    aggregate = aggregate_rows(rows)

    raw_jsonl_path = output_root / "matrix_runs.jsonl"
    raw_csv_path = output_root / "matrix_runs.csv"
    agg_csv_path = output_root / "matrix_aggregate_profile_level.csv"
    md_path = output_root / "matrix_report.md"

    with raw_jsonl_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _write_csv(raw_csv_path, rows)
    _write_csv(agg_csv_path, aggregate)
    _write_markdown_report(
        md_path,
        args=args,
        run_rows=rows,
        aggregate=aggregate,
    )
    dashboard_artifacts = _render_dashboard_if_enabled(args, output_root)
    _append_dashboard_section(md_path, dashboard_artifacts)

    print()
    print("artifacts:")
    print(f"- {raw_jsonl_path}")
    print(f"- {raw_csv_path}")
    print(f"- {agg_csv_path}")
    print(f"- {md_path}")
    for key in sorted(dashboard_artifacts.keys()):
        print(f"- {key}: {dashboard_artifacts[key]}")
    print()
    print("aggregate:")
    for row in aggregate:
        print(
            "  profile={profile} algorithm={algorithm} level={level} feasible={runs_feasible}/{runs_total} "
            "feasible_ratio={ratio:.3f} best_cv_min_mean={cv}".format(
                profile=row.get("profile", ""),
                algorithm=row.get("algorithm", ""),
                level=row.get("level", ""),
                runs_feasible=int(row.get("runs_feasible", 0)),
                runs_total=int(row.get("runs_total", 0)),
                ratio=float(row.get("feasible_ratio", 0.0)),
                cv=(
                    f"{float(row['best_cv_min_mean']):.4f}"
                    if row.get("best_cv_min_mean") is not None
                    else "-"
                ),
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
