from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import colormaps
from matplotlib.colors import Normalize, PowerNorm
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from scipy.interpolate import RegularGridInterpolator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulation.comsol.field_registry import build_field_registry_manifest, get_field_spec
from simulation.comsol.physics_profiles import (
    materialize_contract_payload,
)
from tools.comsol_field_demo.common import iter_case_dirs, load_config, load_design_state, read_json, write_json
from tools.comsol_field_demo.layout_template import CATEGORY_DEFAULTS

FIELD_REGISTRY_KEY_BY_TOOL_FIELD: Dict[str, str] = {
    "temperature": get_field_spec("temperature").key,
    "displacement": get_field_spec("displacement").key,
    "stress": get_field_spec("stress").key,
}

DEFAULT_FIELD_CMAPS: Dict[str, str] = {
    "temperature": "inferno",
    "displacement": "viridis",
    "stress": "magma",
}

DEFAULT_FIELD_DISPLAY: Dict[str, Dict[str, float | str]] = {
    "temperature": {"unit": "K", "scale": 1.0},
    "displacement": {"unit": "mm", "scale": 1000.0},
    "stress": {"unit": "kPa", "scale": 0.001},
}


def load_tensor_payload(path: str | Path) -> Dict[str, Any]:
    with np.load(path, allow_pickle=True) as payload:
        vectors = payload["vectors"] if "vectors" in payload.files and payload["vectors"].size else None
        return {
            "field": np.asarray(payload["field"], dtype=float),
            "vectors": np.asarray(vectors, dtype=float) if vectors is not None else None,
            "x_coords": np.asarray(payload["x_coords"], dtype=float),
            "y_coords": np.asarray(payload["y_coords"], dtype=float),
            "z_coords": np.asarray(payload["z_coords"], dtype=float),
            "unit": str(payload["unit"].item() if hasattr(payload["unit"], "item") else payload["unit"]),
            "registry_key": str(
                payload["registry_key"].item() if "registry_key" in payload.files else ""
            ),
        }


def _build_interpolator(payload: Mapping[str, Any], key: str = "field") -> RegularGridInterpolator:
    return RegularGridInterpolator(
        (
            np.asarray(payload["x_coords"], dtype=float),
            np.asarray(payload["y_coords"], dtype=float),
            np.asarray(payload["z_coords"], dtype=float),
        ),
        np.asarray(payload[key], dtype=float),
        bounds_error=False,
        fill_value=np.nan,
    )


def _sample_scalar(interpolator: RegularGridInterpolator, points: np.ndarray) -> np.ndarray:
    values = np.asarray(interpolator(points), dtype=float)
    if values.ndim == 0:
        values = values.reshape(1)
    return values


def _box_faces(center: Sequence[float], dims: Sequence[float]) -> list[list[tuple[float, float, float]]]:
    cx, cy, cz = [float(item) for item in center]
    dx, dy, dz = [float(item) / 2.0 for item in dims]
    vertices = np.asarray(
        [
            [cx - dx, cy - dy, cz - dz],
            [cx + dx, cy - dy, cz - dz],
            [cx + dx, cy + dy, cz - dz],
            [cx - dx, cy + dy, cz - dz],
            [cx - dx, cy - dy, cz + dz],
            [cx + dx, cy - dy, cz + dz],
            [cx + dx, cy + dy, cz + dz],
            [cx - dx, cy + dy, cz + dz],
        ],
        dtype=float,
    )
    return [
        [tuple(vertices[idx]) for idx in face]
        for face in (
            (0, 1, 2, 3),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (2, 3, 7, 6),
            (1, 2, 6, 5),
            (0, 3, 7, 4),
        )
    ]


def _resolve_shell_geometry(design_state) -> Dict[str, Any]:
    metadata = dict(getattr(design_state, "metadata", {}) or {})
    shell_meta = dict(metadata.get("shell", {}) or {})
    envelope = getattr(design_state, "envelope", None)
    if envelope is None:
        return {"enabled": False}
    outer = getattr(envelope, "outer_size", None)
    if outer is None:
        return {"enabled": False}
    outer_x = float(getattr(outer, "x", 0.0) or 0.0)
    outer_y = float(getattr(outer, "y", 0.0) or 0.0)
    outer_z = float(getattr(outer, "z", 0.0) or 0.0)
    thickness = float(shell_meta.get("thickness_mm", getattr(envelope, "thickness", 0.0)) or 0.0)
    enabled = bool(shell_meta.get("enabled", False)) and thickness > 0.0 and min(outer_x, outer_y, outer_z) > 0.0
    if not enabled:
        return {"enabled": False}
    inner = getattr(envelope, "inner_size", None)
    inner_x = float(getattr(inner, "x", outer_x - 2.0 * thickness) or (outer_x - 2.0 * thickness))
    inner_y = float(getattr(inner, "y", outer_y - 2.0 * thickness) or (outer_y - 2.0 * thickness))
    inner_z = float(getattr(inner, "z", outer_z - 2.0 * thickness) or (outer_z - 2.0 * thickness))
    return {
        "enabled": True,
        "thickness": thickness,
        "outer_size": np.asarray([outer_x, outer_y, outer_z], dtype=float),
        "inner_size": np.asarray([inner_x, inner_y, inner_z], dtype=float),
    }


