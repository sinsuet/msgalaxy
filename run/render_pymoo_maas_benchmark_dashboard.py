#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Render benchmark dashboards from `run_pymoo_maas_benchmark_matrix.py` artifacts.

Inputs:
  - matrix_runs.csv
  - matrix_aggregate_profile_level.csv

Outputs (in benchmark dir by default):
  - dashboard_feasible_ratio.png
  - dashboard_best_cv.png
  - dashboard_first_feasible_eval.png
  - dashboard_summary.md
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import Dict, List

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing CSV: {path}")
    return pd.read_csv(path)


def _sorted_levels(levels: List[str]) -> List[str]:
    def _key(item: str):
        raw = str(item).strip().upper()
        if raw.startswith("L"):
            try:
                return (0, int(raw[1:]))
            except ValueError:
                return (1, raw)
        return (1, raw)

    return sorted([str(x) for x in levels], key=_key)


def _build_matrix(
    df: pd.DataFrame,
    *,
    algorithm: str,
    profiles: List[str],
    levels: List[str],
    metric: str,
) -> np.ndarray:
    matrix = np.full((len(profiles), len(levels)), np.nan, dtype=float)
    subset = df[df["algorithm"].astype(str).str.lower() == str(algorithm).lower()]
    for _, row in subset.iterrows():
        profile = str(row.get("profile", ""))
        level = str(row.get("level", ""))
        if profile not in profiles or level not in levels:
            continue
        i = profiles.index(profile)
        j = levels.index(level)
        try:
            matrix[i, j] = float(row.get(metric))
        except (TypeError, ValueError):
            matrix[i, j] = np.nan
    return matrix


