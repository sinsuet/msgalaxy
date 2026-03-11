"""
Step-level iteration review package builder.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from core.path_policy import resolve_repo_path, serialize_repo_path
from core.visualization import (
    _merge_operator_actions,
    _operator_action_family,
    _parse_operator_actions,
)
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .builders import load_json, load_layout_snapshot_records
from .contracts import (
    CaseContractAdapterInfo,
    IterationReviewMetricsPayload,
    IterationReviewPackage,
    IterationReviewFieldCaseMap,
    IterationReviewFieldCaseMapEntry,
    OperatorActionInfo,
    PhysicsProfileInfo,
    PhysicsSourceClaim,
    ReviewArtifactRef,
    ReviewMetricCard,
    ReviewMetricDelta,
    ReviewStateIndex,
)
from .operator_semantics import build_operator_semantic_display
from .registry import (
    REGISTRY_VERSIONS,
    build_registry_snapshot,
    get_metric_spec,
    get_operator_family_spec,
    get_review_profile_contract,
    get_unit_spec,
)
from visualization.review_summary_bridge import (
    build_iteration_review_summary_from_paths,
    format_iteration_review_report_block,
    format_iteration_review_visualization_block,
    report_block_markers,
    upsert_text_block,
    visualization_block_markers,
)


DEFAULT_REVIEW_PROFILES: tuple[str, ...] = ("teacher_demo", "research_fast")
_STAGE_SLUG_RE = re.compile(r"[^a-z0-9]+")
_LAYOUT_CANVAS_SIZE = (960, 720)
_LAYOUT_MARGIN = 56
_LAYOUT_BACKGROUND = (245, 246, 248)
_LAYOUT_PANEL = (255, 255, 255)
_LAYOUT_ENVELOPE_OUTLINE = (49, 57, 68)
_LAYOUT_TEXT = (34, 40, 49)
_LAYOUT_AXIS = (152, 161, 171)
_CATEGORY_COLORS: dict[str, tuple[int, int, int]] = {
    "payload": (38, 94, 172),
    "thermal": (201, 81, 37),
    "power": (214, 156, 36),
    "avionics": (74, 120, 88),
    "structure": (108, 116, 129),
    "propulsion": (125, 72, 167),
}
_FIELD_CASE_SUBDIR_NAMES = frozenset({"field_exports", "renders", "tensor"})
_DATASET_SUMMARY_FILENAMES = frozenset({"field_run_summary.json", "render_summary.json"})
_RENDER_ASSET_ALIASES: dict[str, tuple[str, ...]] = {
    "geometry_overlay": ("geometry_overlay",),
    "temperature_field": ("temperature_field", "temperature"),
    "displacement_field": ("displacement_field", "displacement"),
    "stress_field": ("stress_field", "stress"),
    "triptych": ("triptych", "three_fields"),
}
_RENDER_ASSET_FILENAMES: dict[str, tuple[str, ...]] = {
    "geometry_overlay": ("geometry_overlay.png",),
    "temperature_field": ("temperature_field.png",),
    "displacement_field": ("displacement_field.png",),
    "stress_field": ("stress_field.png",),
    "triptych": ("three_fields_horizontal.png", "triptych.png"),
}
_MINIMAL_LEGACY_CASE_SIGNALS: tuple[str, ...] = (
    "design_state.json",
    "field_exports/manifest.json or field_exports/simulation_result.json",
    "renders/manifest.json or supported render images",
    "dataset field_run_summary.json/render_summary.json case entry",
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _slug_stage(stage: str) -> str:
    normalized = _STAGE_SLUG_RE.sub("_", str(stage or "").strip().lower()).strip("_")
    return normalized or "step"


def _normalize_profiles(review_profiles: Sequence[str] | None) -> list[str]:
    if review_profiles is None:
        return list(DEFAULT_REVIEW_PROFILES)
    normalized = [str(item).strip() for item in list(review_profiles) if str(item).strip()]
    return list(dict.fromkeys(normalized or list(DEFAULT_REVIEW_PROFILES)))


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _persist_iteration_review_run_artifacts(
    *,
    run_path: Path,
    review_result: Mapping[str, Any],
    iteration_review_summary: Mapping[str, Any],
) -> None:
    summary_path = run_path / "summary.json"
    if not summary_path.exists():
        return

    summary_payload = load_json(summary_path)
    summary_payload["iteration_review_index_path"] = str(review_result.get("index_path", "") or "")
    profiles_payload = dict(review_result.get("profiles", {}) or {})
    summary_payload["iteration_review_teacher_demo_index_path"] = str(
        dict(profiles_payload.get("teacher_demo", {}) or {}).get("index_path", "") or ""
    )
    summary_payload["iteration_review_research_fast_index_path"] = str(
        dict(profiles_payload.get("research_fast", {}) or {}).get("index_path", "") or ""
    )
    summary_payload["iteration_review_summary"] = dict(iteration_review_summary or {})
    _write_json(summary_path, summary_payload)

    report_block = format_iteration_review_report_block(iteration_review_summary)
    report_path = run_path / "report.md"
    if report_block:
        if report_path.exists():
            existing_report = report_path.read_text(encoding="utf-8")
        else:
            existing_report = "# Report\n"
        report_start_marker, report_end_marker = report_block_markers()
        updated_report = upsert_text_block(
            existing_report,
            report_block,
            start_marker=report_start_marker,
            end_marker=report_end_marker,
        )
        report_path.write_text(updated_report.rstrip() + "\n", encoding="utf-8")

    visualization_block = format_iteration_review_visualization_block(iteration_review_summary)
    if not visualization_block:
        return

    visualization_summary_path = run_path / "visualizations" / "visualization_summary.txt"
    visualization_summary_path.parent.mkdir(parents=True, exist_ok=True)
    if visualization_summary_path.exists():
        existing_content = visualization_summary_path.read_text(encoding="utf-8")
    else:
        optimization_mode = str(
            summary_payload.get("run_mode")
            or summary_payload.get("optimization_mode")
            or summary_payload.get("execution_mode")
            or "unknown"
        ).strip()
        existing_content = (
            f"Optimization mode: {optimization_mode}\n"
            "=== Optimization Visualization Summary ===\n"
            "- Visualization summary not generated before iteration review package build."
        )
    start_marker, end_marker = visualization_block_markers()
    updated_content = upsert_text_block(
        existing_content,
        visualization_block,
        start_marker=start_marker,
        end_marker=end_marker,
    )
    visualization_summary_path.write_text(updated_content.rstrip() + "\n", encoding="utf-8")


def _resolve_optional_repo_path(raw_path: Any) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    return resolve_repo_path(text)


def _coerce_xyz(value: Any) -> tuple[float, float, float]:
    if isinstance(value, Mapping):
        return (
            _safe_float(value.get("x")) or 0.0,
            _safe_float(value.get("y")) or 0.0,
            _safe_float(value.get("z")) or 0.0,
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts = list(value)
        padded = parts[:3] + [0.0] * max(0, 3 - len(parts))
        return (
            _safe_float(padded[0]) or 0.0,
            _safe_float(padded[1]) or 0.0,
            _safe_float(padded[2]) or 0.0,
        )
    return (0.0, 0.0, 0.0)


def _component_vector(component: Mapping[str, Any], *keys: str) -> tuple[float, float, float]:
    for key in keys:
        if key in component:
            return _coerce_xyz(component.get(key))
    return (0.0, 0.0, 0.0)


def _category_color(component: Mapping[str, Any]) -> tuple[int, int, int]:
    category = str(component.get("category", "") or "").strip().lower()
    return _CATEGORY_COLORS.get(category, (87, 125, 182))


def _component_label(component: Mapping[str, Any], index: int) -> str:
    label = str(component.get("id", "") or component.get("name", "") or component.get("display_name", "") or "").strip()
    return label or f"component_{index}"


def _component_bounds_xy(component: Mapping[str, Any], *, origin_mode: str) -> tuple[float, float, float, float]:
    pos_x, pos_y, _ = _component_vector(component, "position", "position_mm", "center")
    size_x, size_y, _ = _component_vector(component, "dimensions", "dimensions_mm", "size")
    width = max(size_x, 1.0)
    height = max(size_y, 1.0)
    if origin_mode in {"center", "centre"}:
        return (
            pos_x - (width / 2.0),
            pos_x + (width / 2.0),
            pos_y - (height / 2.0),
            pos_y + (height / 2.0),
        )
    return (pos_x, pos_x + width, pos_y, pos_y + height)


def _layout_frame_bounds(
    design_state: Mapping[str, Any],
    component_bounds: Sequence[tuple[float, float, float, float]],
) -> tuple[tuple[float, float, float, float], str, list[str]]:
    notes: list[str] = []
    envelope = dict(design_state.get("envelope", {}) or {})
    origin_mode = str(envelope.get("origin", "") or "center").strip().lower() or "center"
    outer_x, outer_y, _ = _component_vector(envelope, "outer_size", "outer_size_mm", "size")
    if outer_x > 0.0 and outer_y > 0.0:
        if origin_mode in {"center", "centre"}:
            return ((-outer_x / 2.0, outer_x / 2.0, -outer_y / 2.0, outer_y / 2.0), origin_mode, notes)
        notes.append(f"Envelope origin '{origin_mode}' rendered as lower-left anchored top view.")
        return ((0.0, outer_x, 0.0, outer_y), origin_mode, notes)

    if component_bounds:
        min_x = min(bounds[0] for bounds in component_bounds)
        max_x = max(bounds[1] for bounds in component_bounds)
        min_y = min(bounds[2] for bounds in component_bounds)
        max_y = max(bounds[3] for bounds in component_bounds)
        pad_x = max(8.0, (max_x - min_x) * 0.1)
        pad_y = max(8.0, (max_y - min_y) * 0.1)
        notes.append("Envelope outer_size missing; bounds were inferred from component boxes.")
        return ((min_x - pad_x, max_x + pad_x, min_y - pad_y, max_y + pad_y), origin_mode, notes)

    notes.append("Layout view skipped because both envelope and component extents are unavailable.")
    return ((0.0, 0.0, 0.0, 0.0), origin_mode, notes)


def _top_view_to_pixel(
    value_x: float,
    value_y: float,
    *,
    bounds: tuple[float, float, float, float],
    image_size: tuple[int, int],
) -> tuple[float, float]:
    min_x, max_x, min_y, max_y = bounds
    width, height = image_size
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    drawable_width = width - (_LAYOUT_MARGIN * 2)
    drawable_height = height - (_LAYOUT_MARGIN * 2)
    scale = min(drawable_width / span_x, drawable_height / span_y)
    pixel_x = _LAYOUT_MARGIN + ((value_x - min_x) * scale)
    pixel_y = height - _LAYOUT_MARGIN - ((value_y - min_y) * scale)
    return pixel_x, pixel_y


def _render_layout_view(
    *,
    step_dir: Path,
    filename: str,
    record: Dict[str, Any],
    state_label: str,
) -> tuple[str, list[str]]:
    snapshot = dict(record.get("snapshot", {}) or {})
    design_state = dict(snapshot.get("design_state", {}) or {})
    if not design_state:
        return "", ["Layout view skipped because snapshot.design_state is missing."]

    components = [
        dict(component)
        for component in list(design_state.get("components", []) or [])
        if isinstance(component, Mapping)
    ]
    envelope = dict(design_state.get("envelope", {}) or {})
    origin_mode = str(envelope.get("origin", "") or "center").strip().lower() or "center"
    component_bounds = [_component_bounds_xy(component, origin_mode=origin_mode) for component in components]
    bounds, origin_mode, notes = _layout_frame_bounds(design_state, component_bounds)
    if bounds[0] == bounds[1] or bounds[2] == bounds[3]:
        return "", notes

    destination = step_dir / filename
    image = Image.new("RGB", _LAYOUT_CANVAS_SIZE, color=_LAYOUT_BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    min_x, max_x, min_y, max_y = bounds
    envelope_left, envelope_bottom = _top_view_to_pixel(min_x, min_y, bounds=bounds, image_size=image.size)
    envelope_right, envelope_top = _top_view_to_pixel(max_x, max_y, bounds=bounds, image_size=image.size)
    draw.rectangle(
        [(envelope_left, envelope_top), (envelope_right, envelope_bottom)],
        fill=_LAYOUT_PANEL,
        outline=_LAYOUT_ENVELOPE_OUTLINE,
        width=4,
    )

    if min_x <= 0.0 <= max_x:
        axis_x, _ = _top_view_to_pixel(0.0, min_y, bounds=bounds, image_size=image.size)
        draw.line([(axis_x, envelope_top), (axis_x, envelope_bottom)], fill=_LAYOUT_AXIS, width=1)
    if min_y <= 0.0 <= max_y:
        _, axis_y = _top_view_to_pixel(min_x, 0.0, bounds=bounds, image_size=image.size)
        draw.line([(envelope_left, axis_y), (envelope_right, axis_y)], fill=_LAYOUT_AXIS, width=1)

    for index, component in enumerate(components, start=1):
        comp_min_x, comp_max_x, comp_min_y, comp_max_y = _component_bounds_xy(component, origin_mode=origin_mode)
        left, bottom = _top_view_to_pixel(comp_min_x, comp_min_y, bounds=bounds, image_size=image.size)
        right, top = _top_view_to_pixel(comp_max_x, comp_max_y, bounds=bounds, image_size=image.size)
        fill_color = _category_color(component)
        draw.rectangle([(left, top), (right, bottom)], fill=fill_color, outline=_LAYOUT_TEXT, width=2)
        label = _component_label(component, index)
        draw.text((left + 6, top + 6), label[:20], fill=(255, 255, 255), font=font)

    stage = str(dict(record.get("event", {}) or {}).get("stage", snapshot.get("stage", "")) or "").strip() or "unknown"
    draw.text((_LAYOUT_MARGIN, 18), f"{state_label}: {stage}", fill=_LAYOUT_TEXT, font=font)
    draw.text(
        (_LAYOUT_MARGIN, image.height - 24),
        f"components={len(components)} origin={origin_mode}",
        fill=_LAYOUT_TEXT,
        font=font,
    )
    draw.text(
        (image.width - 220, 18),
        f"{max_x - min_x:.1f} x {max_y - min_y:.1f} mm",
        fill=_LAYOUT_TEXT,
        font=font,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    notes.append("Rendered local top-view geometry from snapshot.design_state.")
    return serialize_repo_path(destination), notes


def _panel_image(
    *,
    source_path: str,
    title: str,
    subtitle: str = "",
    panel_size: tuple[int, int] = (320, 250),
) -> Image.Image:
    panel = Image.new("RGB", panel_size, color=(252, 252, 253))
    draw = ImageDraw.Draw(panel)
    font = ImageFont.load_default()
    title_band_height = 28
    footer_height = 18 if subtitle else 0
    draw.rectangle([(0, 0), (panel_size[0] - 1, panel_size[1] - 1)], outline=(205, 211, 218), width=1)
    draw.rectangle([(0, 0), (panel_size[0] - 1, title_band_height)], fill=(236, 239, 243))
    draw.text((10, 8), title, fill=_LAYOUT_TEXT, font=font)

    source = _resolve_optional_repo_path(source_path)
    if source is None or not source.exists():
        draw.text((10, title_band_height + 12), "missing", fill=(154, 81, 74), font=font)
        return panel

    image = Image.open(source).convert("RGB")
    try:
        fitted = ImageOps.contain(
            image,
            (
                max(48, panel_size[0] - 18),
                max(48, panel_size[1] - title_band_height - footer_height - 18),
            ),
        )
        paste_x = (panel_size[0] - fitted.width) // 2
        paste_y = title_band_height + 8 + max(
            0,
            ((panel_size[1] - title_band_height - footer_height - 16 - fitted.height) // 2),
        )
        panel.paste(fitted, (paste_x, paste_y))
    finally:
        image.close()

    if subtitle:
        draw.text((10, panel_size[1] - footer_height), subtitle[:44], fill=(96, 103, 112), font=font)
    return panel


def _build_step_montage(
    *,
    step_dir: Path,
    profile_name: str,
    before_layout_path: str,
    after_layout_path: str,
    triptych_path: str,
    field_assets: Mapping[str, Any],
) -> tuple[str, list[str]]:
    contract = get_review_profile_contract(profile_name)
    if contract.package_level != "full":
        return "", ["Step montage skipped by lightweight review profile contract."]

    artifacts = dict(field_assets.get("artifacts", {}) or {})
    context_candidates = [
        ("Triptych", str(triptych_path or "")),
        ("Geometry Overlay", str(artifacts.get("geometry_overlay", "") or "")),
        ("Temperature Field", str(artifacts.get("temperature_field", "") or "")),
        ("Displacement Field", str(artifacts.get("displacement_field", "") or "")),
        ("Stress Field", str(artifacts.get("stress_field", "") or "")),
    ]
    context_title = ""
    context_path = ""
    for candidate_title, candidate_path in context_candidates:
        resolved = _resolve_optional_repo_path(candidate_path)
        if resolved is not None and resolved.exists():
            context_title = candidate_title
            context_path = serialize_repo_path(resolved)
            break

    panel_specs = [
        ("Before Layout", before_layout_path, "snapshot"),
        ("After Layout", after_layout_path, "snapshot"),
    ]
    if context_path:
        panel_specs.append((context_title, context_path, "linked field"))

    existing_panel_specs = []
    for title, path, subtitle in panel_specs:
        resolved = _resolve_optional_repo_path(path)
        if resolved is not None and resolved.exists():
            existing_panel_specs.append((title, serialize_repo_path(resolved), subtitle))

    if len(existing_panel_specs) < 2:
        return "", ["Step montage skipped because fewer than two source panels are available."]

    panel_images = [_panel_image(source_path=path, title=title, subtitle=subtitle) for title, path, subtitle in existing_panel_specs]
    try:
        panel_width = panel_images[0].width
        panel_height = panel_images[0].height
        gutter = 18
        margin = 20
        header_height = 36
        canvas = Image.new(
            "RGB",
            (
                margin * 2 + panel_width * len(panel_images) + gutter * max(0, len(panel_images) - 1),
                margin * 2 + header_height + panel_height,
            ),
            color=(242, 244, 247),
        )
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default()
        draw.text((margin, 12), f"Step Montage: {step_dir.name}", fill=_LAYOUT_TEXT, font=font)
        cursor_x = margin
        cursor_y = margin + header_height
        for image in panel_images:
            canvas.paste(image, (cursor_x, cursor_y))
            cursor_x += image.width + gutter
        destination = step_dir / "step_montage.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(destination)
    finally:
        for image in panel_images:
            image.close()

    notes = ["Step montage built from local before/after layout views."]
    if context_path:
        notes.append(f"Context panel linked from {context_title.lower()}.")
    return serialize_repo_path(destination), notes


def _build_profile_montage_sheet(
    *,
    destination: Path,
    title: str,
    items: Sequence[tuple[str, str]],
) -> tuple[str, list[str]]:
    available_items: list[tuple[str, str]] = []
    for label, path in list(items or []):
        resolved = _resolve_optional_repo_path(path)
        if resolved is None or not resolved.exists():
            continue
        available_items.append((str(label), serialize_repo_path(resolved)))

    if not available_items:
        return "", ["Aggregate montage skipped because no source step montages are available."]

    panel_images = [_panel_image(source_path=path, title=label, subtitle="step montage", panel_size=(300, 220)) for label, path in available_items]
    try:
        columns = min(3, len(panel_images))
        rows = max(1, (len(panel_images) + columns - 1) // columns)
        panel_width = panel_images[0].width
        panel_height = panel_images[0].height
        gutter = 18
        margin = 20
        header_height = 38
        canvas = Image.new(
            "RGB",
            (
                margin * 2 + panel_width * columns + gutter * max(0, columns - 1),
                margin * 2 + header_height + panel_height * rows + gutter * max(0, rows - 1),
            ),
            color=(241, 244, 247),
        )
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default()
        draw.text((margin, 12), title, fill=_LAYOUT_TEXT, font=font)
        for index, image in enumerate(panel_images):
            row = index // columns
            col = index % columns
            x = margin + col * (panel_width + gutter)
            y = margin + header_height + row * (panel_height + gutter)
            canvas.paste(image, (x, y))
        destination.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(destination)
    finally:
        for image in panel_images:
            image.close()

    return serialize_repo_path(destination), [f"Aggregate montage built from {len(available_items)} step montages."]


def _package_entry_step_label(entry: Mapping[str, Any]) -> str:
    return f"step_{int(dict(entry or {}).get('step_index', 0) or 0):04d}"


def _package_entry_montage_label(entry: Mapping[str, Any]) -> str:
    payload = dict(entry or {})
    step_label = _package_entry_step_label(payload)
    semantic_caption_short = str(payload.get("semantic_caption_short", "") or "").strip()
    if semantic_caption_short:
        return f"{step_label} | {semantic_caption_short}"
    family_label = str(payload.get("primary_action_family_label", "") or "").strip()
    if family_label:
        return f"{step_label} | {family_label}"
    primary_action = str(payload.get("primary_action", "") or "").strip()
    if primary_action:
        return f"{step_label} | {primary_action}"
    return step_label


def _package_entry_aggregate_item(
    entry: Mapping[str, Any],
    *,
    path_key: str,
    path_override: str = "",
) -> Dict[str, Any]:
    payload = dict(entry or {})
    source_path = str(path_override or payload.get(path_key, "") or "").strip()
    return {
        "step_index": int(payload.get("step_index", 0) or 0),
        "sequence": int(payload.get("sequence", 0) or 0),
        "stage": str(payload.get("stage", "") or ""),
        "label": _package_entry_montage_label(payload),
        "path": source_path,
        "primary_action": str(payload.get("primary_action", "") or ""),
        "primary_action_family": str(payload.get("primary_action_family", "") or ""),
        "primary_action_family_label": str(payload.get("primary_action_family_label", "") or ""),
        "primary_action_label": str(payload.get("primary_action_label", "") or ""),
        "semantic_caption_short": str(payload.get("semantic_caption_short", "") or ""),
        "semantic_caption": str(payload.get("semantic_caption", "") or ""),
        "target_summary": str(payload.get("target_summary", "") or ""),
        "rule_summary": str(payload.get("rule_summary", "") or ""),
        "expected_effect_summary": str(payload.get("expected_effect_summary", "") or ""),
        "observed_effect_summary": str(payload.get("observed_effect_summary", "") or ""),
    }


def _aggregate_item_tuple(item: Mapping[str, Any]) -> tuple[str, str]:
    payload = dict(item or {})
    return (
        str(payload.get("label", "") or ""),
        str(payload.get("path", "") or ""),
    )


def _select_keyframe_montage_items(package_entries: Sequence[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    entries = [dict(entry) for entry in list(package_entries or [])]
    if not entries:
        return []
    if len(entries) <= 3:
        return [
            _package_entry_aggregate_item(
                entry,
                path_key="step_montage_path",
            )
            for entry in entries
        ]

    selected_indices = [0, len(entries) // 2, len(entries) - 1]
    ordered_unique: list[int] = []
    for index in selected_indices:
        if index not in ordered_unique:
            ordered_unique.append(index)
    return [
        _package_entry_aggregate_item(
            entries[index],
            path_key="step_montage_path",
        )
        for index in ordered_unique
    ]


def _select_dataset_overview_items(package_entries: Sequence[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    items: list[Dict[str, Any]] = []
    for entry in [dict(item) for item in list(package_entries or [])]:
        if not str(entry.get("field_case_dir", "") or "").strip():
            continue
        source_path = (
            str(entry.get("triptych_path", "") or "").strip()
            or str(entry.get("step_montage_path", "") or "").strip()
        )
        if not source_path:
            continue
        items.append(
            _package_entry_aggregate_item(
                entry,
                path_key="triptych_path",
                path_override=source_path,
            )
        )
    return items


def _sorted_family_counts(counts: Mapping[str, Any]) -> list[tuple[str, int]]:
    normalized: list[tuple[str, int]] = []
    for key, value in dict(counts or {}).items():
        family = str(key or "").strip()
        if not family:
            continue
        normalized.append((family, _safe_int(value, 0)))
    return sorted(
        normalized,
        key=lambda item: (
            -int(item[1]),
            int(get_operator_family_spec(item[0]).display_order if get_operator_family_spec(item[0]) else 9999),
            item[0],
        ),
    )


def _build_profile_operator_family_audit(package_entries: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    primary_family_counts: dict[str, int] = {}
    primary_family_labels: dict[str, str] = {}
    action_family_counts: dict[str, int] = {}
    action_family_labels: dict[str, str] = {}
    unmapped_actions: set[str] = set()
    package_steps_with_unmapped_actions: list[int] = []
    family_contract_warning_count = 0

    for entry in [dict(item) for item in list(package_entries or [])]:
        primary_family = str(entry.get("primary_action_family", "") or "").strip()
        primary_family_label = str(
            entry.get("primary_action_family_label", "") or _family_label(primary_family)
        ).strip()
        if primary_family:
            primary_family_counts[primary_family] = int(primary_family_counts.get(primary_family, 0) or 0) + 1
            primary_family_labels[primary_family] = primary_family_label or _family_label(primary_family)

        for family in [str(item).strip() for item in list(entry.get("action_family_sequence", []) or []) if str(item).strip()]:
            action_family_counts[family] = int(action_family_counts.get(family, 0) or 0) + 1
            action_family_labels.setdefault(family, _family_label(family))

        entry_unmapped_actions = [
            str(item).strip()
            for item in list(entry.get("unmapped_actions", []) or [])
            if str(item).strip()
        ]
        if entry_unmapped_actions:
            package_steps_with_unmapped_actions.append(int(entry.get("step_index", 0) or 0))
            unmapped_actions.update(entry_unmapped_actions)

        family_contract_warning_count += len(
            [
                str(item).strip()
                for item in list(entry.get("family_contract_warnings", []) or [])
                if str(item).strip()
            ]
        )

    dominant_primary_family = ""
    dominant_primary_family_label = ""
    sorted_primary_families = _sorted_family_counts(primary_family_counts)
    if sorted_primary_families:
        dominant_primary_family = str(sorted_primary_families[0][0] or "")
        dominant_primary_family_label = str(
            primary_family_labels.get(dominant_primary_family, "") or _family_label(dominant_primary_family)
        )

    return {
        "package_count": len(list(package_entries or [])),
        "dominant_primary_family": dominant_primary_family,
        "dominant_primary_family_label": dominant_primary_family_label,
        "primary_family_counts": primary_family_counts,
        "primary_family_labels": primary_family_labels,
        "action_family_counts": action_family_counts,
        "action_family_labels": action_family_labels,
        "unmapped_actions": sorted(unmapped_actions),
        "unmapped_action_count": int(len(unmapped_actions)),
        "package_steps_with_unmapped_actions": sorted(set(package_steps_with_unmapped_actions)),
        "family_contract_warning_count": int(family_contract_warning_count),
    }


def _build_profile_aggregate_outputs(
    *,
    profile_root: Path,
    profile_name: str,
    package_entries: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    timeline_path = profile_root / "montage" / "timeline_montage.png"
    keyframe_path = profile_root / "montage" / "keyframe_montage.png"
    dataset_overview_path = profile_root / "dataset_overview" / "case_grid.png"
    contract = get_review_profile_contract(profile_name)
    if contract.package_level != "full":
        skipped = {
            "path": "",
            "exists": False,
            "notes": ["Aggregate montage skipped by lightweight review profile contract."],
            "items": [],
        }
        return {
            "timeline_montage": dict(skipped),
            "keyframe_montage": dict(skipped),
            "dataset_overview": {
                "path": serialize_repo_path(dataset_overview_path),
                "exists": False,
                "notes": ["Dataset overview is still reserved as a planned path in this minimal slice."],
                "items": [],
            },
        }

    timeline_items = [
        _package_entry_aggregate_item(
            entry,
            path_key="step_montage_path",
        )
        for entry in list(package_entries or [])
    ]
    keyframe_items = _select_keyframe_montage_items(package_entries)
    dataset_overview_items = _select_dataset_overview_items(package_entries)
    timeline_actual_path, timeline_notes = _build_profile_montage_sheet(
        destination=timeline_path,
        title=f"Timeline Montage: {profile_name}",
        items=[_aggregate_item_tuple(item) for item in timeline_items],
    )
    keyframe_actual_path, keyframe_notes = _build_profile_montage_sheet(
        destination=keyframe_path,
        title=f"Keyframe Montage: {profile_name}",
        items=[_aggregate_item_tuple(item) for item in keyframe_items],
    )
    dataset_overview_actual_path, dataset_overview_notes = _build_profile_montage_sheet(
        destination=dataset_overview_path,
        title=f"Dataset Overview: {profile_name}",
        items=[_aggregate_item_tuple(item) for item in dataset_overview_items],
    )
    return {
        "timeline_montage": {
            "path": timeline_actual_path or serialize_repo_path(timeline_path),
            "exists": bool(timeline_actual_path),
            "notes": timeline_notes,
            "items": timeline_items,
        },
        "keyframe_montage": {
            "path": keyframe_actual_path or serialize_repo_path(keyframe_path),
            "exists": bool(keyframe_actual_path),
            "notes": keyframe_notes,
            "items": keyframe_items,
        },
        "dataset_overview": {
            "path": dataset_overview_actual_path or serialize_repo_path(dataset_overview_path),
            "exists": bool(dataset_overview_actual_path),
            "notes": dataset_overview_notes,
            "items": dataset_overview_items,
        },
    }


def _load_optional_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return load_json(path)
    except Exception:
        return {}


def _resolve_path_from_base(raw_path: Any, *, base_dir: Path | None = None) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        return Path()
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate.resolve()
    if base_dir is not None:
        scoped = (base_dir / candidate).resolve()
        if scoped.exists():
            return scoped
    return resolve_repo_path(candidate)


def _resolve_existing_path(raw_path: Any, *base_dirs: Path | None) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    candidate = Path(text)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        return resolved if resolved.exists() else None
    for base_dir in base_dirs:
        if base_dir is None:
            continue
        scoped = (base_dir / candidate).resolve()
        if scoped.exists():
            return scoped
    try:
        resolved = resolve_repo_path(candidate)
    except Exception:
        return None
    return resolved if resolved.exists() else None


def _normalize_field_case_candidate_path(path: Path) -> Path:
    candidate = path.resolve()
    if candidate.is_file():
        parent_name = candidate.parent.name.lower()
        filename = candidate.name.lower()
        if filename == "design_state.json":
            return candidate.parent
        if filename in {"manifest.json", "simulation_result.json"} and parent_name in _FIELD_CASE_SUBDIR_NAMES:
            return candidate.parent.parent
        if filename in _DATASET_SUMMARY_FILENAMES:
            return candidate.parent
    if candidate.is_dir() and candidate.name.lower() in _FIELD_CASE_SUBDIR_NAMES:
        return candidate.parent
    return candidate


def _infer_dataset_root(case_dir: Path) -> Path:
    if not case_dir:
        return Path()
    if case_dir.parent.name.lower() == "cases":
        return case_dir.parent.parent.resolve()
    return Path()


def _load_dataset_case_entry(summary_path: Path, case_dir: Path) -> Dict[str, Any]:
    if not summary_path.exists():
        return {}
    payload = _load_optional_json(summary_path)
    cases = list(payload.get("cases", []) or [])
    case_id = case_dir.name
    expected_case_dir = case_dir.resolve()
    for entry in cases:
        if not isinstance(entry, Mapping):
            continue
        candidate = dict(entry)
        if str(candidate.get("case_id", "") or "").strip() == case_id:
            return candidate
        candidate_case_dir = _resolve_existing_path(candidate.get("case_dir", ""), summary_path.parent)
        if candidate_case_dir is None:
            continue
        normalized_case_dir = _normalize_field_case_candidate_path(candidate_case_dir)
        if normalized_case_dir == expected_case_dir:
            return candidate
    return {}


def _supported_render_files_present(case_dir: Path) -> bool:
    renders_dir = case_dir / "renders"
    if not renders_dir.is_dir():
        return False
    for filenames in _RENDER_ASSET_FILENAMES.values():
        for filename in filenames:
            if (renders_dir / filename).exists():
                return True
    return False


def _inspect_legacy_case_candidate(path: Path) -> Dict[str, Any]:
    candidate = _normalize_field_case_candidate_path(path)
    info: Dict[str, Any] = {
        "candidate": candidate,
        "recognized": False,
        "recognized_inputs": [],
        "missing_signals": list(_MINIMAL_LEGACY_CASE_SIGNALS),
        "dataset_root": Path(),
        "field_summary_case": {},
        "render_summary_case": {},
    }
    if not candidate.exists() or not candidate.is_dir():
        return info

    dataset_root = _infer_dataset_root(candidate)
    field_summary_case = (
        _load_dataset_case_entry(dataset_root / "field_run_summary.json", candidate)
        if dataset_root.exists()
        else {}
    )
    render_summary_case = (
        _load_dataset_case_entry(dataset_root / "render_summary.json", candidate)
        if dataset_root.exists()
        else {}
    )

    recognized_inputs: list[str] = []
    if (candidate / "design_state.json").exists():
        recognized_inputs.append("design_state.json")
    if (
        (candidate / "field_exports" / "manifest.json").exists()
        or (candidate / "field_exports" / "simulation_result.json").exists()
        or field_summary_case
    ):
        recognized_inputs.append("field_exports")
    if (
        (candidate / "renders" / "manifest.json").exists()
        or _supported_render_files_present(candidate)
        or render_summary_case
    ):
        recognized_inputs.append("render_metadata")

    missing_signals: list[str] = []
    if "design_state.json" not in recognized_inputs:
        missing_signals.append(_MINIMAL_LEGACY_CASE_SIGNALS[0])
    if "field_exports" not in recognized_inputs:
        missing_signals.append(_MINIMAL_LEGACY_CASE_SIGNALS[1])
    if "render_metadata" not in recognized_inputs:
        missing_signals.append(_MINIMAL_LEGACY_CASE_SIGNALS[2])
    if not field_summary_case and not render_summary_case:
        missing_signals.append(_MINIMAL_LEGACY_CASE_SIGNALS[3])

    info.update(
        {
            "recognized": bool(recognized_inputs),
            "recognized_inputs": recognized_inputs,
            "missing_signals": missing_signals,
            "dataset_root": dataset_root,
            "field_summary_case": field_summary_case,
            "render_summary_case": render_summary_case,
        }
    )
    return info


def _case_contract_mode(recognized_inputs: Sequence[str]) -> str:
    normalized = set(str(item).strip() for item in list(recognized_inputs or []) if str(item).strip())
    if normalized == {"design_state.json"}:
        return "design_state_only"
    if normalized == {"field_exports"}:
        return "field_exports_only"
    if normalized == {"render_metadata"}:
        return "render_metadata_only"
    if normalized == {"field_exports", "render_metadata"}:
        return "field_exports_render_metadata"
    if normalized == {"design_state.json", "field_exports"}:
        return "design_state_field_exports"
    if normalized == {"design_state.json", "render_metadata"}:
        return "design_state_render_metadata"
    if normalized >= {"design_state.json", "field_exports", "render_metadata"}:
        return "design_state_field_exports_render_metadata"
    return "legacy_case_mixed"


def _require_field_case_dir(path: Path, *, context: str) -> Path:
    candidate = _normalize_field_case_candidate_path(path)
    if not candidate.exists():
        raise FileNotFoundError(f"{context}: case path not found: {path}")
    if _is_field_dataset_root(candidate):
        raise ValueError(
            f"{context}: expected a case directory or supported case subpath, got dataset root {serialize_repo_path(candidate)}."
        )
    inspection = _inspect_legacy_case_candidate(candidate)
    if inspection["recognized"]:
        return Path(inspection["candidate"])
    raise ValueError(
        f"{context}: unsupported legacy case contract at {serialize_repo_path(candidate)}. "
        f"Minimal adapter requires at least one of: {', '.join(_MINIMAL_LEGACY_CASE_SIGNALS)}."
    )


def _is_field_case_dir(path: Path) -> bool:
    return bool(_inspect_legacy_case_candidate(path).get("recognized"))


def _is_field_dataset_root(path: Path) -> bool:
    candidate = _normalize_field_case_candidate_path(path)
    return candidate.is_dir() and (candidate / "cases").is_dir()


def _build_expected_step_specs(records: Sequence[Dict[str, Any]]) -> list[Dict[str, Any]]:
    expected_steps: list[Dict[str, Any]] = []
    for step_index, (_, after_record) in enumerate(zip(records[:-1], records[1:]), start=1):
        event = dict(after_record.get("event", {}) or {})
        snapshot = dict(after_record.get("snapshot", {}) or {})
        expected_steps.append(
            {
                "step_index": int(step_index),
                "sequence": _safe_int(event.get("sequence", 0)),
                "stage": str(event.get("stage", snapshot.get("stage", "")) or ""),
            }
        )
    return expected_steps


def _new_field_case_audit(expected_step_count: int) -> Dict[str, Any]:
    return {
        "schema_version": "iteration_review_field_case_audit/v1",
        "mapping_priority": [
            "explicit_step_index",
            "explicit_sequence",
            "dataset_summary_case_order",
            "dataset_case_order",
            "default_case_dir",
        ],
        "expected_step_count": int(expected_step_count),
        "mapped_step_count": 0,
        "matched_step_count": 0,
        "defaulted_step_count": 0,
        "unmapped_step_count": int(expected_step_count),
        "compatible_case_count": 0,
        "incompatible_case_count": 0,
        "ambiguous_binding_count": 0,
        "matched_by_index_count": 0,
        "matched_by_sequence_count": 0,
        "matched_by_summary_case_order_count": 0,
        "matched_by_case_order_count": 0,
        "defaulted_by_case_dir_count": 0,
        "compatible_cases": [],
        "incompatible_cases": [],
        "ambiguous_bindings": [],
        "step_resolutions": [],
    }


def _field_case_mapping_is_active(mapping_payload: Mapping[str, Any]) -> bool:
    if str(mapping_payload.get("mapping_source", "") or "").strip() not in {"", "none"}:
        return True
    if str(mapping_payload.get("dataset_root", "") or "").strip():
        return True
    if str(mapping_payload.get("default_case_dir", "") or "").strip():
        return True
    for key in (
        "mapped_step_count",
        "compatible_case_count",
        "incompatible_case_count",
        "ambiguous_binding_count",
    ):
        if int(mapping_payload.get(key, 0) or 0) > 0:
            return True
    return False


def _build_profile_field_case_gate(
    *,
    profile_name: str,
    contract: Any,
    field_case_mapping: Mapping[str, Any],
) -> Dict[str, Any]:
    gate_contract = getattr(contract, "field_case_gate", None)
    mode = str(getattr(gate_contract, "mode", "off") or "off").strip() or "off"
    allowed_resolution_sources = [
        str(item).strip()
        for item in list(getattr(gate_contract, "allowed_resolution_sources", []) or [])
        if str(item).strip()
    ]
    step_resolutions = [
        dict(item)
        for item in list(field_case_mapping.get("step_resolutions", []) or [])
        if isinstance(item, Mapping)
    ]
    observed_resolution_sources = sorted(
        {
            str(item.get("resolution_source", "") or "").strip()
            for item in step_resolutions
            if str(item.get("status", "") or "").strip() == "matched"
            and str(item.get("resolution_source", "") or "").strip()
        }
    )
    active = _field_case_mapping_is_active(field_case_mapping)
    result: Dict[str, Any] = {
        "schema_version": "iteration_review_profile_field_case_gate/v1",
        "profile_name": str(profile_name or ""),
        "mode": mode,
        "active": bool(active),
        "passed": True,
        "status": "off" if mode == "off" else ("not_applicable" if not active else "passed"),
        "enforcement_action": "allow",
        "allowed_resolution_sources": allowed_resolution_sources,
        "observed_resolution_sources": observed_resolution_sources,
        "violation_count": 0,
        "violations": [],
        "reason": "",
        "notes": [],
    }

    if mode == "off":
        result["reason"] = "Review profile does not enforce field-case linkage strictness."
        result["notes"] = [str(result["reason"])]
        return result

    if not active:
        result["reason"] = "No linked field-case input is active; strict teacher-demo gate is not enforced."
        result["notes"] = [str(result["reason"])]
        return result

    violations: list[Dict[str, Any]] = []

    def _add_count_violation(code: str, count: int, message: str) -> None:
        if count <= 0:
            return
        violations.append(
            {
                "code": code,
                "count": int(count),
                "message": str(message),
            }
        )

    if bool(getattr(gate_contract, "require_zero_ambiguous_bindings", True)):
        _add_count_violation(
            "ambiguous_binding_count",
            int(field_case_mapping.get("ambiguous_binding_count", 0) or 0),
            "Ambiguous field-case bindings are not allowed for this review profile.",
        )
    if bool(getattr(gate_contract, "require_zero_incompatible_cases", True)):
        _add_count_violation(
            "incompatible_case_count",
            int(field_case_mapping.get("incompatible_case_count", 0) or 0),
            "Incompatible legacy cases remain in the linked dataset.",
        )
    if bool(getattr(gate_contract, "require_zero_defaulted", True)):
        _add_count_violation(
            "defaulted_step_count",
            int(field_case_mapping.get("defaulted_step_count", 0) or 0),
            "Defaulted field-case fallback is not allowed for this review profile.",
        )
    if bool(getattr(gate_contract, "require_zero_unmapped", True)):
        _add_count_violation(
            "unmapped_step_count",
            int(field_case_mapping.get("unmapped_step_count", 0) or 0),
            "Unmapped steps are not allowed for this review profile.",
        )

    if allowed_resolution_sources:
        disallowed_steps: list[Dict[str, Any]] = []
        allowed_lookup = set(allowed_resolution_sources)
        for step_payload in step_resolutions:
            if str(step_payload.get("status", "") or "").strip() != "matched":
                continue
            resolution_source = str(step_payload.get("resolution_source", "") or "").strip()
            if not resolution_source or resolution_source in allowed_lookup:
                continue
            disallowed_steps.append(
                {
                    "step_index": int(step_payload.get("step_index", 0) or 0),
                    "sequence": int(step_payload.get("sequence", 0) or 0),
                    "stage": str(step_payload.get("stage", "") or ""),
                    "resolution_source": resolution_source,
                    "field_case_dir": str(step_payload.get("field_case_dir", "") or ""),
                }
            )
        if disallowed_steps:
            violations.append(
                {
                    "code": "disallowed_resolution_source",
                    "count": int(len(disallowed_steps)),
                    "message": (
                        "Matched field-case bindings used resolution sources outside the "
                        f"allowed teacher-demo contract: {', '.join(allowed_resolution_sources)}."
                    ),
                    "steps": disallowed_steps,
                }
            )

    passed = not violations
    result["passed"] = bool(passed)
    result["status"] = "passed" if passed else "blocked"
    result["enforcement_action"] = "build_packages" if passed else "skip_profile_packages"
    result["violation_count"] = int(len(violations))
    result["violations"] = violations
    if passed:
        result["reason"] = "Linked field-case mapping satisfies the strict teacher-demo contract."
        result["notes"] = [str(result["reason"])]
    else:
        result["reason"] = "; ".join(
            str(item.get("message", "") or "").strip()
            for item in violations
            if str(item.get("message", "") or "").strip()
        )
        result["notes"] = [
            "Teacher-demo package emission is blocked because the linked field-case mapping is not strict enough.",
            str(result["reason"]),
        ]
    return result


def _scan_dataset_summary_case_entries(dataset_root: Path, summary_filename: str) -> Dict[str, Any]:
    summary_path = dataset_root / summary_filename
    result: Dict[str, Any] = {
        "summary_path": "" if not summary_path.exists() else serialize_repo_path(summary_path),
        "entries_by_case_dir": {},
        "duplicates": [],
    }
    if not summary_path.exists():
        return result

    payload = _load_optional_json(summary_path)
    entries = list(payload.get("cases", []) or [])
    entries_by_case_dir: Dict[str, Dict[str, Any]] = {}
    duplicates: list[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        entry_payload = dict(entry)
        resolved_case_dir = _resolve_existing_path(
            entry_payload.get("case_dir", ""),
            summary_path.parent,
            dataset_root,
        )
        if resolved_case_dir is None:
            continue
        normalized_case_dir = _normalize_field_case_candidate_path(resolved_case_dir)
        serialized_case_dir = serialize_repo_path(normalized_case_dir)
        if serialized_case_dir in entries_by_case_dir:
            duplicates.append(
                {
                    "summary_path": serialize_repo_path(summary_path),
                    "case_dir": serialized_case_dir,
                    "case_id": str(entry_payload.get("case_id", "") or ""),
                }
            )
            continue
        entries_by_case_dir[serialized_case_dir] = entry_payload

    result["entries_by_case_dir"] = entries_by_case_dir
    result["duplicates"] = duplicates
    return result


def _resolve_field_case_binding_for_step(
    *,
    step_index: int,
    sequence: int,
    stage: str,
    field_case_plan: Mapping[str, Any],
) -> Dict[str, Any]:
    steps_by_index = dict(field_case_plan.get("steps_by_index", {}) or {})
    steps_by_sequence = dict(field_case_plan.get("steps_by_sequence", {}) or {})
    index_entry = dict(steps_by_index.get(int(step_index), {}) or {})
    sequence_entry = dict(steps_by_sequence.get(int(sequence), {}) or {})
    index_case_dir = str(index_entry.get("field_case_dir", "") or "").strip()
    sequence_case_dir = str(sequence_entry.get("field_case_dir", "") or "").strip()
    ambiguous = bool(index_case_dir and sequence_case_dir and index_case_dir != sequence_case_dir)

    if index_case_dir:
        return {
            "step_index": int(step_index),
            "sequence": int(sequence),
            "stage": str(stage or ""),
            "field_case_dir": index_case_dir,
            "status": "matched",
            "resolution_source": str(index_entry.get("resolution_source", "") or "explicit_step_index"),
            "notes": [str(item) for item in list(index_entry.get("notes", []) or []) if str(item).strip()],
            "ambiguous": ambiguous,
            "conflict_case_dir": sequence_case_dir if ambiguous else "",
        }

    if sequence_case_dir:
        return {
            "step_index": int(step_index),
            "sequence": int(sequence),
            "stage": str(stage or ""),
            "field_case_dir": sequence_case_dir,
            "status": "matched",
            "resolution_source": str(sequence_entry.get("resolution_source", "") or "explicit_sequence"),
            "notes": [str(item) for item in list(sequence_entry.get("notes", []) or []) if str(item).strip()],
            "ambiguous": False,
            "conflict_case_dir": "",
        }

    default_case_dir = str(field_case_plan.get("default_case_dir", "") or "").strip()
    if default_case_dir:
        return {
            "step_index": int(step_index),
            "sequence": int(sequence),
            "stage": str(stage or ""),
            "field_case_dir": default_case_dir,
            "status": "defaulted",
            "resolution_source": "default_case_dir",
            "notes": ["Resolved by default_case_dir fallback."],
            "ambiguous": False,
            "conflict_case_dir": "",
        }

    return {
        "step_index": int(step_index),
        "sequence": int(sequence),
        "stage": str(stage or ""),
        "field_case_dir": "",
        "status": "unmapped",
        "resolution_source": "none",
        "notes": ["No compatible field case mapping was resolved for this step."],
        "ambiguous": False,
        "conflict_case_dir": "",
    }


def _finalize_field_case_plan(plan: Dict[str, Any], records: Sequence[Dict[str, Any]], *, strict_explicit_conflicts: bool) -> Dict[str, Any]:
    expected_steps = _build_expected_step_specs(records)
    audit = dict(plan.get("audit", {}) or _new_field_case_audit(len(expected_steps)))
    audit["expected_step_count"] = int(len(expected_steps))
    step_resolutions: list[Dict[str, Any]] = []
    ambiguous_bindings: list[Dict[str, Any]] = list(audit.get("ambiguous_bindings", []) or [])

    matched_step_count = 0
    defaulted_step_count = 0
    unmapped_step_count = 0
    matched_by_index_count = 0
    matched_by_sequence_count = 0
    matched_by_summary_case_order_count = 0
    matched_by_case_order_count = 0
    defaulted_by_case_dir_count = 0

    for step_spec in expected_steps:
        binding = _resolve_field_case_binding_for_step(
            step_index=int(step_spec.get("step_index", 0) or 0),
            sequence=int(step_spec.get("sequence", 0) or 0),
            stage=str(step_spec.get("stage", "") or ""),
            field_case_plan=plan,
        )
        if binding["ambiguous"]:
            ambiguity = {
                "step_index": int(binding["step_index"]),
                "sequence": int(binding["sequence"]),
                "stage": str(binding["stage"]),
                "selected_case_dir": str(binding["field_case_dir"]),
                "conflict_case_dir": str(binding["conflict_case_dir"]),
                "reason": "step_index and sequence bindings resolve to different case dirs",
            }
            ambiguous_bindings.append(ambiguity)
            if strict_explicit_conflicts:
                raise ValueError(
                    "Ambiguous explicit field_case_map binding for "
                    f"step_index={binding['step_index']} sequence={binding['sequence']}: "
                    f"{binding['field_case_dir']} vs {binding['conflict_case_dir']}"
                )

        step_resolutions.append(
            {
                "step_index": int(binding["step_index"]),
                "sequence": int(binding["sequence"]),
                "stage": str(binding["stage"]),
                "status": str(binding["status"]),
                "resolution_source": str(binding["resolution_source"]),
                "field_case_dir": str(binding["field_case_dir"]),
                "notes": [str(item) for item in list(binding.get("notes", []) or []) if str(item).strip()],
            }
        )

        if binding["status"] == "matched":
            matched_step_count += 1
            if binding["resolution_source"] == "explicit_step_index":
                matched_by_index_count += 1
            elif binding["resolution_source"] == "explicit_sequence":
                matched_by_sequence_count += 1
            elif binding["resolution_source"] == "dataset_summary_case_order":
                matched_by_summary_case_order_count += 1
            elif binding["resolution_source"] == "dataset_case_order":
                matched_by_case_order_count += 1
        elif binding["status"] == "defaulted":
            defaulted_step_count += 1
            defaulted_by_case_dir_count += 1
        else:
            unmapped_step_count += 1

    audit["matched_step_count"] = int(matched_step_count)
    audit["defaulted_step_count"] = int(defaulted_step_count)
    audit["mapped_step_count"] = int(matched_step_count + defaulted_step_count)
    audit["unmapped_step_count"] = int(unmapped_step_count)
    audit["matched_by_index_count"] = int(matched_by_index_count)
    audit["matched_by_sequence_count"] = int(matched_by_sequence_count)
    audit["matched_by_summary_case_order_count"] = int(matched_by_summary_case_order_count)
    audit["matched_by_case_order_count"] = int(matched_by_case_order_count)
    audit["defaulted_by_case_dir_count"] = int(defaulted_by_case_dir_count)
    audit["ambiguous_bindings"] = ambiguous_bindings
    audit["ambiguous_binding_count"] = int(len(ambiguous_bindings))
    audit["step_resolutions"] = step_resolutions
    plan["audit"] = audit
    plan["resolved_steps"] = {
        int(item["step_index"]): dict(item)
        for item in step_resolutions
    }
    return plan


def _build_field_case_plan(
    *,
    run_path: Path,
    records: Sequence[Dict[str, Any]],
    field_case_dir: str | Path | None,
    field_case_map: str | Path | Mapping[str, Any] | None,
) -> Dict[str, Any]:
    expected_steps = _build_expected_step_specs(records)
    plan: Dict[str, Any] = {
        "mapping_source": "none",
        "dataset_root": "",
        "default_case_dir": "",
        "steps_by_index": {},
        "steps_by_sequence": {},
        "audit": _new_field_case_audit(len(expected_steps)),
    }

    if field_case_map is not None:
        payload: Dict[str, Any]
        base_dir: Path | None = None
        if isinstance(field_case_map, Mapping):
            payload = dict(field_case_map or {})
        else:
            map_path = _resolve_path_from_base(field_case_map, base_dir=run_path)
            if not map_path.exists():
                raise FileNotFoundError(f"field_case_map not found: {field_case_map}")
            payload = _load_optional_json(map_path)
            base_dir = map_path.parent
            plan["mapping_source"] = serialize_repo_path(map_path)
        if payload:
            normalized = IterationReviewFieldCaseMap.model_validate(payload)
            dataset_root = _resolve_path_from_base(normalized.dataset_root, base_dir=base_dir)
            default_case_dir_path = _resolve_path_from_base(normalized.default_case_dir, base_dir=base_dir)
            if dataset_root.exists():
                plan["dataset_root"] = serialize_repo_path(dataset_root)
            if str(normalized.default_case_dir or "").strip():
                default_case_dir_path = _require_field_case_dir(
                    default_case_dir_path,
                    context="field_case_map.default_case_dir",
                )
                plan["default_case_dir"] = serialize_repo_path(default_case_dir_path)
            for entry in normalized.steps:
                case_dir = _resolve_path_from_base(entry.field_case_dir, base_dir=base_dir)
                case_dir = _require_field_case_dir(
                    case_dir,
                    context=f"field_case_map.steps[{int(entry.step_index)}].field_case_dir",
                )
                entry_payload = entry.model_dump(mode="json")
                entry_payload["field_case_dir"] = serialize_repo_path(case_dir)
                step_index_key = int(entry.step_index)
                sequence_key = int(entry.sequence)
                if step_index_key > 0:
                    existing = dict(plan["steps_by_index"].get(step_index_key, {}) or {})
                    if existing and str(existing.get("field_case_dir", "") or "") != entry_payload["field_case_dir"]:
                        raise ValueError(
                            f"Ambiguous explicit field_case_map step_index {step_index_key}: "
                            f"{existing.get('field_case_dir', '')} vs {entry_payload['field_case_dir']}"
                        )
                    entry_payload["resolution_source"] = "explicit_step_index"
                    plan["steps_by_index"][step_index_key] = dict(entry_payload)
                else:
                    entry_payload["resolution_source"] = "explicit_sequence"
                if sequence_key > 0:
                    existing = dict(plan["steps_by_sequence"].get(sequence_key, {}) or {})
                    if existing and str(existing.get("field_case_dir", "") or "") != entry_payload["field_case_dir"]:
                        raise ValueError(
                            f"Ambiguous explicit field_case_map sequence {sequence_key}: "
                            f"{existing.get('field_case_dir', '')} vs {entry_payload['field_case_dir']}"
                        )
                    sequence_payload = dict(entry_payload)
                    sequence_payload["resolution_source"] = "explicit_sequence"
                    plan["steps_by_sequence"][sequence_key] = sequence_payload
            compatible_case_dirs = sorted(
                {
                    str(dict(payload).get("field_case_dir", "") or "")
                    for payload in list(plan["steps_by_index"].values()) + list(plan["steps_by_sequence"].values())
                    if str(dict(payload).get("field_case_dir", "") or "").strip()
                }
            )
            plan["audit"]["compatible_case_count"] = len(compatible_case_dirs)
            plan["audit"]["compatible_cases"] = [
                {"case_dir": case_dir, "recognition_source": "explicit_map"}
                for case_dir in compatible_case_dirs
            ]
            if plan["mapping_source"] == "none":
                plan["mapping_source"] = str(normalized.mapping_source or "inline_map")
            return _finalize_field_case_plan(plan, records, strict_explicit_conflicts=True)

    if field_case_dir is None:
        return _finalize_field_case_plan(plan, records, strict_explicit_conflicts=False)

    case_root = _resolve_path_from_base(field_case_dir, base_dir=run_path)
    if not case_root.exists():
        raise FileNotFoundError(f"field_case_dir not found: {field_case_dir}")
    if _is_field_case_dir(case_root):
        case_root = _require_field_case_dir(case_root, context="field_case_dir")
        plan["mapping_source"] = "single_case_dir"
        plan["default_case_dir"] = serialize_repo_path(case_root)
        plan["audit"]["compatible_case_count"] = 1
        plan["audit"]["compatible_cases"] = [
            {"case_dir": serialize_repo_path(case_root), "recognition_source": "single_case_dir"}
        ]
        return _finalize_field_case_plan(plan, records, strict_explicit_conflicts=False)

    if _is_field_dataset_root(case_root):
        compatible_case_rows: list[Dict[str, Any]] = []
        incompatible_cases: list[Dict[str, Any]] = []
        field_summary = _scan_dataset_summary_case_entries(case_root, "field_run_summary.json")
        render_summary = _scan_dataset_summary_case_entries(case_root, "render_summary.json")
        for candidate in sorted((case_root / "cases").iterdir()):
            inspection = _inspect_legacy_case_candidate(candidate)
            normalized_candidate = _normalize_field_case_candidate_path(candidate)
            serialized_case_dir = serialize_repo_path(normalized_candidate)
            if inspection["recognized"]:
                compatible_case_rows.append(
                    {
                        "case_dir": normalized_candidate,
                        "serialized_case_dir": serialized_case_dir,
                        "recognized_inputs": [str(item) for item in list(inspection.get("recognized_inputs", []) or [])],
                        "has_field_summary": serialized_case_dir in dict(field_summary.get("entries_by_case_dir", {}) or {}),
                        "has_render_summary": serialized_case_dir in dict(render_summary.get("entries_by_case_dir", {}) or {}),
                    }
                )
                continue
            incompatible_cases.append(
                {
                    "case_dir": serialized_case_dir,
                    "reason": "unsupported_legacy_case_contract",
                    "missing_signals": [str(item) for item in list(inspection.get("missing_signals", []) or []) if str(item).strip()],
                }
            )

        if not compatible_case_rows:
            raise ValueError(
                f"field_case_dir dataset root has no compatible legacy cases: {serialize_repo_path(case_root)}"
            )
        plan["mapping_source"] = "dataset_root"
        plan["dataset_root"] = serialize_repo_path(case_root)
        plan["default_case_dir"] = str(compatible_case_rows[0]["serialized_case_dir"])
        plan["audit"]["compatible_case_count"] = len(compatible_case_rows)
        plan["audit"]["incompatible_case_count"] = len(incompatible_cases)
        plan["audit"]["compatible_cases"] = [
            {
                "case_dir": str(item["serialized_case_dir"]),
                "recognized_inputs": list(item["recognized_inputs"]),
                "recognition_source": (
                    "dataset_summary_case_order"
                    if item["has_field_summary"] or item["has_render_summary"]
                    else "dataset_case_order"
                ),
            }
            for item in compatible_case_rows
        ]
        plan["audit"]["incompatible_cases"] = incompatible_cases
        plan["audit"]["ambiguous_bindings"] = list(field_summary.get("duplicates", []) or []) + list(render_summary.get("duplicates", []) or [])
        for step_index, case_row in enumerate(compatible_case_rows, start=1):
            resolution_source = (
                "dataset_summary_case_order"
                if bool(case_row["has_field_summary"]) or bool(case_row["has_render_summary"])
                else "dataset_case_order"
            )
            resolution_notes = ["Mapped by dataset case order."]
            if resolution_source == "dataset_summary_case_order":
                resolution_notes.append("Dataset summary entry is available for this case.")
            plan["steps_by_index"][step_index] = {
                "step_index": step_index,
                "sequence": 0,
                "stage": "",
                "field_case_dir": str(case_row["serialized_case_dir"]),
                "physics_profile": "",
                "resolution_source": resolution_source,
                "notes": resolution_notes,
            }
        return _finalize_field_case_plan(plan, records, strict_explicit_conflicts=False)

    raise ValueError(
        f"field_case_dir does not match the minimal legacy case adapter contract: {serialize_repo_path(case_root)}"
    )


def _resolve_field_case_dir_for_step(
    *,
    step_index: int,
    sequence: int,
    field_case_plan: Mapping[str, Any],
) -> str:
    steps_by_index = dict(field_case_plan.get("steps_by_index", {}) or {})
    if int(step_index) in steps_by_index:
        return str(dict(steps_by_index[int(step_index)]).get("field_case_dir", "") or "")
    steps_by_sequence = dict(field_case_plan.get("steps_by_sequence", {}) or {})
    if int(sequence) in steps_by_sequence:
        return str(dict(steps_by_sequence[int(sequence)]).get("field_case_dir", "") or "")
    return str(field_case_plan.get("default_case_dir", "") or "")


def _extract_metrics(record: Dict[str, Any]) -> Dict[str, Any]:
    event = dict(record.get("event", {}) or {})
    snapshot = dict(record.get("snapshot", {}) or {})
    metrics = dict(event.get("metrics", {}) or {})
    metrics.update(dict(snapshot.get("metrics", {}) or {}))
    return metrics


def _build_state_index(record: Dict[str, Any]) -> ReviewStateIndex:
    event = dict(record.get("event", {}) or {})
    snapshot = dict(record.get("snapshot", {}) or {})
    design_state = dict(snapshot.get("design_state", {}) or {})
    metadata = dict(snapshot.get("metadata", {}) or {})
    components = list(design_state.get("components", []) or [])
    return ReviewStateIndex(
        snapshot_path=str(record.get("persisted_snapshot_path", "") or ""),
        stage=str(event.get("stage", snapshot.get("stage", "")) or ""),
        sequence=_safe_int(event.get("sequence", 0)),
        iteration=_safe_int(event.get("iteration", snapshot.get("iteration", 0))),
        attempt=_safe_int(event.get("attempt", 0)),
        thermal_source=str(event.get("thermal_source", snapshot.get("thermal_source", "")) or ""),
        diagnosis_status=str(event.get("diagnosis_status", snapshot.get("diagnosis_status", "")) or ""),
        diagnosis_reason=str(event.get("diagnosis_reason", snapshot.get("diagnosis_reason", "")) or ""),
        component_count=len(components),
        layout_state_hash=str(metadata.get("layout_state_hash", "") or ""),
        metrics=_extract_metrics(record),
    )


def _canonical_metric_values(raw_metrics: Mapping[str, Any], *, source_name: str) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    canonical: Dict[str, Dict[str, Any]] = {}
    unknown: Dict[str, Any] = {}
    for raw_key, value in dict(raw_metrics or {}).items():
        spec = get_metric_spec(str(raw_key))
        if spec is None:
            unknown[str(raw_key)] = value
            continue
        canonical[spec.key] = {
            "spec": spec,
            "value": value,
            "raw_key": str(raw_key),
            "source": source_name,
        }
    return canonical, unknown


_SUMMARY_UNIT_TO_REVIEW_UNIT_KEY = {
    "degc": "temperature_celsius",
    "k": "temperature_kelvin",
    "mm": "length_mm",
    "mpa": "stress_mpa",
    "pa": "stress_pa",
    "hz": "frequency_hz",
    "v": "electric_potential_v",
    "%": "percent",
    "1": "unitless",
}


def _resolve_metric_unit_spec(
    metric_key: str,
    spec,
    metric_unit_contract: Mapping[str, Any] | None,
) -> tuple[str, str]:
    unit_key = str(getattr(spec, "unit_key", "unitless") or "unitless")
    contract_payload = dict(dict(metric_unit_contract or {}).get(metric_key, {}) or {})
    summary_unit = str(contract_payload.get("summary_unit", "") or "").strip().lower()
    mapped_unit_key = _SUMMARY_UNIT_TO_REVIEW_UNIT_KEY.get(summary_unit)
    if mapped_unit_key and get_unit_spec(mapped_unit_key) is not None:
        unit_key = mapped_unit_key
    unit = get_unit_spec(unit_key)
    return unit_key, "" if unit is None else unit.symbol


def _build_metric_cards(
    before_metrics: Mapping[str, Any],
    after_metrics: Mapping[str, Any],
    physics_metrics: Mapping[str, Any],
    metric_unit_contract: Mapping[str, Any] | None = None,
) -> tuple[Dict[str, ReviewMetricCard], Dict[str, ReviewMetricDelta], Dict[str, Any]]:
    before_canonical, before_unknown = _canonical_metric_values(before_metrics, source_name="before_state")
    after_canonical, after_unknown = _canonical_metric_values(after_metrics, source_name="after_state")
    physics_canonical, physics_unknown = _canonical_metric_values(physics_metrics, source_name="physics_case")

    merged_after = dict(after_canonical)
    for key, payload in physics_canonical.items():
        merged_after[key] = payload

    cards: Dict[str, ReviewMetricCard] = {}
    deltas: Dict[str, ReviewMetricDelta] = {}
    unknown_metrics = dict(before_unknown)
    unknown_metrics.update(after_unknown)
    unknown_metrics.update(physics_unknown)

    for metric_key, payload in merged_after.items():
        spec = payload["spec"]
        unit_key, unit_symbol = _resolve_metric_unit_spec(metric_key, spec, metric_unit_contract)
        cards[metric_key] = ReviewMetricCard(
            key=metric_key,
            label=spec.label,
            value=payload["value"],
            unit_key=unit_key,
            unit_symbol=unit_symbol,
            direction=spec.direction,
            source=str(payload["source"]),
            raw_key=str(payload["raw_key"]),
        )

        before_payload = before_canonical.get(metric_key)
        before_value = None if before_payload is None else _safe_float(before_payload["value"])
        after_value = _safe_float(payload["value"])
        delta_value = None
        improved = None
        if before_value is not None and after_value is not None:
            delta_value = after_value - before_value
            if spec.direction == "minimize":
                improved = delta_value < 0.0
            elif spec.direction == "maximize":
                improved = delta_value > 0.0

        deltas[metric_key] = ReviewMetricDelta(
            key=metric_key,
            label=spec.label,
            before=before_value,
            after=after_value,
            delta=delta_value,
            improved=improved,
            direction=spec.direction,
            unit_key=unit_key,
            unit_symbol=unit_symbol,
        )

    return cards, deltas, unknown_metrics


def _build_observed_effects(metric_deltas: Mapping[str, ReviewMetricDelta]) -> list[str]:
    effects: list[str] = []
    for metric_key, delta in metric_deltas.items():
        if delta.delta is None:
            continue
        if delta.improved is True:
            effects.append(f"{metric_key}:improved")
        elif delta.improved is False:
            effects.append(f"{metric_key}:regressed")
    return effects


def _normalize_operator_dsl_version(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "legacy_compatible"
    lowered = text.lower()
    if lowered in {"v4", "dsl_v4", "opmaas-r4", "opmaas_r4"}:
        return "v4"
    if lowered.endswith(("v4", "r4")):
        return "v4"
    return text


def _family_label(family: str) -> str:
    spec = get_operator_family_spec(family)
    if spec is None:
        return str(family or "")
    return str(spec.label or family)


def _ordered_family_sequence(actions: Sequence[str]) -> list[str]:
    sequence: list[str] = []
    seen = set()
    for action in list(actions or []):
        family = _operator_action_family(action)
        if family in seen:
            continue
        seen.add(family)
        sequence.append(family)
    return sequence


def _build_operator_family_contract_warnings(
    *,
    review_profile: str,
    dsl_version: str,
    semantic_actions: Sequence[str],
    stubbed_actions: Sequence[str],
) -> list[str]:
    contract = get_review_profile_contract(review_profile)
    if str(dsl_version or "") != "v4":
        return []

    semantic_contract_actions = _merge_operator_actions(semantic_actions, stubbed_actions)
    unmapped = [
        action
        for action in semantic_contract_actions
        if _operator_action_family(action) == "other"
    ]
    if not unmapped:
        return []

    message = (
        f"Unmapped DSL v4 semantic actions: {', '.join(unmapped)} "
        f"(profile={review_profile})"
    )
    if str(contract.unknown_v4_family_policy or "allow") == "error":
        raise ValueError(message)
    if str(contract.unknown_v4_family_policy or "allow") == "warn":
        return [message]
    return []


def _build_operator_info(
    after_record: Dict[str, Any],
    metric_deltas: Mapping[str, ReviewMetricDelta],
    *,
    review_profile: str,
) -> OperatorActionInfo:
    event = dict(after_record.get("event", {}) or {})
    snapshot = dict(after_record.get("snapshot", {}) or {})
    metadata = dict(snapshot.get("metadata", {}) or {})
    event_metadata = dict(event.get("metadata", {}) or {})
    merged_metadata = dict(metadata)
    merged_metadata.update(event_metadata)

    runtime_actions = _parse_operator_actions(
        event.get("operator_actions", snapshot.get("operator_actions", []))
    )
    semantic_actions = _parse_operator_actions(merged_metadata.get("semantic_operator_actions", []))
    stubbed_actions = _parse_operator_actions(
        merged_metadata.get("selected_candidate_stubbed_actions", [])
    )
    actions = _merge_operator_actions(semantic_actions, runtime_actions, stubbed_actions)
    family_counts: Dict[str, int] = {}
    for action in actions:
        family = _operator_action_family(action)
        family_counts[family] = int(family_counts.get(family, 0) or 0) + 1
    action_family_sequence = _ordered_family_sequence(actions)
    unmapped_actions = [
        action for action in actions if _operator_action_family(action) == "other"
    ]

    raw_expected_effects = merged_metadata.get("expected_effects", [])
    expected_effects = raw_expected_effects
    if not isinstance(expected_effects, list):
        expected_effects = [str(expected_effects)] if str(expected_effects).strip() else []

    rationale = str(
        merged_metadata.get("operator_rationale", "")
        or merged_metadata.get("decision_rationale", "")
        or merged_metadata.get("change_summary", "")
        or ""
    )
    rule_engine_report = (
        merged_metadata.get("rule_engine_report")
        or merged_metadata.get("rule_engine")
        or {}
    )
    realization = merged_metadata.get("realization", {})
    dsl_version = _normalize_operator_dsl_version(
        merged_metadata.get("operator_dsl_version", "")
        or merged_metadata.get("selected_candidate_dsl_version", "")
        or "legacy_compatible"
    )
    primary_action = "" if not actions else str(actions[0])
    primary_action_family = (
        _operator_action_family(primary_action) if primary_action else ""
    )
    family_contract_warnings = _build_operator_family_contract_warnings(
        review_profile=review_profile,
        dsl_version=dsl_version,
        semantic_actions=semantic_actions,
        stubbed_actions=stubbed_actions,
    )
    semantic_display = build_operator_semantic_display(
        primary_action=primary_action,
        dsl_version=dsl_version,
        metadata=merged_metadata,
        expected_effects=raw_expected_effects,
        observed_effects=_build_observed_effects(metric_deltas),
        rule_engine_report=rule_engine_report if isinstance(rule_engine_report, Mapping) else {},
    )
    observed_effects = _build_observed_effects(metric_deltas)
    selected_semantic_action_payloads = merged_metadata.get(
        "selected_semantic_action_payloads",
        [],
    )

    return OperatorActionInfo(
        dsl_version=dsl_version,
        primary_action=primary_action,
        primary_action_family=primary_action_family,
        primary_action_family_label=_family_label(primary_action_family),
        primary_action_label=str(semantic_display.get("primary_action_label", "") or ""),
        semantic_caption_short=str(semantic_display.get("semantic_caption_short", "") or ""),
        semantic_caption=str(semantic_display.get("semantic_caption", "") or ""),
        target_summary=str(semantic_display.get("target_summary", "") or ""),
        rule_summary=str(semantic_display.get("rule_summary", "") or ""),
        expected_effect_summary=str(semantic_display.get("expected_effect_summary", "") or ""),
        observed_effect_summary=str(semantic_display.get("observed_effect_summary", "") or ""),
        action_types=actions,
        action_family_sequence=action_family_sequence,
        action_family_counts=family_counts,
        unmapped_actions=unmapped_actions,
        family_contract_warnings=family_contract_warnings,
        policy_id=str(merged_metadata.get("policy_id", "") or event.get("policy_id", "") or ""),
        candidate_id=str(
            merged_metadata.get("candidate_id", "")
            or merged_metadata.get("selected_candidate_id", "")
            or event.get("candidate_id", "")
            or ""
        ),
        program_id=str(
            merged_metadata.get("program_id", "")
            or event.get("program_id", "")
            or merged_metadata.get("selected_semantic_program_id", "")
            or event.get("selected_semantic_program_id", "")
            or merged_metadata.get("selected_operator_program_id", "")
            or event.get("selected_operator_program_id", "")
            or ""
        ),
        rationale=rationale,
        expected_effects=[str(item) for item in expected_effects if str(item).strip()],
        observed_effects=observed_effects,
        raw_operator_payload={
            "operator_actions": actions,
            "runtime_operator_actions": runtime_actions,
            "semantic_operator_actions": semantic_actions,
            "selected_candidate_stubbed_actions": stubbed_actions,
            "event_stage": str(event.get("stage", "") or ""),
            "selected_semantic_action_payloads": selected_semantic_action_payloads,
        },
        v4_reserved={
            "operator_program": {
                "version": str(merged_metadata.get("operator_program_version", "") or ""),
                "program_patch": merged_metadata.get("operator_program_patch", {}),
            },
            "selected_operator_program_id": str(
                merged_metadata.get("selected_operator_program_id", "")
                or event.get("selected_operator_program_id", "")
                or ""
            ),
            "selected_semantic_program_id": str(
                merged_metadata.get("selected_semantic_program_id", "")
                or event.get("selected_semantic_program_id", "")
                or ""
            ),
            "selected_candidate_dsl_version": str(
                merged_metadata.get("selected_candidate_dsl_version", "") or ""
            ),
            "semantic_unmapped_actions": [
                action
                for action in _merge_operator_actions(semantic_actions, stubbed_actions)
                if _operator_action_family(action) == "other"
            ],
            "semantic_operator_actions": semantic_actions,
            "selected_semantic_action_payloads": selected_semantic_action_payloads,
            "selected_candidate_stubbed_actions": stubbed_actions,
            "selected_candidate_realization_status": str(
                merged_metadata.get("selected_candidate_realization_status", "") or ""
            ),
            "selected_candidate_has_stub_realization": bool(
                merged_metadata.get("selected_candidate_has_stub_realization", False)
            ),
            "rule_engine_report": rule_engine_report,
            "rule_engine": merged_metadata.get("rule_engine", {}),
            "realization": realization if isinstance(realization, Mapping) else {},
        },
    )


def _discover_triptych_path(field_case_dir: Path) -> Path | None:
    if not field_case_dir:
        return None
    renders_dir = field_case_dir / "renders"
    for candidate in (
        renders_dir / "three_fields_horizontal.png",
        renders_dir / "triptych.png",
    ):
        if candidate.exists():
            return candidate
    return None


def _resolve_nested_mapping(key: str, *sources: Mapping[str, Any] | None) -> Dict[str, Any]:
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        payload = source.get(key)
        if isinstance(payload, Mapping) and dict(payload):
            return dict(payload)
    return {}


def _resolve_first_mapping(key: str, sources: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    for source in list(sources or []):
        if not isinstance(source, Mapping):
            continue
        payload = source.get(key)
        if isinstance(payload, Mapping) and dict(payload):
            return dict(payload)
    return {}


def _resolve_first_scalar(key: str, sources: Sequence[Mapping[str, Any]], *, default: str = "") -> str:
    for source in list(sources or []):
        if not isinstance(source, Mapping):
            continue
        value = source.get(key)
        if isinstance(value, str):
            if value.strip():
                return str(value)
            continue
        if value not in (None, {}, [], ()):
            return str(value)
    return str(default or "")


def _resolve_render_artifact_path(
    *,
    artifact_key: str,
    case_dir: Path,
    dataset_root: Path,
    render_manifest: Mapping[str, Any],
    render_summary_case: Mapping[str, Any],
    defaulted_fields: list[str],
) -> str:
    manifest_renders = dict(render_manifest.get("renders", {}) or {})
    summary_renders = dict(render_summary_case.get("renders", {}) or {})
    for alias in _RENDER_ASSET_ALIASES.get(artifact_key, ()):
        manifest_asset = _resolve_existing_path(
            manifest_renders.get(alias, ""),
            case_dir,
            case_dir / "renders",
            dataset_root,
        )
        if manifest_asset is not None:
            return serialize_repo_path(manifest_asset)
        summary_asset = _resolve_existing_path(
            summary_renders.get(alias, ""),
            case_dir,
            case_dir / "renders",
            dataset_root,
        )
        if summary_asset is not None:
            defaulted_fields.append(f"artifacts.{artifact_key}<-render_summary")
            return serialize_repo_path(summary_asset)
    for filename in _RENDER_ASSET_FILENAMES.get(artifact_key, ()):
        conventional_path = case_dir / "renders" / filename
        if conventional_path.exists():
            defaulted_fields.append(f"artifacts.{artifact_key}<-renders/{filename}")
            return serialize_repo_path(conventional_path)
    return ""


def _load_field_case_assets(field_case_dir: str | Path | None) -> Dict[str, Any]:
    if field_case_dir is None:
        return {}
    if not str(field_case_dir).strip():
        return {}
    raw_path = Path(field_case_dir)
    resolved_path = raw_path.resolve() if raw_path.is_absolute() else resolve_repo_path(raw_path)
    case_dir = _require_field_case_dir(resolved_path, context="field_case_dir")

    inspection = _inspect_legacy_case_candidate(case_dir)
    dataset_root = Path(inspection.get("dataset_root", Path()))
    field_summary_case = dict(inspection.get("field_summary_case", {}) or {})
    render_summary_case = dict(inspection.get("render_summary_case", {}) or {})
    defaulted_fields: list[str] = []
    adapter_notes: list[str] = []

    normalized_input = _normalize_field_case_candidate_path(resolved_path)
    if normalized_input != resolved_path:
        adapter_notes.append(
            f"Normalized legacy case input from {serialize_repo_path(resolved_path)} to {serialize_repo_path(normalized_input)}."
        )
    if field_summary_case:
        adapter_notes.append("Dataset field_run_summary.json entry available as legacy fallback.")
    if render_summary_case:
        adapter_notes.append("Dataset render_summary.json entry available as legacy fallback.")

    render_manifest_path = case_dir / "renders" / "manifest.json"
    field_manifest_path = case_dir / "field_exports" / "manifest.json"
    tensor_manifest_path = case_dir / "tensor" / "manifest.json"
    simulation_result_path = case_dir / "field_exports" / "simulation_result.json"

    render_manifest = _load_optional_json(render_manifest_path)
    field_manifest = _load_optional_json(field_manifest_path)
    tensor_manifest = _load_optional_json(tensor_manifest_path)
    simulation_result = _load_optional_json(simulation_result_path)

    source_candidates: tuple[Mapping[str, Any], ...] = (
        simulation_result,
        field_manifest,
        field_summary_case,
        tensor_manifest,
        render_manifest,
        render_summary_case,
    )
    raw_data = _resolve_nested_mapping(
        "raw_data",
        simulation_result,
        field_manifest,
        field_summary_case,
        tensor_manifest,
        render_manifest,
    )
    metric_sources = _resolve_nested_mapping(
        "metric_sources",
        raw_data,
        dict(simulation_result.get("raw_data", {}) or {}),
        dict(field_manifest.get("raw_data", {}) or {}),
        dict(field_summary_case.get("raw_data", {}) or {}),
    )
    source_claim_payload = _resolve_nested_mapping(
        "source_claim",
        raw_data,
        simulation_result,
        field_manifest,
        field_summary_case,
        tensor_manifest,
        render_manifest,
    )

    metrics = dict(simulation_result.get("metrics", {}) or {})
    if not metrics:
        metrics = dict(field_manifest.get("metrics", {}) or {})
    if not metrics:
        metrics = dict(field_summary_case.get("metrics", {}) or {})
        if metrics:
            defaulted_fields.append("metrics<-field_run_summary")

    contract_bundle = _resolve_first_mapping("contract_bundle", (raw_data,) + source_candidates)
    contract_versions = dict(contract_bundle.get("contract_versions", {}) or {})
    field_export_registry = _resolve_first_mapping("field_export_registry", (raw_data,) + source_candidates)
    simulation_metric_unit_contract = _resolve_first_mapping(
        "simulation_metric_unit_contract",
        (raw_data,) + source_candidates,
    )
    profile_audit_digest = _resolve_first_mapping("profile_audit_digest", (raw_data,) + source_candidates)
    if not profile_audit_digest and contract_bundle:
        profile_audit_digest = dict(contract_bundle.get("profile_audit_digest", {}) or {})

    physics_profile = str(
        contract_bundle.get("physics_profile", "")
        or source_claim_payload.get("physics_profile", "")
        or raw_data.get("physics_profile", "")
        or dict(field_manifest.get("driver_config", {}) or {}).get("thermal_evaluator_mode", "")
        or dict(field_summary_case.get("driver_config", {}) or {}).get("thermal_evaluator_mode", "")
        or metric_sources.get("thermal_source", "")
        or "field_case_linked"
    )
    if physics_profile == "field_case_linked":
        defaulted_fields.append("physics_profile<-field_case_linked")

    source_claim = {
        "thermal_source": str(
            source_claim_payload.get("thermal_source", "")
            or metric_sources.get("thermal_source", "")
            or ""
        ),
        "structural_source": str(
            source_claim_payload.get("structural_source", "")
            or metric_sources.get("structural_source", "")
            or ""
        ),
        "power_source": str(
            source_claim_payload.get("power_source", "")
            or metric_sources.get("power_source", "")
            or ""
        ),
        "field_data_source": str(
            source_claim_payload.get("field_data_source", "")
            or "tools/comsol_field_demo"
        ),
    }
    if not str(source_claim_payload.get("field_data_source", "") or "").strip():
        defaulted_fields.append("source_claim.field_data_source<-tools/comsol_field_demo")

    artifacts = {
        "geometry_overlay": _resolve_render_artifact_path(
            artifact_key="geometry_overlay",
            case_dir=case_dir,
            dataset_root=dataset_root,
            render_manifest=render_manifest,
            render_summary_case=render_summary_case,
            defaulted_fields=defaulted_fields,
        ),
        "temperature_field": _resolve_render_artifact_path(
            artifact_key="temperature_field",
            case_dir=case_dir,
            dataset_root=dataset_root,
            render_manifest=render_manifest,
            render_summary_case=render_summary_case,
            defaulted_fields=defaulted_fields,
        ),
        "displacement_field": _resolve_render_artifact_path(
            artifact_key="displacement_field",
            case_dir=case_dir,
            dataset_root=dataset_root,
            render_manifest=render_manifest,
            render_summary_case=render_summary_case,
            defaulted_fields=defaulted_fields,
        ),
        "stress_field": _resolve_render_artifact_path(
            artifact_key="stress_field",
            case_dir=case_dir,
            dataset_root=dataset_root,
            render_manifest=render_manifest,
            render_summary_case=render_summary_case,
            defaulted_fields=defaulted_fields,
        ),
        "triptych": _resolve_render_artifact_path(
            artifact_key="triptych",
            case_dir=case_dir,
            dataset_root=dataset_root,
            render_manifest=render_manifest,
            render_summary_case=render_summary_case,
            defaulted_fields=defaulted_fields,
        ),
    }
    if not artifacts["triptych"]:
        discovered_triptych = _discover_triptych_path(case_dir)
        if discovered_triptych is not None:
            defaulted_fields.append(f"artifacts.triptych<-renders/{discovered_triptych.name}")
            artifacts["triptych"] = serialize_repo_path(discovered_triptych)

    if not (case_dir / "design_state.json").exists() and not any(artifacts.values()) and not metrics:
        raise ValueError(
            f"field_case_dir: legacy case adapter could not resolve usable review inputs from {serialize_repo_path(case_dir)}."
        )

    contract_mode = _case_contract_mode(list(inspection.get("recognized_inputs", []) or []))
    return {
        "case_dir": serialize_repo_path(case_dir),
        "metrics": metrics,
        "physics_profile": physics_profile,
        "source_claim": source_claim,
        "case_contract_mode": contract_mode,
        "case_contract_inputs": [str(item) for item in list(inspection.get("recognized_inputs", []) or [])],
        "case_contract_defaults": list(dict.fromkeys(defaulted_fields)),
        "case_contract_notes": adapter_notes,
        "contract_bundle_version": _resolve_first_scalar(
            "contract_bundle_version",
            (raw_data,) + source_candidates,
            default=str(contract_bundle.get("bundle_version", "") or ""),
        ),
        "contract_bundle": contract_bundle,
        "field_export_registry_version": _resolve_first_scalar(
            "field_export_registry_version",
            (raw_data,) + source_candidates,
            default=str(contract_versions.get("field_export_registry", "") or ""),
        ),
        "field_export_registry": field_export_registry,
        "simulation_metric_unit_contract_version": _resolve_first_scalar(
            "simulation_metric_unit_contract_version",
            (raw_data,) + source_candidates,
            default=str(contract_versions.get("simulation_metric_unit_contract", "") or ""),
        ),
        "simulation_metric_unit_contract": simulation_metric_unit_contract,
        "profile_audit_digest_version": _resolve_first_scalar(
            "profile_audit_digest_version",
            (raw_data,) + source_candidates,
            default=str(contract_versions.get("profile_audit_digest", "") or ""),
        ),
        "profile_audit_digest": profile_audit_digest,
        "render_manifest_path": "" if not render_manifest_path.exists() else serialize_repo_path(render_manifest_path),
        "field_manifest_path": "" if not field_manifest_path.exists() else serialize_repo_path(field_manifest_path),
        "tensor_manifest_path": "" if not tensor_manifest_path.exists() else serialize_repo_path(tensor_manifest_path),
        "simulation_result_path": "" if not simulation_result_path.exists() else serialize_repo_path(simulation_result_path),
        "artifacts": artifacts,
    }


def _copy_or_build_triptych(
    *,
    step_dir: Path,
    field_assets: Mapping[str, Any],
) -> tuple[str, list[str]]:
    artifacts = dict(field_assets.get("artifacts", {}) or {})
    notes: list[str] = []
    destination = step_dir / "triptych.png"

    source_triptych = _resolve_optional_repo_path(artifacts.get("triptych", ""))
    if source_triptych is not None and source_triptych.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_triptych, destination)
        notes.append("Triptych copied from upstream field-case render output.")
        return serialize_repo_path(destination), notes

    field_paths = [
        _resolve_optional_repo_path(artifacts.get("temperature_field", "")),
        _resolve_optional_repo_path(artifacts.get("displacement_field", "")),
        _resolve_optional_repo_path(artifacts.get("stress_field", "")),
    ]
    if not all(path is not None and path.exists() for path in field_paths):
        notes.append("Triptych skipped because one or more field renders are missing.")
        return "", notes

    images = [Image.open(path).convert("RGB") for path in field_paths if path is not None]
    try:
        max_height = max(image.height for image in images)
        resized: list[Image.Image] = []
        for image in images:
            if image.height == max_height:
                resized.append(image.copy())
                continue
            width = max(1, int(round(image.width * (max_height / float(image.height)))))
            resized.append(image.resize((width, max_height)))
        total_width = sum(image.width for image in resized)
        canvas = Image.new("RGB", (total_width, max_height), color=(8, 8, 8))
        cursor_x = 0
        for image in resized:
            canvas.paste(image, (cursor_x, 0))
            cursor_x += image.width
        destination.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(destination)
        notes.append("Triptych built from temperature/displacement/stress PNGs.")
        return serialize_repo_path(destination), notes
    finally:
        for image in images:
            image.close()


def _materialize_profile_triptych(
    *,
    step_dir: Path,
    field_assets: Mapping[str, Any],
    profile_name: str,
) -> tuple[str, list[str]]:
    contract = get_review_profile_contract(profile_name)
    if contract.triptych_policy == "skip":
        return "", ["Triptych skipped by review profile contract."]
    return _copy_or_build_triptych(step_dir=step_dir, field_assets=field_assets)


def _artifact_ref(
    *,
    key: str,
    step_dir: Path,
    filename: str,
    artifact_type: str,
    actual_path: str = "",
    source_claim: str = "",
    notes: Iterable[str] | None = None,
) -> ReviewArtifactRef:
    actual = str(actual_path or "").strip()
    return ReviewArtifactRef(
        key=key,
        path=actual,
        planned_path=serialize_repo_path(step_dir / filename),
        exists=bool(actual),
        artifact_type=artifact_type,
        source_claim=str(source_claim or ""),
        notes=[str(item) for item in list(notes or []) if str(item).strip()],
    )


def _build_review_artifacts(
    *,
    step_dir: Path,
    manifest_path: Path,
    metrics_path: Path,
    before_record: Dict[str, Any],
    after_record: Dict[str, Any],
    field_assets: Mapping[str, Any],
    profile_name: str,
) -> Dict[str, ReviewArtifactRef]:
    artifacts = dict(field_assets.get("artifacts", {}) or {})
    source_claim = dict(field_assets.get("source_claim", {}) or {})
    thermal_claim = str(source_claim.get("thermal_source", "") or "")
    structural_claim = str(source_claim.get("structural_source", "") or "")
    before_layout_path, before_layout_notes = _render_layout_view(
        step_dir=step_dir,
        filename="geometry_before.png",
        record=before_record,
        state_label="before",
    )
    after_layout_path, after_layout_notes = _render_layout_view(
        step_dir=step_dir,
        filename="geometry_after.png",
        record=after_record,
        state_label="after",
    )
    triptych_path, triptych_notes = _materialize_profile_triptych(
        step_dir=step_dir,
        field_assets=field_assets,
        profile_name=profile_name,
    )
    step_montage_path, step_montage_notes = _build_step_montage(
        step_dir=step_dir,
        profile_name=profile_name,
        before_layout_path=before_layout_path,
        after_layout_path=after_layout_path,
        triptych_path=triptych_path,
        field_assets=field_assets,
    )

    return {
        "review_manifest": ReviewArtifactRef(
            key="review_manifest",
            path=serialize_repo_path(manifest_path),
            planned_path=serialize_repo_path(manifest_path),
            exists=True,
            artifact_type="json_manifest",
        ),
        "metrics_card": ReviewArtifactRef(
            key="metrics_card",
            path=serialize_repo_path(metrics_path),
            planned_path=serialize_repo_path(metrics_path),
            exists=True,
            artifact_type="json_metrics",
        ),
        "before_layout_view": _artifact_ref(
            key="before_layout_view",
            step_dir=step_dir,
            filename="geometry_before.png",
            artifact_type="layout_view",
            actual_path=before_layout_path,
            notes=before_layout_notes,
        ),
        "after_layout_view": _artifact_ref(
            key="after_layout_view",
            step_dir=step_dir,
            filename="geometry_after.png",
            artifact_type="layout_view",
            actual_path=after_layout_path,
            notes=after_layout_notes,
        ),
        "geometry_overlay": _artifact_ref(
            key="geometry_overlay",
            step_dir=step_dir,
            filename="geometry_overlay.png",
            artifact_type="field_overlay",
            actual_path=str(artifacts.get("geometry_overlay", "") or ""),
            notes=["Linked from tools/comsol_field_demo when available."],
        ),
        "temperature_field": _artifact_ref(
            key="temperature_field",
            step_dir=step_dir,
            filename="temperature_field.png",
            artifact_type="field_render",
            actual_path=str(artifacts.get("temperature_field", "") or ""),
            source_claim=thermal_claim,
            notes=["Linked from tools/comsol_field_demo when available."],
        ),
        "displacement_field": _artifact_ref(
            key="displacement_field",
            step_dir=step_dir,
            filename="displacement_field.png",
            artifact_type="field_render",
            actual_path=str(artifacts.get("displacement_field", "") or ""),
            source_claim=structural_claim,
            notes=["Linked from tools/comsol_field_demo when available."],
        ),
        "stress_field": _artifact_ref(
            key="stress_field",
            step_dir=step_dir,
            filename="stress_field.png",
            artifact_type="field_render",
            actual_path=str(artifacts.get("stress_field", "") or ""),
            source_claim=structural_claim,
            notes=["Linked from tools/comsol_field_demo when available."],
        ),
        "triptych": _artifact_ref(
            key="triptych",
            step_dir=step_dir,
            filename="triptych.png",
            artifact_type="triptych",
            actual_path=triptych_path,
            notes=triptych_notes,
        ),
        "step_montage": _artifact_ref(
            key="step_montage",
            step_dir=step_dir,
            filename="step_montage.png",
            artifact_type="step_montage",
            actual_path=step_montage_path,
            notes=step_montage_notes,
        ),
    }


def _build_physics_info(summary: Mapping[str, Any], after_record: Dict[str, Any], field_assets: Mapping[str, Any]) -> PhysicsProfileInfo:
    event = dict(after_record.get("event", {}) or {})
    source_claim_payload = dict(field_assets.get("source_claim", {}) or {})
    thermal_source = str(
        source_claim_payload.get("thermal_source", "")
        or event.get("thermal_source", "")
        or summary.get("thermal_evaluator_mode", "")
        or ""
    )
    source_claim = PhysicsSourceClaim(
        thermal_source=thermal_source,
        structural_source=str(source_claim_payload.get("structural_source", "") or ""),
        power_source=str(source_claim_payload.get("power_source", "") or ""),
        field_data_source=str(source_claim_payload.get("field_data_source", "") or ""),
        source_gate_passed=summary.get("source_gate_passed"),
        operator_family_gate_passed=summary.get("operator_family_gate_passed"),
        operator_realization_gate_passed=summary.get("operator_realization_gate_passed"),
        final_audit_status=str(summary.get("final_audit_status", "") or ""),
    )
    case_contract = CaseContractAdapterInfo(
        contract_mode=str(field_assets.get("case_contract_mode", "") or ""),
        recognized_inputs=[str(item) for item in list(field_assets.get("case_contract_inputs", []) or []) if str(item).strip()],
        defaulted_fields=[str(item) for item in list(field_assets.get("case_contract_defaults", []) or []) if str(item).strip()],
        notes=[str(item) for item in list(field_assets.get("case_contract_notes", []) or []) if str(item).strip()],
    )
    return PhysicsProfileInfo(
        physics_profile=str(
            field_assets.get("physics_profile", "")
            or summary.get("physics_profile", "")
            or thermal_source
            or "runtime_default"
        ),
        backend=str(summary.get("simulation_backend", "") or summary.get("backend", "") or ""),
        evaluator_mode=str(summary.get("thermal_evaluator_mode", "") or ""),
        source_claim=source_claim,
        case_contract=case_contract,
        contract_bundle_version=str(field_assets.get("contract_bundle_version", "") or ""),
        contract_bundle=dict(field_assets.get("contract_bundle", {}) or {}),
        field_export_registry_version=str(
            field_assets.get("field_export_registry_version", "") or ""
        ),
        field_export_registry=dict(field_assets.get("field_export_registry", {}) or {}),
        simulation_metric_unit_contract_version=str(
            field_assets.get("simulation_metric_unit_contract_version", "") or ""
        ),
        simulation_metric_unit_contract=dict(
            field_assets.get("simulation_metric_unit_contract", {}) or {}
        ),
        profile_audit_digest_version=str(
            field_assets.get("profile_audit_digest_version", "") or ""
        ),
        profile_audit_digest=dict(field_assets.get("profile_audit_digest", {}) or {}),
        final_mph_path=serialize_repo_path(summary.get("final_mph_path", "") or ""),
        field_case_dir=str(field_assets.get("case_dir", "") or ""),
        render_manifest_path=str(field_assets.get("render_manifest_path", "") or ""),
        field_manifest_path=str(field_assets.get("field_manifest_path", "") or ""),
        tensor_manifest_path=str(field_assets.get("tensor_manifest_path", "") or ""),
        simulation_result_path=str(field_assets.get("simulation_result_path", "") or ""),
        review_ready=bool(field_assets),
    )


def _package_notes(profile_name: str, field_assets: Mapping[str, Any], raw_metrics_unregistered: Mapping[str, Any]) -> list[str]:
    notes = [
        f"Review profile: {profile_name}.",
        "Minimal executable slice: manifest/index first, local layout views rendered, teacher-demo montage optional, linked field renders reused when available.",
    ]
    if not field_assets:
        notes.append("No linked COMSOL field case was provided; package remains a lightweight manifest.")
    else:
        notes.append(f"Linked field case: {field_assets.get('case_dir', '')}.")
        contract_mode = str(field_assets.get("case_contract_mode", "") or "").strip()
        if contract_mode:
            notes.append(f"Legacy case adapter mode: {contract_mode}.")
        for note in [str(item) for item in list(field_assets.get("case_contract_notes", []) or []) if str(item).strip()]:
            notes.append(note)
        defaulted = [str(item) for item in list(field_assets.get("case_contract_defaults", []) or []) if str(item).strip()]
        if defaulted:
            notes.append(f"Legacy case defaults: {', '.join(defaulted)}.")
    if raw_metrics_unregistered:
        notes.append("Some raw metrics are outside the current metric registry and stay under raw_metrics_unregistered.")
    return notes


def _build_iteration_review_package(
    *,
    run_path: Path,
    summary: Mapping[str, Any],
    profile_name: str,
    step_index: int,
    before_record: Dict[str, Any],
    after_record: Dict[str, Any],
    before_state: ReviewStateIndex,
    after_state: ReviewStateIndex,
    step_dir: Path,
    field_assets: Mapping[str, Any],
) -> tuple[IterationReviewPackage, IterationReviewMetricsPayload]:
    before_metrics = dict(before_state.metrics or {})
    after_metrics = dict(after_state.metrics or {})
    physics_metrics = dict(field_assets.get("metrics", {}) or {})
    metric_cards, metric_deltas, raw_metrics_unregistered = _build_metric_cards(
        before_metrics,
        after_metrics,
        physics_metrics,
        metric_unit_contract=dict(
            field_assets.get("simulation_metric_unit_contract", {}) or {}
        ),
    )
    operator = _build_operator_info(
        after_record,
        metric_deltas,
        review_profile=profile_name,
    )
    physics = _build_physics_info(summary, after_record, field_assets)

    manifest_path = step_dir / "review_manifest.json"
    metrics_path = step_dir / "metrics.json"
    review_artifacts = _build_review_artifacts(
        step_dir=step_dir,
        manifest_path=manifest_path,
        metrics_path=metrics_path,
        before_record=before_record,
        after_record=after_record,
        field_assets=field_assets,
        profile_name=profile_name,
    )

    package_status = "lightweight_manifest"
    for key in ("temperature_field", "displacement_field", "stress_field", "geometry_overlay"):
        if review_artifacts[key].exists:
            package_status = "linked_field_assets"
            break

    package = IterationReviewPackage(
        review_profile=profile_name,
        package_status=package_status,
        run_id=str(summary.get("run_id", run_path.name) or run_path.name),
        run_dir=serialize_repo_path(run_path),
        package_dir=serialize_repo_path(step_dir),
        manifest_path=serialize_repo_path(manifest_path),
        step_index=int(step_index),
        sequence=after_state.sequence,
        iteration=after_state.iteration,
        attempt=after_state.attempt,
        stage=str(after_state.stage),
        before=before_state,
        after=after_state,
        operator=operator,
        physics=physics,
        metrics=metric_cards,
        metric_deltas=metric_deltas,
        raw_metrics_unregistered=raw_metrics_unregistered,
        review_artifacts=review_artifacts,
        notes=_package_notes(profile_name, field_assets, raw_metrics_unregistered),
    )
    metrics_payload = IterationReviewMetricsPayload(
        review_profile=profile_name,
        step_index=int(step_index),
        sequence=after_state.sequence,
        metrics=metric_cards,
        metric_deltas=metric_deltas,
        raw_metrics_unregistered=raw_metrics_unregistered,
    )
    return package, metrics_payload


def build_iteration_review_packages_from_run(
    run_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    review_profiles: Sequence[str] | None = None,
    field_case_dir: str | Path | None = None,
    field_case_map: str | Path | Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    run_path = Path(run_dir).resolve()
    summary_path = run_path / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.json not found in {run_path}")

    summary = load_json(summary_path)
    summary.setdefault("run_id", str(summary.get("run_id", run_path.name) or run_path.name))
    summary.setdefault("run_dir", serialize_repo_path(run_path))
    output_root = Path(output_dir).resolve() if output_dir else (run_path / "visualizations" / "review_packages").resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    records = list(load_layout_snapshot_records(run_path))
    normalized_profiles = _normalize_profiles(review_profiles)
    field_case_plan = _build_field_case_plan(
        run_path=run_path,
        records=records,
        field_case_dir=field_case_dir,
        field_case_map=field_case_map,
    )
    field_case_audit = dict(field_case_plan.get("audit", {}) or {})

    root_index: Dict[str, Any] = {
        "schema_version": "iteration_review_root_index/v1",
        "versions": REGISTRY_VERSIONS.model_dump(mode="json"),
        "run_id": str(summary.get("run_id", run_path.name) or run_path.name),
        "run_dir": serialize_repo_path(run_path),
        "output_root": serialize_repo_path(output_root),
        "package_dir_pattern": "steps/step_<step_index>_seq_<sequence>_<stage>/",
        "profiles": {},
        "notes": [
            "Step package builder writes manifest/index contracts first, renders local before/after layout views, builds teacher-demo step montages, and links existing field assets when available.",
            "Teacher-demo profile also builds lightweight timeline/keyframe montage sheets and a dataset overview when linked field cases exist.",
        ],
        "field_case_mapping": {
            "mapping_source": str(field_case_plan.get("mapping_source", "") or "none"),
            "dataset_root": str(field_case_plan.get("dataset_root", "") or ""),
            "default_case_dir": str(field_case_plan.get("default_case_dir", "") or ""),
            "mapped_step_count": int(field_case_audit.get("mapped_step_count", 0) or 0),
            "matched_step_count": int(field_case_audit.get("matched_step_count", 0) or 0),
            "defaulted_step_count": int(field_case_audit.get("defaulted_step_count", 0) or 0),
            "unmapped_step_count": int(field_case_audit.get("unmapped_step_count", 0) or 0),
            "expected_step_count": int(field_case_audit.get("expected_step_count", max(0, len(records) - 1)) or 0),
            "compatible_case_count": int(field_case_audit.get("compatible_case_count", 0) or 0),
            "incompatible_case_count": int(field_case_audit.get("incompatible_case_count", 0) or 0),
            "ambiguous_binding_count": int(field_case_audit.get("ambiguous_binding_count", 0) or 0),
            "matched_by_index_count": int(field_case_audit.get("matched_by_index_count", 0) or 0),
            "matched_by_sequence_count": int(field_case_audit.get("matched_by_sequence_count", 0) or 0),
            "matched_by_summary_case_order_count": int(field_case_audit.get("matched_by_summary_case_order_count", 0) or 0),
            "matched_by_case_order_count": int(field_case_audit.get("matched_by_case_order_count", 0) or 0),
            "defaulted_by_case_dir_count": int(field_case_audit.get("defaulted_by_case_dir_count", 0) or 0),
            "compatible_cases": list(field_case_audit.get("compatible_cases", []) or []),
            "incompatible_cases": list(field_case_audit.get("incompatible_cases", []) or []),
            "ambiguous_bindings": list(field_case_audit.get("ambiguous_bindings", []) or []),
            "step_resolutions": list(field_case_audit.get("step_resolutions", []) or []),
        },
    }

    for profile_name in normalized_profiles:
        contract = get_review_profile_contract(profile_name)
        profile_root = output_root / profile_name
        steps_root = profile_root / "steps"
        steps_root.mkdir(parents=True, exist_ok=True)
        field_case_gate = _build_profile_field_case_gate(
            profile_name=profile_name,
            contract=contract,
            field_case_mapping=dict(root_index.get("field_case_mapping", {}) or {}),
        )

        registry_snapshot_path = profile_root / "registry_snapshot.json"
        _write_json(registry_snapshot_path, build_registry_snapshot(profile_name))

        package_entries: list[Dict[str, Any]] = []
        step_manifest_paths: list[str] = []
        profile_notes = [
            str(item)
            for item in list(field_case_gate.get("notes", []) or [])
            if str(item).strip()
        ]
        if str(field_case_gate.get("enforcement_action", "") or "") != "skip_profile_packages":
            profile_notes = []
            for step_index, (before_record, after_record) in enumerate(zip(records[:-1], records[1:]), start=1):
                before_state = _build_state_index(before_record)
                after_state = _build_state_index(after_record)
                step_dir = steps_root / f"step_{step_index:04d}_seq_{after_state.sequence:04d}_{_slug_stage(after_state.stage)}"
                step_dir.mkdir(parents=True, exist_ok=True)
                step_binding = dict(dict(field_case_plan.get("resolved_steps", {}) or {}).get(int(step_index), {}) or {})
                if not step_binding:
                    step_binding = _resolve_field_case_binding_for_step(
                        step_index=step_index,
                        sequence=after_state.sequence,
                        stage=after_state.stage,
                        field_case_plan=field_case_plan,
                    )
                step_field_case_dir = str(step_binding.get("field_case_dir", "") or "")
                field_assets = _load_field_case_assets(step_field_case_dir)

                package, metrics_payload = _build_iteration_review_package(
                    run_path=run_path,
                    summary=summary,
                    profile_name=profile_name,
                    step_index=step_index,
                    before_record=before_record,
                    after_record=after_record,
                    before_state=before_state,
                    after_state=after_state,
                    step_dir=step_dir,
                    field_assets=field_assets,
                )

                manifest_path = step_dir / "review_manifest.json"
                metrics_path = step_dir / "metrics.json"
                _write_json(manifest_path, package.model_dump(mode="json"))
                _write_json(metrics_path, metrics_payload.model_dump(mode="json"))

                manifest_rel_path = serialize_repo_path(manifest_path)
                metrics_rel_path = serialize_repo_path(metrics_path)
                step_manifest_paths.append(manifest_rel_path)
                package_entries.append(
                    {
                        "step_index": int(package.step_index),
                        "sequence": int(package.sequence),
                        "stage": str(package.stage),
                        "manifest_path": manifest_rel_path,
                        "metrics_path": metrics_rel_path,
                        "package_status": str(package.package_status),
                        "primary_action": str(package.operator.primary_action),
                        "primary_action_family": str(package.operator.primary_action_family),
                        "primary_action_family_label": str(package.operator.primary_action_family_label),
                        "primary_action_label": str(package.operator.primary_action_label),
                        "semantic_caption_short": str(package.operator.semantic_caption_short),
                        "semantic_caption": str(package.operator.semantic_caption),
                        "target_summary": str(package.operator.target_summary),
                        "rule_summary": str(package.operator.rule_summary),
                        "expected_effect_summary": str(package.operator.expected_effect_summary),
                        "observed_effect_summary": str(package.operator.observed_effect_summary),
                        "action_family_sequence": list(package.operator.action_family_sequence or []),
                        "unmapped_actions": list(package.operator.unmapped_actions or []),
                        "family_contract_warnings": list(package.operator.family_contract_warnings or []),
                        "physics_profile": str(package.physics.physics_profile),
                        "field_case_dir": str(package.physics.field_case_dir or ""),
                        "field_case_status": str(step_binding.get("status", "") or ""),
                        "field_case_resolution_source": str(step_binding.get("resolution_source", "") or ""),
                        "triptych_path": str(package.review_artifacts.get("triptych").path if package.review_artifacts.get("triptych") else ""),
                        "step_montage_path": str(package.review_artifacts.get("step_montage").path if package.review_artifacts.get("step_montage") else ""),
                    }
                )

        operator_family_audit = _build_profile_operator_family_audit(package_entries)
        profile_index_payload = {
            "schema_version": "iteration_review_profile_index/v1",
            "review_profile": profile_name,
            "contract": contract.model_dump(mode="json"),
            "field_case_gate": field_case_gate,
            "run_id": root_index["run_id"],
            "run_dir": root_index["run_dir"],
            "output_root": serialize_repo_path(profile_root),
            "package_count": len(package_entries),
            "registry_snapshot_path": serialize_repo_path(registry_snapshot_path),
            "step_manifest_paths": step_manifest_paths,
            "packages": package_entries,
            "operator_family_audit": operator_family_audit,
            "field_case_mapping": dict(root_index.get("field_case_mapping", {}) or {}),
            "file_conventions": {
                "manifest": "review_manifest.json",
                "metrics": "metrics.json",
                "before_layout_view": "geometry_before.png",
                "after_layout_view": "geometry_after.png",
                "geometry_overlay": "geometry_overlay.png",
                "temperature_field": "temperature_field.png",
                "displacement_field": "displacement_field.png",
                "stress_field": "stress_field.png",
                "triptych": "triptych.png",
                "step_montage": "step_montage.png",
            },
            "aggregate_output_paths": {
                "timeline_montage": serialize_repo_path(profile_root / "montage" / "timeline_montage.png"),
                "keyframe_montage": serialize_repo_path(profile_root / "montage" / "keyframe_montage.png"),
                "dataset_overview": serialize_repo_path(profile_root / "dataset_overview" / "case_grid.png"),
            },
        }
        if profile_notes:
            profile_index_payload["notes"] = profile_notes
        profile_index_payload["aggregate_outputs"] = _build_profile_aggregate_outputs(
            profile_root=profile_root,
            profile_name=profile_name,
            package_entries=package_entries,
        )
        profile_index_path = profile_root / "package_index.json"
        _write_json(profile_index_path, profile_index_payload)

        root_index["profiles"][profile_name] = {
            "index_path": serialize_repo_path(profile_index_path),
            "registry_snapshot_path": serialize_repo_path(registry_snapshot_path),
            "package_count": len(package_entries),
            "operator_family_audit": operator_family_audit,
            "field_case_gate": field_case_gate,
        }
        if profile_notes:
            root_index["notes"].extend(profile_notes)

    root_index_path = output_root / "index.json"
    _write_json(root_index_path, root_index)

    review_result = {
        "schema_version": "iteration_review_build_result/v1",
        "run_id": root_index["run_id"],
        "run_dir": root_index["run_dir"],
        "output_root": root_index["output_root"],
        "index_path": serialize_repo_path(root_index_path),
        "profiles": dict(root_index.get("profiles", {}) or {}),
    }
    iteration_review_summary = build_iteration_review_summary_from_paths(
        root_index_path_text=str(review_result.get("index_path", "") or ""),
        teacher_demo_review_index_path=str(
            dict(dict(review_result.get("profiles", {}) or {}).get("teacher_demo", {}) or {}).get("index_path", "")
            or ""
        ),
        research_fast_review_index_path=str(
            dict(dict(review_result.get("profiles", {}) or {}).get("research_fast", {}) or {}).get("index_path", "")
            or ""
        ),
    )
    _persist_iteration_review_run_artifacts(
        run_path=run_path,
        review_result=review_result,
        iteration_review_summary=iteration_review_summary,
    )
    return review_result
