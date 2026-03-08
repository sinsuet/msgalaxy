"""
vop_maas visualization dispatch helpers.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from core.modes.mass.visualization_dispatch import render_mass_artifacts


def render_vop_maas_artifacts(
    *,
    delegated_mass_csv_path: str,
    tables_dir: str,
    viz_dir: str,
    experiment_dir: str,
    plot_mass_trace: Callable[[str, str], Any],
    plot_mass_storyboard: Callable[[str, str], Any],
    plot_layout_timeline: Callable[[str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Render VOP-MaaS artifacts.

    The VOP controller view reuses mass execution charts from delegated raw artifacts
    while leaving room for VOP-specific text summaries at the top-level summary stage.
    """
    return render_mass_artifacts(
        mass_csv_path=delegated_mass_csv_path,
        tables_dir=tables_dir,
        viz_dir=viz_dir,
        experiment_dir=experiment_dir,
        plot_mass_trace=plot_mass_trace,
        plot_mass_storyboard=plot_mass_storyboard,
        plot_layout_timeline=plot_layout_timeline,
    )
