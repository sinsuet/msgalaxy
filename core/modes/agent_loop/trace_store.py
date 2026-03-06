"""
agent_loop evolution trace row/materialization helpers.
"""

from __future__ import annotations

import csv
from datetime import datetime
from typing import Any, Dict, List


AGENT_LOOP_TRACE_HEADERS: List[str] = [
    "iteration",
    "timestamp",
    "max_temp",
    "min_clearance",
    "total_mass",
    "total_power",
    "num_violations",
    "is_safe",
    "solver_cost",
    "llm_tokens",
    "penalty_score",
    "state_id",
    "avg_temp",
    "min_temp",
    "temp_gradient",
    "cg_offset",
    "num_collisions",
    "penalty_violation",
    "penalty_temp",
    "penalty_clearance",
    "penalty_cg",
    "penalty_collision",
    "delta_penalty",
    "delta_cg_offset",
    "delta_max_temp",
    "delta_min_clearance",
    "effectiveness_score",
]


def _fmt_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return ""


def init_agent_loop_trace_csv(csv_path: str) -> None:
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(AGENT_LOOP_TRACE_HEADERS)


def append_agent_loop_trace_row(csv_path: str, row: List[Any]) -> None:
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def materialize_metrics_payload(data: Dict[str, Any]) -> List[Any]:
    payload = dict(data or {})
    return [
        payload.get("iteration", 0),
        payload.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        _fmt_float(payload.get("max_temp", 0), 2),
        _fmt_float(payload.get("min_clearance", 0), 2),
        _fmt_float(payload.get("total_mass", 0), 2),
        _fmt_float(payload.get("total_power", 0), 2),
        payload.get("num_violations", 0),
        payload.get("is_safe", False),
        _fmt_float(payload.get("solver_cost", 0), 4),
        payload.get("llm_tokens", 0),
        _fmt_float(payload.get("penalty_score", 0), 2),
        payload.get("state_id", ""),
        _fmt_float(payload.get("avg_temp", 0), 2),
        _fmt_float(payload.get("min_temp", 0), 2),
        _fmt_float(payload.get("temp_gradient", 0), 2),
        _fmt_float(payload.get("cg_offset", 0), 2),
        int(payload.get("num_collisions", 0)),
        _fmt_float(payload.get("penalty_violation", 0), 2),
        _fmt_float(payload.get("penalty_temp", 0), 2),
        _fmt_float(payload.get("penalty_clearance", 0), 2),
        _fmt_float(payload.get("penalty_cg", 0), 2),
        _fmt_float(payload.get("penalty_collision", 0), 2),
        _fmt_float(payload.get("delta_penalty", 0), 2),
        _fmt_float(payload.get("delta_cg_offset", 0), 2),
        _fmt_float(payload.get("delta_max_temp", 0), 2),
        _fmt_float(payload.get("delta_min_clearance", 0), 2),
        _fmt_float(payload.get("effectiveness_score", 0), 2),
    ]