def _build_shell_panel_boxes(design_state) -> list[Dict[str, Any]]:
    shell = _resolve_shell_geometry(design_state)
    if not bool(shell.get("enabled", False)):
        return []
    outer_x, outer_y, outer_z = [float(item) for item in np.asarray(shell["outer_size"], dtype=float)]
    inner_x, inner_y, inner_z = [float(item) for item in np.asarray(shell["inner_size"], dtype=float)]
    thickness = float(shell["thickness"])
    half_outer_x = outer_x / 2.0
    half_outer_y = outer_y / 2.0
    half_outer_z = outer_z / 2.0
    half_inner_x = inner_x / 2.0
    half_inner_y = inner_y / 2.0
    half_inner_z = inner_z / 2.0
    return [
        {"name": "shell_bottom", "center": np.asarray([0.0, 0.0, -half_outer_z + thickness / 2.0]), "dims": np.asarray([outer_x, outer_y, thickness])},
        {"name": "shell_top", "center": np.asarray([0.0, 0.0, half_outer_z - thickness / 2.0]), "dims": np.asarray([outer_x, outer_y, thickness])},
        {"name": "shell_left", "center": np.asarray([-half_outer_x + thickness / 2.0, 0.0, 0.0]), "dims": np.asarray([thickness, outer_y, inner_z])},
        {"name": "shell_right", "center": np.asarray([half_outer_x - thickness / 2.0, 0.0, 0.0]), "dims": np.asarray([thickness, outer_y, inner_z])},
        {"name": "shell_back", "center": np.asarray([0.0, -half_outer_y + thickness / 2.0, 0.0]), "dims": np.asarray([inner_x, thickness, inner_z])},
        {"name": "shell_front", "center": np.asarray([0.0, half_outer_y - thickness / 2.0, 0.0]), "dims": np.asarray([inner_x, thickness, inner_z])},
    ]


def _add_box_collection(
    *,
    ax,
    center: Sequence[float],
    dims: Sequence[float],
    face_color: Sequence[float],
    edge_color: Sequence[float],
    linewidth: float,
) -> None:
    faces = _box_faces(center, dims)
    ax.add_collection3d(
        Poly3DCollection(
            faces,
            facecolors=[tuple(face_color)] * len(faces),
            edgecolors=tuple(edge_color),
            linewidths=float(linewidth),
        )
    )


def _component_base_color(category: str) -> str:
    return str(dict(CATEGORY_DEFAULTS.get(category, {}) or {}).get("display_color", "#adb5bd"))


def _resolve_colormap(name: str):
    try:
        return colormaps[str(name)]
    except Exception:
        return colormaps["plasma"]


def _convert_payload_for_display(
    *,
    field_name: str,
    tensor_payload: Mapping[str, Any],
    render_cfg: Mapping[str, Any],
) -> Dict[str, Any]:
    display_cfg = dict(render_cfg.get("field_display", {}) or {})
    field_cfg = dict(display_cfg.get(field_name, {}) or {})
    default_cfg = dict(DEFAULT_FIELD_DISPLAY.get(field_name, {}) or {})
    scale = float(field_cfg.get("scale", default_cfg.get("scale", 1.0)))
    vector_scale = float(field_cfg.get("vector_scale", scale))
    unit = str(field_cfg.get("unit", default_cfg.get("unit", str(tensor_payload.get("unit", "")))))
    vectors = tensor_payload.get("vectors")
    converted_vectors = np.asarray(vectors, dtype=float) * vector_scale if vectors is not None else None
    return {
        "field": np.asarray(tensor_payload["field"], dtype=float) * scale,
        "vectors": converted_vectors,
        "x_coords": np.asarray(tensor_payload["x_coords"], dtype=float),
        "y_coords": np.asarray(tensor_payload["y_coords"], dtype=float),
        "z_coords": np.asarray(tensor_payload["z_coords"], dtype=float),
        "unit": unit,
    }


