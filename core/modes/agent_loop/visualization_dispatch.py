"""
agent_loop visualization dispatch helpers.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict


def render_agent_loop_artifacts(
    *,
    csv_path: str,
    viz_dir: str,
    plot_evolution_trace: Callable[[str, str], Any],
) -> Dict[str, Any]:
    """
    Render agent_loop-specific visualization artifacts.

    Returns:
        empty timeline report dict for interface consistency.
    """
    if os.path.exists(csv_path):
        try:
            output_path = os.path.join(viz_dir, "evolution_trace.png")
            plot_evolution_trace(csv_path, output_path)
            print(f"  [OK] 演化轨迹图: {output_path}")
        except Exception as exc:
            print(f"  [FAIL] 演化轨迹图生成失败: {exc}")
    return {}

