#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Rebuild report/visualization/table artifacts for existing run directories.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, List

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from optimization.modes.mass.observability.release_audit import (
    rebuild_run_release_audit_artifacts,
)


def _collect_run_dirs(
    *,
    run_dirs: Iterable[str],
    runs_root: str,
    glob_pattern: str,
) -> List[Path]:
    collected: List[Path] = []
    seen = set()

    for raw in list(run_dirs or []):
        candidate = Path(raw).resolve(strict=False)
        if candidate.is_dir() and (candidate / "summary.json").exists():
            key = candidate.as_posix().lower()
            if key not in seen:
                seen.add(key)
                collected.append(candidate)

    if runs_root:
        root = Path(runs_root).resolve(strict=False)
        for candidate in sorted(root.glob(glob_pattern)):
            if not candidate.is_dir() or not (candidate / "summary.json").exists():
                continue
            key = candidate.as_posix().lower()
            if key in seen:
                continue
            seen.add(key)
            collected.append(candidate)

    return collected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild release-audit-aligned artifacts for existing MaaS runs.",
    )
    parser.add_argument("run_dirs", nargs="*", help="Run directories to rebuild.")
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
        "--skip-visualizations",
        action="store_true",
        help="Do not regenerate visualization artifacts.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of a compact text summary.",
    )
    args = parser.parse_args()

    run_dirs = _collect_run_dirs(
        run_dirs=args.run_dirs,
        runs_root=args.runs_root,
        glob_pattern=args.glob,
    )
    if not run_dirs:
        parser.error("No run directories resolved.")

    results = []
    for run_dir in run_dirs:
        results.append(
            rebuild_run_release_audit_artifacts(
                str(run_dir),
                refresh_visualizations=not bool(args.skip_visualizations),
            )
        )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(
                f"[OK] {item['run_dir']} "
                f"audit={item['final_audit_status']} "
                f"first_feasible={item['first_feasible_eval']} "
                f"comsol_calls={item['comsol_calls_to_first_feasible']} "
                f"table={item['release_audit_table']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