def _resolve_field_style(
    *,
    field_name: str,
    tensor_payload: Mapping[str, Any],
    render_cfg: Mapping[str, Any],
) -> tuple[Normalize, Any]:
    field = np.asarray(tensor_payload["field"], dtype=float)
    finite_field = field[np.isfinite(field)]
    default_vmin = float(np.nanmin(finite_field)) if finite_field.size else 0.0
    default_vmax = float(np.nanmax(finite_field)) if finite_field.size else 1.0
    field_limits = dict(render_cfg.get("field_limits", {}) or {})
    limit_cfg = dict(field_limits.get(field_name, {}) or {})
    percentile_vmin = limit_cfg.get("percentile_vmin")
    percentile_vmax = limit_cfg.get("percentile_vmax")
    vmin = default_vmin
    vmax = default_vmax
    if finite_field.size and percentile_vmin is not None:
        vmin = float(np.nanpercentile(finite_field, float(percentile_vmin)))
    if finite_field.size and percentile_vmax is not None:
        vmax = float(np.nanpercentile(finite_field, float(percentile_vmax)))
    if "vmin" in limit_cfg:
        vmin = float(limit_cfg.get("vmin", vmin))
    if "vmax" in limit_cfg:
        vmax = float(limit_cfg.get("vmax", vmax))
    if vmax <= vmin:
        vmax = vmin + 1.0
    norm_kind = str(limit_cfg.get("norm", "linear")).strip().lower()
    gamma = float(limit_cfg.get("gamma", 1.0))
    field_cmaps = dict(render_cfg.get("field_cmaps", {}) or {})
    cmap_name = str(field_cmaps.get(field_name, DEFAULT_FIELD_CMAPS.get(field_name, "plasma")))
    if norm_kind == "power":
        return PowerNorm(gamma=max(gamma, 1e-3), vmin=vmin, vmax=vmax), _resolve_colormap(cmap_name)
    return Normalize(vmin=vmin, vmax=vmax), _resolve_colormap(cmap_name)


def _compute_scene_bounds(
    *,
    design_state,
    render_cfg: Mapping[str, Any],
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    shell = _resolve_shell_geometry(design_state)
    if bool(shell.get("enabled", False)):
        margin = float(render_cfg.get("scene_margin_mm", 10.0))
        outer_x, outer_y, outer_z = [float(item) for item in np.asarray(shell["outer_size"], dtype=float)]
        return (
            (-outer_x / 2.0 - margin, outer_x / 2.0 + margin),
            (-outer_y / 2.0 - margin, outer_y / 2.0 + margin),
            (-outer_z / 2.0 - margin, outer_z / 2.0 + margin),
        )
    half_x = float(design_state.envelope.outer_size.x) / 2.0
    half_y = float(design_state.envelope.outer_size.y) / 2.0
    half_z = float(design_state.envelope.outer_size.z) / 2.0
    if not bool(render_cfg.get("fit_scene_to_components", True)):
        return (-half_x, half_x), (-half_y, half_y), (-half_z, half_z)
    margin = float(render_cfg.get("scene_margin_mm", 10.0))
    min_x = min(float(comp.position.x) - float(comp.dimensions.x) / 2.0 for comp in design_state.components)
    max_x = max(float(comp.position.x) + float(comp.dimensions.x) / 2.0 for comp in design_state.components)
    min_y = min(float(comp.position.y) - float(comp.dimensions.y) / 2.0 for comp in design_state.components)
    max_y = max(float(comp.position.y) + float(comp.dimensions.y) / 2.0 for comp in design_state.components)
    min_z = min(float(comp.position.z) - float(comp.dimensions.z) / 2.0 for comp in design_state.components)
    max_z = max(float(comp.position.z) + float(comp.dimensions.z) / 2.0 for comp in design_state.components)
    return (
        (max(-half_x, min_x - margin), min(half_x, max_x + margin)),
        (max(-half_y, min_y - margin), min(half_y, max_y + margin)),
        (max(-half_z, min_z - margin), min(half_z, max_z + margin)),
    )


def _render_geometry_shell(
    *,
    ax,
    bounds: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    face_alpha: float,
    edge_alpha: float,
    design_state=None,
) -> None:
    shell_boxes = _build_shell_panel_boxes(design_state) if design_state is not None else []
    if shell_boxes:
        for shell_box in shell_boxes:
            _add_box_collection(
                ax=ax,
                center=shell_box["center"],
                dims=shell_box["dims"],
                face_color=(0.82, 0.86, 0.90, float(face_alpha)),
                edge_color=(0.20, 0.26, 0.33, float(edge_alpha)),
                linewidth=0.55,
            )
        return
    (min_x, max_x), (min_y, max_y), (min_z, max_z) = bounds
    outer_vertices = np.asarray(
        [
            [min_x, min_y, min_z],
            [max_x, min_y, min_z],
            [max_x, max_y, min_z],
            [min_x, max_y, min_z],
            [min_x, min_y, max_z],
            [max_x, min_y, max_z],
            [max_x, max_y, max_z],
            [min_x, max_y, max_z],
        ],
        dtype=float,
    )
    side_faces = [
        [tuple(outer_vertices[index]) for index in face]
        for face in (
            (0, 1, 2, 3),
            (0, 3, 7, 4),
            (1, 2, 6, 5),
            (3, 2, 6, 7),
        )
    ]
    ax.add_collection3d(
        Poly3DCollection(
            side_faces,
            facecolors=[(0.82, 0.86, 0.90, face_alpha)] * len(side_faces),
            edgecolors=(0.20, 0.26, 0.33, edge_alpha),
            linewidths=0.8,
        )
    )
    edge_pairs = (
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 0),
        (0, 4),
        (1, 5),
        (2, 6),
        (3, 7),
        (4, 7),
        (5, 6),
    )
    for start, end in edge_pairs:
        segment = outer_vertices[[start, end]]
        ax.plot(
            segment[:, 0],
            segment[:, 1],
            segment[:, 2],
            color=(0.18, 0.22, 0.28, max(edge_alpha, 0.22)),
            linewidth=0.8,
            alpha=max(edge_alpha, 0.22),
        )