def _render_feasible_ratio_heatmap(df: pd.DataFrame, output_path: Path) -> None:
    algorithms = sorted(df["algorithm"].dropna().astype(str).str.lower().unique().tolist())
    profiles = sorted(df["profile"].dropna().astype(str).unique().tolist())
    levels = _sorted_levels(df["level"].dropna().astype(str).unique().tolist())

    n = len(algorithms)
    ncols = min(3, max(1, n))
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(5.2 * ncols, 4.0 * nrows))
    axes_list = np.atleast_1d(axes).flatten()

    image_handle = None
    for idx, algorithm in enumerate(algorithms):
        ax = axes_list[idx]
        matrix = _build_matrix(
            df,
            algorithm=algorithm,
            profiles=profiles,
            levels=levels,
            metric="feasible_ratio",
        )
        image_handle = ax.imshow(
            np.nan_to_num(matrix, nan=-0.01),
            vmin=0.0,
            vmax=1.0,
            cmap="YlGnBu",
            aspect="auto",
        )
        ax.set_title(f"{algorithm.upper()} feasible_ratio")
        ax.set_xticks(np.arange(len(levels)))
        ax.set_xticklabels(levels)
        ax.set_yticks(np.arange(len(profiles)))
        ax.set_yticklabels(profiles)
        ax.tick_params(axis="x", rotation=20)

        for i in range(len(profiles)):
            for j in range(len(levels)):
                value = matrix[i, j]
                label = "-" if not np.isfinite(value) else f"{value:.2f}"
                ax.text(j, i, label, ha="center", va="center", fontsize=8, color="black")

    for idx in range(len(algorithms), len(axes_list)):
        axes_list[idx].axis("off")

    if image_handle is not None:
        fig.colorbar(image_handle, ax=axes_list.tolist(), shrink=0.9, label="Feasible Ratio")
    fig.suptitle("OP-MaaS Benchmark Dashboard: Feasible Ratio Heatmap", fontsize=12)
    fig.subplots_adjust(top=0.88, right=0.92, wspace=0.28, hspace=0.35)
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _render_metric_lineboards(
    df: pd.DataFrame,
    *,
    metric: str,
    title: str,
    y_label: str,
    output_path: Path,
) -> None:
    algorithms = sorted(df["algorithm"].dropna().astype(str).str.lower().unique().tolist())
    profiles = sorted(df["profile"].dropna().astype(str).unique().tolist())
    levels = _sorted_levels(df["level"].dropna().astype(str).unique().tolist())

    n = len(algorithms)
    ncols = min(3, max(1, n))
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(5.5 * ncols, 4.2 * nrows))
    axes_list = np.atleast_1d(axes).flatten()

    for idx, algorithm in enumerate(algorithms):
        ax = axes_list[idx]
        matrix = _build_matrix(
            df,
            algorithm=algorithm,
            profiles=profiles,
            levels=levels,
            metric=metric,
        )
        x = np.arange(len(levels), dtype=float)
        for i, profile in enumerate(profiles):
            row = matrix[i, :]
            if np.all(~np.isfinite(row)):
                continue
            ax.plot(x, row, marker="o", linewidth=1.6, label=profile)

        ax.set_title(algorithm.upper())
        ax.set_xlabel("Level")
        ax.set_ylabel(y_label)
        ax.set_xticks(x)
        ax.set_xticklabels(levels, rotation=20)
        ax.grid(True, alpha=0.25)
        if len(ax.lines) > 0:
            ax.legend(fontsize=7, loc="best")
        else:
            ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center", va="center", color="gray")

    for idx in range(len(algorithms), len(axes_list)):
        axes_list[idx].axis("off")

    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _write_markdown_summary(*, raw_df: pd.DataFrame, agg_df: pd.DataFrame, output_path: Path) -> None:
    lines: List[str] = []
    lines.append("# OP-MaaS Benchmark Dashboard Summary")
    lines.append("")
    lines.append(f"- runs_total: {int(len(raw_df))}")
    lines.append(f"- aggregate_rows: {int(len(agg_df))}")
    lines.append("")

    if len(agg_df) > 0:
        work = agg_df.copy()
        work["feasible_ratio"] = pd.to_numeric(work.get("feasible_ratio"), errors="coerce")
        work["best_cv_min_mean"] = pd.to_numeric(work.get("best_cv_min_mean"), errors="coerce")
        work = work.sort_values(["feasible_ratio", "best_cv_min_mean"], ascending=[False, True])

        lines.append("## Top Combinations")
        lines.append("")
        lines.append("| profile | algorithm | level | feasible_ratio | best_cv_min_mean | first_feasible_eval_mean |")
        lines.append("|---|---|---|---:|---:|---:|")
        top_rows = work.head(12)
        for _, row in top_rows.iterrows():
            lines.append(
                "| {profile} | {algorithm} | {level} | {fr:.3f} | {cv} | {ffe} |".format(
                    profile=row.get("profile", ""),
                    algorithm=row.get("algorithm", ""),
                    level=row.get("level", ""),
                    fr=float(row.get("feasible_ratio")) if pd.notna(row.get("feasible_ratio")) else 0.0,
                    cv=(
                        f"{float(row.get('best_cv_min_mean')):.4f}"
                        if pd.notna(row.get("best_cv_min_mean"))
                        else "-"
                    ),
                    ffe=(
                        f"{float(row.get('first_feasible_eval_mean')):.3f}"
                        if pd.notna(row.get("first_feasible_eval_mean"))
                        else "-"
                    ),
                )
            )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_dashboard(benchmark_dir: Path, output_dir: Path) -> Dict[str, str]:
    raw_csv = benchmark_dir / "matrix_runs.csv"
    agg_csv = benchmark_dir / "matrix_aggregate_profile_level.csv"

    raw_df = _load_csv(raw_csv)
    agg_df = _load_csv(agg_csv)
    output_dir.mkdir(parents=True, exist_ok=True)

    feasible_png = output_dir / "dashboard_feasible_ratio.png"
    best_cv_png = output_dir / "dashboard_best_cv.png"
    first_feasible_png = output_dir / "dashboard_first_feasible_eval.png"
    summary_md = output_dir / "dashboard_summary.md"

    _render_feasible_ratio_heatmap(agg_df, feasible_png)
    _render_metric_lineboards(
        agg_df,
        metric="best_cv_min_mean",
        title="OP-MaaS Benchmark Dashboard: Best CV Mean",
        y_label="best_cv_min_mean (lower better)",
        output_path=best_cv_png,
    )
    _render_metric_lineboards(
        agg_df,
        metric="first_feasible_eval_mean",
        title="OP-MaaS Benchmark Dashboard: First Feasible Eval Mean",
        y_label="first_feasible_eval_mean (lower better)",
        output_path=first_feasible_png,
    )
    _write_markdown_summary(raw_df=raw_df, agg_df=agg_df, output_path=summary_md)

    return {
        "dashboard_feasible_ratio": str(feasible_png),
        "dashboard_best_cv": str(best_cv_png),
        "dashboard_first_feasible_eval": str(first_feasible_png),
        "dashboard_summary": str(summary_md),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render OP-MaaS benchmark dashboards")
    parser.add_argument(
        "--benchmark-dir",
        required=True,
        help="Benchmark directory containing matrix_runs.csv and matrix_aggregate_profile_level.csv",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory for dashboard artifacts (default: benchmark-dir)",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    benchmark_dir = Path(args.benchmark_dir).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else benchmark_dir

    artifacts = render_dashboard(benchmark_dir=benchmark_dir, output_dir=output_dir)
    print("dashboard artifacts:")
    for key, value in artifacts.items():
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
