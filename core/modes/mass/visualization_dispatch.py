"""
mass visualization dispatch helpers.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict

def render_mass_artifacts(
    *,
    mass_csv_path: str,
    tables_dir: str,
    viz_dir: str,
    experiment_dir: str,
    plot_mass_trace: Callable[[str, str], Any],
    plot_mass_storyboard: Callable[[str, str], Any],
    plot_layout_timeline: Callable[[str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Render mass-specific trend/storyboard/timeline artifacts.

    Returns:
        timeline report dict.
    """
    timeline_report: Dict[str, Any] = {}

    if os.path.exists(mass_csv_path):
        try:
            output_path = os.path.join(viz_dir, "mass_trace.png")
            plot_mass_trace(mass_csv_path, output_path)
            print(f"  [OK] mass轨迹图: {output_path}")
        except Exception as exc:
            print(f"  [FAIL] mass轨迹图生成失败: {exc}")
    else:
        print("  [WARN] 未找到 mass_trace.csv，跳过轨迹图生成")

    if os.path.isdir(tables_dir):
        try:
            output_path = os.path.join(viz_dir, "mass_storyboard.png")
            plot_mass_storyboard(tables_dir, output_path)
            print(f"  [OK] mass故事板: {output_path}")
        except Exception as exc:
            print(f"  [FAIL] mass故事板生成失败: {exc}")

    try:
        timeline_report = plot_layout_timeline(experiment_dir, viz_dir)
        frame_count = int(timeline_report.get("frame_count", 0) or 0)
        if frame_count > 0:
            print(f"  [OK] 布局时间线帧: {timeline_report.get('frames_dir', '')}")
            gif_path = str(timeline_report.get("gif_path", "") or "")
            if gif_path:
                print(f"  [OK] 布局时间线GIF: {gif_path}")
        else:
            print("  [WARN] 未检测到 layout_events，跳过布局时间线生成")
    except Exception as exc:
        print(f"  [FAIL] 布局时间线生成失败: {exc}")

    return timeline_report