def _iter_box_surface_meshes(center: Sequence[float], dims: Sequence[float], grid_size: int):
    cx, cy, cz = [float(item) for item in center]
    dx, dy, dz = [float(item) / 2.0 for item in dims]
    x_min, x_max = cx - dx, cx + dx
    y_min, y_max = cy - dy, cy + dy
    z_min, z_max = cz - dz, cz + dz
    y_grid = np.linspace(y_min, y_max, grid_size)
    z_grid = np.linspace(z_min, z_max, grid_size)
    x_grid = np.linspace(x_min, x_max, grid_size)
    Y, Z = np.meshgrid(y_grid, z_grid)
    X, Zx = np.meshgrid(x_grid, z_grid)
    Xy, Yx = np.meshgrid(x_grid, y_grid)
    return [
        (np.full_like(Y, x_max), Y, Z),
        (np.full_like(Y, x_min), Y, Z),
        (X, np.full_like(X, y_max), Zx),
        (X, np.full_like(X, y_min), Zx),
        (Xy, Yx, np.full_like(Xy, z_max)),
        (Xy, Yx, np.full_like(Xy, z_min)),
    ]


def _render_scalar_box_surfaces(
    *,
    ax,
    scalar_interpolator: RegularGridInterpolator,
    center: Sequence[float],
    dims: Sequence[float],
    norm: Normalize,
    cmap,
    alpha: float,
    grid_size: int,
    displacement: Sequence[float] | None = None,
) -> None:
    shift = np.asarray(displacement, dtype=float) if displacement is not None else np.zeros(3, dtype=float)
    for X, Y, Z in _iter_box_surface_meshes(center, dims, grid_size):
        Xs = X + shift[0]
        Ys = Y + shift[1]
        Zs = Z + shift[2]
        points = np.column_stack([Xs.ravel(), Ys.ravel(), Zs.ravel()])
        values = _sample_scalar(scalar_interpolator, points).reshape(Xs.shape)
        face_colors = cmap(norm(np.nan_to_num(values, nan=float(norm.vmin))))
        face_colors[..., 3] = float(alpha)
        ax.plot_surface(
            Xs,
            Ys,
            Zs,
            rstride=1,
            cstride=1,
            facecolors=face_colors,
            linewidth=0.12,
            edgecolor=(0.05, 0.05, 0.05, 0.18),
            shade=False,
        )


def _render_shell_faces(
    *,
    ax,
    scalar_interpolator: RegularGridInterpolator,
    bounds: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    norm: Normalize,
    cmap,
    alpha: float,
    grid_size: int,
    design_state=None,
    shell_displacements: Mapping[str, np.ndarray] | None = None,
) -> None:
    shell_boxes = _build_shell_panel_boxes(design_state) if design_state is not None else []
    if shell_boxes:
        for shell_box in shell_boxes:
            _render_scalar_box_surfaces(
                ax=ax,
                scalar_interpolator=scalar_interpolator,
                center=np.asarray(shell_box["center"], dtype=float),
                dims=np.asarray(shell_box["dims"], dtype=float),
                norm=norm,
                cmap=cmap,
                alpha=alpha,
                grid_size=grid_size,
                displacement=None if shell_displacements is None else shell_displacements.get(shell_box["name"]),
            )
        return
    (min_x, max_x), (min_y, max_y), (min_z, max_z) = bounds
    u = np.linspace(min_x, max_x, grid_size)
    v = np.linspace(min_z, max_z, grid_size)
    U, V = np.meshgrid(u, v)
    faces = [
        (U, np.full_like(U, max_y), V),
        (np.full_like(U, max_x), U, V),
        (np.full_like(U, min_x), U, V),
    ]
    for X, Y, Z in faces:
        points = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
        values = _sample_scalar(scalar_interpolator, points).reshape(X.shape)
        face_colors = cmap(norm(np.nan_to_num(values, nan=float(norm.vmin))))
        ax.plot_surface(
            X,
            Y,
            Z,
            rstride=1,
            cstride=1,
            facecolors=face_colors,
            linewidth=0.15,
            edgecolor=(0.05, 0.05, 0.05, 0.25),
            shade=False,
            alpha=alpha,
        )


