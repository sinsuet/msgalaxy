from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from geometry.catalog_geometry import resolve_catalog_component_specs
from geometry.geometry_proxy import build_geometry_proxy_manifest
from geometry.shell_spec import resolve_shell_spec

from .baseline import load_default_satellite_reference_baseline
from .contracts import SatelliteReferenceBaseline
from .runtime import resolve_satellite_bom_context


def _string_values(values: Any) -> list[str]:
    if isinstance(values, str):
        text = str(values).strip()
        return [text] if text else []
    if isinstance(values, Mapping):
        output: list[str] = []
        for value in dict(values).values():
            output.extend(_string_values(value))
        return output
    if isinstance(values, Iterable):
        output: list[str] = []
        for value in values:
            output.extend(_string_values(value))
        return output
    text = str(values or "").strip()
    return [text] if text else []


def _catalog_aperture_targets(spec: Any) -> list[str]:
    interfaces = dict(getattr(spec, "interfaces", {}) or {})
    targets: list[str] = []
    for key in ("aperture_alignment", "aperture_site", "aperture_id"):
        targets.extend(_string_values(interfaces.get(key)))
    targets.extend(_string_values(interfaces.get("aperture_bindings", [])))
    deduped: list[str] = []
    for target in targets:
        if target not in deduped:
            deduped.append(target)
    return deduped


def build_satellite_geometry_integration_report(
    *,
    bom_file: str | Path | None,
    design_state: Any,
    baseline: Optional[SatelliteReferenceBaseline] = None,
) -> Dict[str, Any]:
    baseline_obj = baseline or load_default_satellite_reference_baseline()
    context = resolve_satellite_bom_context(
        None if bom_file is None else str(bom_file),
        baseline=baseline_obj,
    )
    archetype = baseline_obj.get_archetype(str(context.get("archetype_id", "") or ""))
    shell_spec = resolve_shell_spec(design_state)
    catalog_specs = resolve_catalog_component_specs(design_state)
    geometry_proxy_manifest = build_geometry_proxy_manifest(design_state)

    shell_variant = ""
    if shell_spec is not None:
        shell_variant = str(dict(getattr(shell_spec, "metadata", {}) or {}).get("shell_variant", "") or "")
    if not shell_variant:
        shell_variant = str(dict(getattr(design_state, "metadata", {}) or {}).get("shell_variant", "") or "")

    allowed_shell_variants = (
        []
        if archetype is None
        else [str(item) for item in list(archetype.morphology.allowed_shell_variants or [])]
    )
    shell_variant_allowed = bool(shell_variant) and shell_variant in allowed_shell_variants

    aperture_index: Dict[str, Any] = {}
    panel_faces: list[str] = []
    if shell_spec is not None:
        aperture_index = {
            str(aperture.aperture_id): aperture
            for aperture in list(getattr(shell_spec, "aperture_sites", []) or [])
        }
        panel_faces = [
            str(panel.face)
            for panel in list(shell_spec.resolved_panels() or [])
        ]
    panel_by_id = {} if shell_spec is None else shell_spec.panel_index()

    aperture_bindings: list[Dict[str, Any]] = []
    matched_catalog_components: list[str] = []
    for component_id, spec in catalog_specs.items():
        targets = _catalog_aperture_targets(spec)
        if not targets:
            continue
        family = str(getattr(spec, "family", "") or "")
        for target in targets:
            aperture = aperture_index.get(target)
            allowed_families = []
            panel_face = ""
            if aperture is not None:
                allowed_families = [
                    str(item)
                    for item in list(getattr(aperture, "allowed_component_families", []) or [])
                ]
                panel = panel_by_id.get(str(getattr(aperture, "panel_id", "") or ""))
                panel_face = "" if panel is None else str(getattr(panel, "face", "") or "")
            family_allowed = not allowed_families or family in allowed_families
            binding_ok = aperture is not None and family_allowed
            aperture_bindings.append(
                {
                    "component_id": str(component_id),
                    "catalog_id": str(getattr(spec, "catalog_id", "") or ""),
                    "family": family,
                    "aperture_id": str(target),
                    "aperture_exists": aperture is not None,
                    "family_allowed": bool(family_allowed),
                    "binding_ok": bool(binding_ok),
                    "panel_face": panel_face,
                }
            )
            if binding_ok and component_id not in matched_catalog_components:
                matched_catalog_components.append(str(component_id))

    proxy_entries = list(dict(geometry_proxy_manifest or {}).get("entries", []) or [])
    proxy_role_counts: Dict[str, int] = {}
    for entry in proxy_entries:
        role = str(dict(entry or {}).get("role", "") or "")
        if not role:
            continue
        proxy_role_counts[role] = int(proxy_role_counts.get(role, 0) or 0) + 1

    required_task_faces = []
    if archetype is not None:
        required_task_faces = [
            {"semantic": str(item.semantic), "face_id": str(item.face_id)}
            for item in list(archetype.morphology.required_task_faces() or [])
        ]

    return {
        "enabled": bool(archetype is not None and shell_spec is not None),
        "task_type": str(context.get("task_type", "") or ""),
        "archetype_id": str(context.get("archetype_id", "") or ""),
        "mission_class": str(context.get("mission_class", "") or ""),
        "default_rule_profile": str(context.get("default_rule_profile", "") or ""),
        "allowed_shell_variants": allowed_shell_variants,
        "required_task_faces": required_task_faces,
        "shell_kind": ""
        if shell_spec is None
        else str(shell_spec.outer_profile.normalized_kind() or ""),
        "shell_variant": shell_variant,
        "shell_variant_allowed": bool(shell_variant_allowed),
        "panel_faces": panel_faces,
        "aperture_count": len(aperture_index),
        "aperture_bindings": aperture_bindings,
        "matched_catalog_components": matched_catalog_components,
        "geometry_proxy_manifest_version": str(
            dict(geometry_proxy_manifest or {}).get("manifest_version", "") or ""
        ),
        "component_proxy_count": int(proxy_role_counts.get("component_proxy", 0) or 0),
        "aperture_proxy_count": int(proxy_role_counts.get("aperture_proxy", 0) or 0),
        "shell_proxy_count": int(proxy_role_counts.get("shell_proxy", 0) or 0),
    }
