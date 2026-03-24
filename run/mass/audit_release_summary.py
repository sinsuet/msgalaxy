#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Print or export a lightweight release-audit summary for existing run directories.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Iterable, List

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from optimization.modes.mass.observability.release_audit import (
    build_release_audit_rollup,
    collect_release_audit_summary_rows,
)


def _collect_run_dirs(
    *,
    run_dirs: Iterable[str],
    runs_root: str,
    glob_pattern: str,
) -> List[str]:
    collected: List[str] = []
    seen = set()

    for raw in list(run_dirs or []):
        candidate = Path(raw).resolve(strict=False)
        if candidate.is_dir() and (candidate / "summary.json").exists():
            key = candidate.as_posix().lower()
            if key not in seen:
                seen.add(key)
                collected.append(str(candidate))

    if runs_root:
        root = Path(runs_root).resolve(strict=False)
        for candidate in sorted(root.glob(glob_pattern)):
            if not candidate.is_dir() or not (candidate / "summary.json").exists():
                continue
            key = candidate.as_posix().lower()
            if key in seen:
                continue
            seen.add(key)
            collected.append(str(candidate))

    return collected


def _write_csv(path: Path, rows: List[dict]) -> None:
    headers: List[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key in seen:
                continue
            seen.add(key)
            headers.append(str(key))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_markdown(path: Path, rows: List[dict], rollup: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append("# Release Audit Summary")
    lines.append("")
    lines.append(f"- Total runs: `{int(rollup.get('total_runs', 0) or 0)}`")
    lines.append(f"- Non-release runs: `{int(rollup.get('non_release_runs', 0) or 0)}`")
    by_status = dict(rollup.get("by_final_audit_status", {}) or {})
    if by_status:
        status_desc = ", ".join(
            f"`{key}`={int(value)}" for key, value in sorted(by_status.items())
        )
        lines.append(f"- By final audit status: {status_desc}")
    lines.append("")
    lines.append("## By Level")
    lines.append("")
    by_level = dict(rollup.get("by_level", {}) or {})
    if by_level:
        for level in sorted(by_level.keys()):
            payload = dict(by_level.get(level, {}) or {})
            total_runs = int(payload.pop("total_runs", 0) or 0)
            desc = ", ".join(
                f"`{key}`={int(value)}" for key, value in sorted(payload.items())
            )
            if desc:
                lines.append(f"- `{level}`: total=`{total_runs}`, {desc}")
            else:
                lines.append(f"- `{level}`: total=`{total_runs}`")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Non-Release Gaps")
    lines.append("")
    by_gap = dict(rollup.get("by_gap_category", {}) or {})
    non_release_rows = [
        row
        for row in rows
        if str(row.get("final_audit_status", "") or "").strip()
        != "release_grade_real_comsol_validated"
    ]
    if by_gap:
        gap_desc = ", ".join(
            f"`{key}`={int(value)}" for key, value in sorted(by_gap.items())
        )
        lines.append(f"- By gap category: {gap_desc}")
    if non_release_rows:
        lines.append("")
        lines.append("| Run | Level | Audit | Gap | Signature | Minimal remediation | Evidence |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for row in non_release_rows:
            lines.append(
                "| {run_dir} | {level} | {audit} | {gap} | {signature} | {remediation} | {evidence} |".format(
                    run_dir=str(row.get("run_dir", "") or ""),
                    level=str(row.get("level", "") or ""),
                    audit=str(row.get("final_audit_status", "") or ""),
                    gap=str(row.get("gap_category", "") or ""),
                    signature=str(row.get("primary_failure_signature", "") or ""),
                    remediation=str(row.get("minimal_remediation", "") or ""),
                    evidence=str(row.get("evidence_hint", "") or ""),
                )
            )
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Runs")
    lines.append("")
    lines.append("| Run | Mode | Level | Audit | Diagnosis | Gap | First feasible | COMSOL calls |")
    lines.append("| --- | --- | --- | --- | --- | --- | ---: | ---: |")
    for row in rows:
        lines.append(
            "| {run_dir} | {run_mode}->{execution_mode} | {level} | {audit} | {diag_status}:{diag_reason} | {gap} | {first_feasible} | {comsol_calls} |".format(
                run_dir=str(row.get("run_dir", "") or ""),
                run_mode=str(row.get("run_mode", "") or ""),
                execution_mode=str(row.get("execution_mode", "") or ""),
                level=str(row.get("level", "") or ""),
                audit=str(row.get("final_audit_status", "") or ""),
                diag_status=str(row.get("diagnosis_status", "") or ""),
                diag_reason=str(row.get("diagnosis_reason", "") or ""),
                gap=str(row.get("gap_category", "") or ""),
                first_feasible=str(row.get("first_feasible_eval", "") or ""),
                comsol_calls=str(row.get("comsol_calls_to_first_feasible", "") or ""),
            )
        )
    observable_rows = [
        row for row in rows if str(row.get("release_audit_table", "") or "").strip()
    ]
    lines.append("")
    lines.append("## Audit Tables")
    lines.append("")
    if observable_rows:
        lines.append("| Run | Release Audit |")
        lines.append("| --- | --- |")
        for row in observable_rows:
            lines.append(
                "| {run_dir} | {release_audit} |".format(
                    run_dir=str(row.get("run_dir", "") or ""),
                    release_audit=str(row.get("release_audit_table", "") or ""),
                )
            )
    else:
        lines.append("- (none)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize release audit fields for existing MaaS runs.",
    )
    parser.add_argument("run_dirs", nargs="*", help="Run directories to summarize.")
    parser.add_argument(
        "--runs-root",
        default="",
        help="Optional root directory for batch collection.",
    )
    parser.add_argument(
        "--glob",
        default="*",
        help="Directory glob under --runs-root.",
    )
    parser.add_argument(
        "--output-csv",
        default="",
        help="Optional CSV output path.",
    )
    parser.add_argument(
        "--output-md",
        default="",
        help="Optional Markdown output path.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of a compact text summary.",
    )
    parser.add_argument(
        "--only-non-release",
        action="store_true",
        help="Only include non-release-grade runs in the output.",
    )
    args = parser.parse_args()

    run_dirs = _collect_run_dirs(
        run_dirs=args.run_dirs,
        runs_root=args.runs_root,
        glob_pattern=args.glob,
    )
    if not run_dirs:
        parser.error("No run directories resolved.")

    rows = collect_release_audit_summary_rows(run_dirs)
    if args.only_non_release:
        rows = [
            row
            for row in rows
            if str(row.get("final_audit_status", "") or "").strip()
            != "release_grade_real_comsol_validated"
        ]
    rollup = build_release_audit_rollup(rows)
    if args.output_csv:
        output_path = Path(args.output_csv).resolve(strict=False)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(output_path, rows)
    if args.output_md:
        output_path = Path(args.output_md).resolve(strict=False)
        _write_markdown(output_path, rows, rollup)

    if args.json:
        print(
            json.dumps(
                {"rows": rows, "rollup": rollup},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        by_status = dict(rollup.get("by_final_audit_status", {}) or {})
        if by_status:
            print(
                "[rollup] "
                + ", ".join(
                    f"{key}={int(value)}" for key, value in sorted(by_status.items())
                )
            )
        for row in rows:
            print(
                f"{row['run_dir']} | "
                f"mode={row.get('run_mode', '')}->{row.get('execution_mode', '')} | "
                f"level={row['level']} | "
                f"audit={row['final_audit_status']} | "
                f"diag={row['diagnosis_status']}:{row['diagnosis_reason']} | "
                f"gap={row.get('gap_category', '')} | "
                f"first_feasible={row['first_feasible_eval']} | "
                f"comsol_calls={row['comsol_calls_to_first_feasible']}"
            )
        if args.output_csv:
            print(f"[OK] wrote {Path(args.output_csv).as_posix()}")
        if args.output_md:
            print(f"[OK] wrote {Path(args.output_md).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