def _draw_component_boxes(
    *,
    ax,
    design_state,
    color_resolver,
    alpha: float,
    displacement_vectors: Dict[str, np.ndarray] | None = None,
) -> None:
    for component in design_state.components:
        center = np.asarray(
            [component.position.x, component.position.y, component.position.z],
            dtype=float,
        )
        if displacement_vectors and component.id in displacement_vectors:
            center = center + np.asarray(displacement_vectors[component.id], dtype=float)
        dims = np.asarray(
            [component.dimensions.x, component.dimensions.y, component.dimensions.z],
            dtype=float,
        )
        faces = _box_faces(center, dims)
        collection = Poly3DCollection(
            faces,
            facecolors=[color_resolver(component)] * len(faces),
            edgecolors=(0.08, 0.08, 0.08, 0.65),
            linewidths=0.45,
            alpha=alpha,
        )
        ax.add_collection3d(collection)


def _build_shell_displacements(
    *,
    shell_boxes: Sequence[Mapping[str, Any]],
    vector_interpolator: RegularGridInterpolator | None,
    exaggeration: float,
) -> Dict[str, np.ndarray]:
    if vector_interpolator is None:
        return {}
    displacements: Dict[str, np.ndarray] = {}
    for shell_box in shell_boxes:
        center = np.asarray([shell_box["center"]], dtype=float)
        vector = np.asarray(vector_interpolator(center), dtype=float).reshape(-1)
        if vector.size != 3:
            continue
        displacements[str(shell_box["name"])] = vector * float(exaggeration)
    return displacements


def _build_component_displacements(
    *,
    design_state,
    vector_interpolator: RegularGridInterpolator | None,
    exaggeration: float,
) -> Dict[str, np.ndarray]:
    if vector_interpolator is None:
        return {}
    displacements: Dict[str, np.ndarray] = {}
    for component in design_state.components:
        center = np.asarray([[component.position.x, component.position.y, component.position.z]], dtype=float)
        vector = np.asarray(vector_interpolator(center), dtype=float).reshape(-1)
        if vector.size != 3:
            continue
        displacements[component.id] = vector * float(exaggeration)
    return displacements


def _set_axes_style(
    ax,
    bounds: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    view_elev: float,
    view_azim: float,
) -> None:
    (min_x, max_x), (min_y, max_y), (min_z, max_z) = bounds
    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    ax.set_zlim(min_z, max_z)
    ax.set_box_aspect((max_x - min_x, max_y - min_y, max_z - min_z))
    ax.view_init(elev=float(view_elev), azim=float(view_azim))
    ax.set_axis_off()


def _render_field_scene(
    *,
    ax,
    design_state,
    tensor_payload: Mapping[str, Any],
    render_cfg: Mapping[str, Any],
    scene_bounds: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    norm: Normalize,
    cmap,
):
    scalar_interpolator = _build_interpolator(tensor_payload, "field")
    vector_interpolator = None
    if tensor_payload.get("vectors") is not None:
        vector_interpolator = _build_interpolator(tensor_payload, "vectors")
    displacement_vectors = _build_component_displacements(
        design_state=design_state,
        vector_interpolator=vector_interpolator,
        exaggeration=float(render_cfg.get("displacement_exaggeration", 2500.0)),
    )
    shell_boxes = _build_shell_panel_boxes(design_state)
    shell_displacements = _build_shell_displacements(
        shell_boxes=shell_boxes,
        vector_interpolator=vector_interpolator,
        exaggeration=float(render_cfg.get("displacement_exaggeration", 2500.0)),
    )
    _render_shell_faces(
        ax=ax,
        scalar_interpolator=scalar_interpolator,
        bounds=scene_bounds,
        norm=norm,
        cmap=cmap,
        alpha=float(render_cfg.get("shell_alpha", 0.34)),
        grid_size=int(render_cfg.get("face_grid_size", 26)),
        design_state=design_state,
        shell_displacements=shell_displacements,
    )

    def _component_color(component) -> Any:
        sampled = float(
            _sample_scalar(
                scalar_interpolator,
                np.asarray([[component.position.x, component.position.y, component.position.z]], dtype=float),
            )[0]
        )
        if not np.isfinite(sampled):
            sampled = float(norm.vmin)
        return cmap(norm(sampled))

    _draw_component_boxes(
        ax=ax,
        design_state=design_state,
        color_resolver=_component_color,
        alpha=float(render_cfg.get("component_alpha", 0.58)),
        displacement_vectors=displacement_vectors,
    )
    _set_axes_style(
        ax,
        scene_bounds,
        view_elev=float(render_cfg.get("view_elev", 25.0)),
        view_azim=float(render_cfg.get("view_azim", -55.0)),
    )
    scalar_map = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    scalar_map.set_array([])
    return scalar_map


def render_geometry_overlay(
    *,
    case_dir: Path,
    config: Mapping[str, Any],
) -> str:
    design_state = load_design_state(case_dir / "design_state.json")
    render_cfg = dict(config.get("render", {}) or {})
    scene_bounds = _compute_scene_bounds(design_state=design_state, render_cfg=render_cfg)
    fig = plt.figure(figsize=tuple(render_cfg.get("figure_size", [10.0, 8.0])))
    ax = fig.add_subplot(111, projection="3d")
    _render_geometry_shell(
        ax=ax,
        bounds=scene_bounds,
        face_alpha=float(render_cfg.get("geometry_shell_alpha", 0.08)),
        edge_alpha=float(render_cfg.get("geometry_shell_edge_alpha", 0.24)),
        design_state=design_state,
    )
    _draw_component_boxes(
        ax=ax,
        design_state=design_state,
        color_resolver=lambda comp: _component_base_color(str(comp.category)),
        alpha=float(render_cfg.get("geometry_alpha", 0.25)),
    )
    _set_axes_style(
        ax,
        scene_bounds,
        view_elev=float(render_cfg.get("view_elev", 25.0)),
        view_azim=float(render_cfg.get("view_azim", -55.0)),
    )
    output_path = case_dir / "renders" / "geometry_overlay.png"
    fig.tight_layout()
    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return str(output_path)


def render_three_field_strip(
    *,
    case_dir: Path,
    design_state,
    tensor_payloads: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
) -> str:
    render_cfg = dict(config.get("render", {}) or {})
    scene_bounds = _compute_scene_bounds(design_state=design_state, render_cfg=render_cfg)
    ordered_fields = ("temperature", "displacement", "stress")
    fig = plt.figure(figsize=tuple(render_cfg.get("three_field_figure_size", [18.0, 5.4])))
    for subplot_index, field_name in enumerate(ordered_fields, start=1):
        payload = _convert_payload_for_display(
            field_name=field_name,
            tensor_payload=tensor_payloads[field_name],
            render_cfg=render_cfg,
        )
        norm, cmap = _resolve_field_style(
            field_name=field_name,
            tensor_payload=payload,
            render_cfg=render_cfg,
        )
        ax = fig.add_subplot(1, 3, subplot_index, projection="3d")
        scalar_map = _render_field_scene(
            ax=ax,
            design_state=design_state,
            tensor_payload=payload,
            render_cfg=render_cfg,
            scene_bounds=scene_bounds,
            norm=norm,
            cmap=cmap,
        )
        colorbar = fig.colorbar(
            scalar_map,
            ax=ax,
            pad=float(render_cfg.get("three_field_colorbar_pad", 0.01)),
            shrink=float(render_cfg.get("three_field_colorbar_shrink", 0.92)),
            fraction=float(render_cfg.get("three_field_colorbar_fraction", 0.04)),
        )
        colorbar.ax.tick_params(
            labelsize=float(render_cfg.get("three_field_colorbar_tick_size", 8.0)),
            length=2.0,
            pad=1.0,
        )
        colorbar.outline.set_linewidth(0.35)
        unit = str(payload.get("unit", ""))
        if unit:
            colorbar.set_label(
                unit,
                fontsize=float(render_cfg.get("three_field_colorbar_label_size", 8.5)),
                labelpad=float(render_cfg.get("three_field_colorbar_label_pad", 4.0)),
            )
    output_path = case_dir / "renders" / "three_fields_horizontal.png"
    fig.subplots_adjust(
        left=float(render_cfg.get("three_field_left", 0.005)),
        right=float(render_cfg.get("three_field_right", 0.995)),
        bottom=float(render_cfg.get("three_field_bottom", 0.01)),
        top=float(render_cfg.get("three_field_top", 0.99)),
        wspace=float(render_cfg.get("three_field_wspace", 0.02)),
    )
    fig.savefig(
        output_path,
        dpi=220,
        bbox_inches="tight",
        pad_inches=float(render_cfg.get("three_field_pad_inches", 0.01)),
    )
    plt.close(fig)
    return str(output_path)


def render_field_image(
    *,
    case_dir: Path,
    tensor_payload: Mapping[str, Any],
    config: Mapping[str, Any],
    output_name: str,
    field_name: str,
) -> str:
    design_state = load_design_state(case_dir / "design_state.json")
    render_cfg = dict(config.get("render", {}) or {})
    scene_bounds = _compute_scene_bounds(design_state=design_state, render_cfg=render_cfg)
    display_payload = _convert_payload_for_display(
        field_name=field_name,
        tensor_payload=tensor_payload,
        render_cfg=render_cfg,
    )
    norm, cmap = _resolve_field_style(
        field_name=field_name,
        tensor_payload=display_payload,
        render_cfg=render_cfg,
    )
    fig = plt.figure(figsize=tuple(render_cfg.get("single_field_figure_size", render_cfg.get("figure_size", [10.0, 8.0]))))
    ax = fig.add_subplot(111, projection="3d")
    _render_field_scene(
        ax=ax,
        design_state=design_state,
        tensor_payload=display_payload,
        render_cfg=render_cfg,
        scene_bounds=scene_bounds,
        norm=norm,
        cmap=cmap,
    )
    output_path = case_dir / "renders" / output_name
    fig.subplots_adjust(left=0.0, right=1.0, bottom=0.0, top=1.0)
    fig.savefig(
        output_path,
        dpi=220,
        bbox_inches="tight",
        pad_inches=float(render_cfg.get("single_field_pad_inches", 0.01)),
    )
    plt.close(fig)
    return str(output_path)


def render_case_outputs(case_dir: str | Path, config: Mapping[str, Any]) -> Dict[str, Any]:
    case_path = Path(case_dir)
    tensor_dir = case_path / "tensor"
    tensor_manifest_path = tensor_dir / "manifest.json"
    tensor_manifest = {}
    if tensor_manifest_path.exists():
        tensor_manifest = dict(read_json(tensor_manifest_path) or {})
    source_claim = dict(tensor_manifest.get("source_claim", {}) or {})
    render_cfg = dict(config.get("render", {}) or {})
    design_state = load_design_state(case_path / "design_state.json")
    outputs: Dict[str, Any] = {
        "case_id": case_path.name,
        "case_dir": str(case_path),
        "source_claim": dict(source_claim),
        "renders": {},
        "errors": [],
    }
    outputs = materialize_contract_payload(
        outputs,
        claim=source_claim,
        contract_bundle=dict(tensor_manifest.get("contract_bundle", {}) or {}),
        source_payload=tensor_manifest,
        field_export_registry=dict(
            tensor_manifest.get("field_export_registry", {}) or build_field_registry_manifest()
        ),
    )
    outputs["renders"]["geometry_overlay"] = render_geometry_overlay(case_dir=case_path, config=config)
    field_payloads: Dict[str, Mapping[str, Any]] = {}
    for field_name in ("temperature", "displacement", "stress"):
        tensor_path = tensor_dir / f"{field_name}_tensor.npz"
        if not tensor_path.exists():
            outputs["errors"].append(f"{field_name}:missing_tensor")
            continue
        payload = load_tensor_payload(tensor_path)
        field_payloads[field_name] = payload
        image_path = render_field_image(
            case_dir=case_path,
            tensor_payload=payload,
            config=config,
            output_name=f"{field_name}_field.png",
            field_name=field_name,
        )
        outputs["renders"][field_name] = image_path
    combined_keys = ("temperature", "displacement", "stress")
    if all(field_name in field_payloads for field_name in combined_keys):
        outputs["renders"]["three_fields"] = render_three_field_strip(
            case_dir=case_path,
            design_state=design_state,
            tensor_payloads={field_name: field_payloads[field_name] for field_name in combined_keys},
            config=config,
        )
    else:
        outputs["errors"].append("three_fields:missing_source_images")
    outputs["render_styles"] = {
        field_name: {
            "registry_key": str(
                field_payloads[field_name].get(
                    "registry_key",
                    FIELD_REGISTRY_KEY_BY_TOOL_FIELD.get(field_name, field_name),
                )
            ),
            "cmap": str(dict(render_cfg.get("field_cmaps", {}) or {}).get(field_name, DEFAULT_FIELD_CMAPS.get(field_name))),
            "raw_unit": str(
                field_payloads[field_name].get("unit", "")
                or get_field_spec(FIELD_REGISTRY_KEY_BY_TOOL_FIELD.get(field_name, field_name)).unit
            ),
            "display_unit": str(
                dict(dict(render_cfg.get("field_display", {}) or {}).get(field_name, {}) or {}).get(
                    "unit",
                    dict(DEFAULT_FIELD_DISPLAY.get(field_name, {}) or {}).get("unit", str(field_payloads[field_name].get("unit", ""))),
                )
            ),
        }
        for field_name in field_payloads
    }
    write_json(case_path / "renders" / "manifest.json", outputs)
    return outputs


def _build_dataset_gallery(
    *,
    dataset_path: Path,
    cases: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    render_cfg = dict(config.get("render", {}) or {})
    gallery_root = dataset_path / "gallery"
    triptych_dir = gallery_root / "three_field_triptychs"
    triptych_dir.mkdir(parents=True, exist_ok=True)

    collected: list[Dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id", Path(str(case.get("case_dir", ""))).name or "case"))
        renders = dict(case.get("renders", {}) or {})
        source = renders.get("three_fields")
        if not source:
            continue
        source_path = Path(str(source))
        if not source_path.exists():
            continue
        destination = triptych_dir / f"{case_id}_three_fields.png"
        shutil.copy2(source_path, destination)
        collected.append(
            {
                "case_id": case_id,
                "source_path": str(source_path),
                "copied_path": str(destination),
            }
        )

    gallery_summary: Dict[str, Any] = {
        "gallery_root": str(gallery_root),
        "triptych_dir": str(triptych_dir),
        "triptych_count": len(collected),
        "triptychs": collected,
    }
    if not collected:
        return gallery_summary

    sample_image = np.asarray(plt.imread(collected[0]["copied_path"]))
    image_height = int(sample_image.shape[0]) if sample_image.ndim >= 2 else 1
    image_width = int(sample_image.shape[1]) if sample_image.ndim >= 2 else 1
    aspect_ratio = float(image_height) / max(float(image_width), 1.0)

    count = len(collected)
    columns = int(render_cfg.get("gallery_columns", 0) or np.ceil(np.sqrt(count)))
    columns = max(1, columns)
    rows = int(np.ceil(float(count) / float(columns)))
    tile_width_in = float(render_cfg.get("gallery_tile_width_in", 5.4))
    tile_height_in = float(render_cfg.get("gallery_tile_height_in", tile_width_in * max(aspect_ratio, 0.18)))
    background = str(render_cfg.get("gallery_background", "#000000"))
    label_color = str(render_cfg.get("gallery_label_color", "#f8f9fa"))
    show_labels = bool(render_cfg.get("gallery_show_labels", False))

    fig = plt.figure(figsize=(columns * tile_width_in, rows * tile_height_in))
    fig.patch.set_facecolor(background)
    for index, triptych in enumerate(collected, start=1):
        ax = fig.add_subplot(rows, columns, index)
        image = np.asarray(plt.imread(triptych["copied_path"]))
        ax.imshow(image)
        ax.axis("off")
        ax.set_facecolor(background)
        if show_labels:
            ax.text(
                0.02,
                0.98,
                str(triptych["case_id"]),
                transform=ax.transAxes,
                ha="left",
                va="top",
                color=label_color,
                fontsize=float(render_cfg.get("gallery_label_fontsize", 8.0)),
                fontweight="bold",
            )
    fig.subplots_adjust(
        left=float(render_cfg.get("gallery_left", 0.01)),
        right=float(render_cfg.get("gallery_right", 0.99)),
        bottom=float(render_cfg.get("gallery_bottom", 0.01)),
        top=float(render_cfg.get("gallery_top", 0.99)),
        wspace=float(render_cfg.get("gallery_wspace", 0.015)),
        hspace=float(render_cfg.get("gallery_hspace", 0.04)),
    )
    montage_path = gallery_root / "dataset_triptych_montage.png"
    fig.savefig(
        montage_path,
        dpi=int(render_cfg.get("gallery_dpi", 220)),
        bbox_inches="tight",
        pad_inches=float(render_cfg.get("gallery_pad_inches", 0.02)),
        facecolor=fig.get_facecolor(),
    )
    plt.close(fig)
    gallery_summary["montage_path"] = str(montage_path)
    gallery_summary["grid"] = {
        "rows": rows,
        "columns": columns,
        "image_width_px": image_width,
        "image_height_px": image_height,
    }
    return gallery_summary


def render_dataset_outputs(dataset_root: str | Path, config: Mapping[str, Any]) -> Dict[str, Any]:
    dataset_path = Path(dataset_root)
    cases = [render_case_outputs(case_dir, config) for case_dir in iter_case_dirs(dataset_path)]
    gallery = _build_dataset_gallery(
        dataset_path=dataset_path,
        cases=cases,
        config=config,
    )
    summary = {
        "dataset_root": str(dataset_path),
        "case_count": len(cases),
        "cases": cases,
        "gallery": gallery,
    }
    write_json(dataset_path / "render_summary.json", summary)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render geometry and three physics fields for demo cases.")
    parser.add_argument("--dataset-root", type=str, required=True, help="Root created by tool_generate_cases.py")
    parser.add_argument("--config", type=str, default=None, help="Path to YAML config.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    config = load_config(args.config)
    summary = render_dataset_outputs(args.dataset_root, config)
    print(f"Rendered {summary['case_count']} cases under {args.dataset_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
