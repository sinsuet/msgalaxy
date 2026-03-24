from __future__ import annotations

import math
from typing import Any, Dict, Optional

from core.logger import get_logger

logger = get_logger(__name__)


class ComsolThermalOperatorMixin:
    """Heat-source binding and thermal operator execution helpers."""

    @staticmethod
    def _shell_contact_mount_face_index(design_state) -> Dict[str, str]:
        metadata = dict(getattr(design_state, "metadata", {}) or {})
        placements = list(metadata.get("placement_state", []) or [])
        mapping: Dict[str, str] = {}
        for item in placements:
            if not isinstance(item, dict):
                continue
            comp_id = str(item.get("instance_id", "") or "").strip()
            if not comp_id:
                continue
            placement_meta = dict(item.get("metadata", {}) or {})
            shell_contact_required = bool(placement_meta.get("shell_contact_required", False))
            if not shell_contact_required:
                continue
            mapping[comp_id] = str(item.get("mount_face", "") or "").strip().upper()
        return mapping

    @staticmethod
    def _default_mount_contact_conductance(comp: Any) -> float:
        override = getattr(comp, "shell_mount_conductance", None)
        try:
            if override is not None and float(override) > 0.0:
                return float(override)
        except Exception:
            pass
        category = str(getattr(comp, "category", "") or "").strip().lower()
        baseline = {
            "payload": 140.0,
            "avionics": 125.0,
            "adcs": 115.0,
            "battery": 170.0,
            "communication": 95.0,
        }
        return float(baseline.get(category, 110.0))

    def _remove_selection_if_exists(self, selection_tag: str) -> None:
        if self.model is None:
            return
        tag = str(selection_tag or "").strip()
        if not tag:
            return
        try:
            self.model.java.selection().remove(tag)
        except Exception:
            pass

    def _create_explicit_domain_selection(
        self,
        *,
        selection_tag: str,
        domain_ids: list[int],
    ) -> str:
        if self.model is None:
            raise RuntimeError("model_unavailable")
        tag = str(selection_tag or "").strip()
        if not tag:
            raise ValueError("empty_selection_tag")
        normalized = [int(value) for value in list(domain_ids or []) if int(value) > 0]
        if not normalized:
            raise ValueError("empty_domain_ids")
        self._remove_selection_if_exists(tag)
        explicit_sel = self.model.java.selection().create(tag, "Explicit")
        try:
            explicit_sel.geom("geom1", 3)
        except Exception:
            pass
        explicit_sel.set(normalized)
        return tag

    def _create_explicit_boundary_selection(
        self,
        *,
        selection_tag: str,
        boundary_ids: list[int],
    ) -> str:
        if self.model is None:
            raise RuntimeError("model_unavailable")
        tag = str(selection_tag or "").strip()
        if not tag:
            raise ValueError("empty_selection_tag")
        normalized = [int(value) for value in list(boundary_ids or []) if int(value) > 0]
        if not normalized:
            raise ValueError("empty_boundary_ids")
        self._remove_selection_if_exists(tag)
        explicit_sel = self.model.java.selection().create(tag, "Explicit")
        try:
            explicit_sel.geom("geom1", 2)
        except Exception:
            pass
        explicit_sel.set(normalized)
        return tag

    def _geometry_is_assembly(self) -> bool:
        if self.model is None:
            return False
        try:
            return bool(self.model.java.geom("geom1").isAssembly())
        except Exception:
            return False

    def _boundary_domain_adjacency(self) -> Dict[int, list[int]]:
        if self.model is None:
            return {}
        try:
            up_down = self.model.java.geom("geom1").getUpDown()
        except Exception:
            return {}
        try:
            upper = [int(value) for value in list(up_down[0])]
            lower = [int(value) for value in list(up_down[1])]
        except Exception:
            return {}

        mapping: Dict[int, list[int]] = {}
        for boundary_id, (up_value, down_value) in enumerate(zip(upper, lower), start=1):
            domains = []
            for domain_id in (int(up_value), int(down_value)):
                if domain_id <= 0 or domain_id in domains:
                    continue
                domains.append(int(domain_id))
            mapping[int(boundary_id)] = domains
        return mapping

    def _resolve_component_domain_index(self, design_state: Any) -> Dict[str, list[int]]:
        domain_index: Dict[str, list[int]] = {}
        heat_report = dict(getattr(self, "_last_heat_binding_report", {}) or {})
        for item in list(heat_report.get("component_domain_bindings", []) or []):
            if not isinstance(item, dict):
                continue
            comp_id = str(item.get("component_id", "") or "").strip()
            domain_ids = [int(value) for value in list(item.get("domain_ids", []) or []) if int(value) > 0]
            if comp_id and domain_ids:
                domain_index[comp_id] = domain_ids

        for index, comp in enumerate(list(getattr(design_state, "components", []) or [])):
            comp_id = str(getattr(comp, "id", "") or "").strip()
            if not comp_id or comp_id in domain_index:
                continue
            binding = self._resolve_component_domain_binding(
                comp=comp,
                comp_index=index,
                selection_tag=f"boxsel_comp_contact_domain_{index}",
            )
            domain_ids = [int(value) for value in list(binding.get("domain_ids", []) or []) if int(value) > 0]
            if domain_ids:
                domain_index[comp_id] = domain_ids
        return domain_index

    def _infer_shell_domain_ids(
        self,
        *,
        component_domain_index: Dict[str, list[int]],
    ) -> list[int]:
        all_domains = set(self._list_domain_ids())
        component_domains = {
            int(domain_id)
            for domain_ids in list(component_domain_index.values())
            for domain_id in list(domain_ids or [])
            if int(domain_id) > 0
        }
        return sorted(int(domain_id) for domain_id in all_domains - component_domains if int(domain_id) > 0)

    @staticmethod
    def _filter_shared_interface_boundary_ids(
        *,
        shell_boundary_ids: list[int],
        component_boundary_ids: list[int],
        shell_domain_ids: list[int],
        component_domain_ids: list[int],
        boundary_adjacency: Dict[int, list[int]],
        geometry_is_assembly: bool,
    ) -> Dict[str, Any]:
        shell_ids = sorted({int(value) for value in list(shell_boundary_ids or []) if int(value) > 0})
        component_ids = sorted({int(value) for value in list(component_boundary_ids or []) if int(value) > 0})
        shell_domains = {int(value) for value in list(shell_domain_ids or []) if int(value) > 0}
        component_domains = {int(value) for value in list(component_domain_ids or []) if int(value) > 0}

        overlap_ids = sorted(set(shell_ids) & set(component_ids))
        adjacency_ids: list[int] = []
        if not geometry_is_assembly and shell_domains and component_domains:
            for boundary_id in sorted(set(shell_ids) | set(component_ids)):
                adjacent_domains = {
                    int(value)
                    for value in list(boundary_adjacency.get(int(boundary_id), []) or [])
                    if int(value) > 0
                }
                if not adjacent_domains:
                    continue
                if adjacent_domains.isdisjoint(shell_domains):
                    continue
                if adjacent_domains.isdisjoint(component_domains):
                    continue
                adjacency_ids.append(int(boundary_id))

        effective_ids = sorted(set(overlap_ids) & set(adjacency_ids))
        if not effective_ids:
            effective_ids = list(adjacency_ids)

        return {
            "shell_boundary_ids": list(shell_ids),
            "component_boundary_ids": list(component_ids),
            "overlap_boundary_ids": list(overlap_ids),
            "adjacency_boundary_ids": list(adjacency_ids),
            "effective_boundary_ids": list(effective_ids),
            "shell_domain_ids": sorted(shell_domains),
            "component_domain_ids": sorted(component_domains),
            "geometry_is_assembly": bool(geometry_is_assembly),
        }

    def _resolve_component_domain_binding(
        self,
        *,
        comp: Any,
        comp_index: int,
        selection_tag: str,
    ) -> Dict[str, Any]:
        selection_name = str(selection_tag or f"boxsel_comp_{int(comp_index)}").strip()
        if self.model is None:
            return {
                "component_id": str(getattr(comp, "id", "") or ""),
                "selection_name": selection_name,
                "selection_ready": False,
                "bindable_single_domain": False,
                "selection_status": "model_unavailable",
                "selection_condition": "",
                "domain_ids": [],
                "domain_count": 0,
                "ambiguous_domain_ids": [],
                "resolution_method": "",
                "distance_mm": None,
                "size_error": None,
                "box_bounds_mm": {},
            }

        pos = comp.position
        dim = comp.dimensions
        tolerance = 1e-3
        x_min = float(pos.x - dim.x / 2.0 - tolerance)
        x_max = float(pos.x + dim.x / 2.0 + tolerance)
        y_min = float(pos.y - dim.y / 2.0 - tolerance)
        y_max = float(pos.y + dim.y / 2.0 + tolerance)
        z_min = float(pos.z - dim.z / 2.0 - tolerance)
        z_max = float(pos.z + dim.z / 2.0 + tolerance)
        box_bounds = {
            "xmin": float(x_min),
            "xmax": float(x_max),
            "ymin": float(y_min),
            "ymax": float(y_max),
            "zmin": float(z_min),
            "zmax": float(z_max),
        }

        for tag in (
            selection_name,
            f"{selection_name}_recovered",
            f"{selection_name}_resolved",
        ):
            self._remove_selection_if_exists(tag)

        resolve_meta: Dict[str, Any] = {}
        condition_used = "inside"
        selection_status = "missing_domain"
        selected_entities: list[int] = []
        ambiguous_candidates: list[int] = []

        try:
            box_sel = self.model.java.selection().create(selection_name, "Box")
            box_sel.set("entitydim", "3")
            box_sel.set("xmin", f"{x_min}[mm]")
            box_sel.set("xmax", f"{x_max}[mm]")
            box_sel.set("ymin", f"{y_min}[mm]")
            box_sel.set("ymax", f"{y_max}[mm]")
            box_sel.set("zmin", f"{z_min}[mm]")
            box_sel.set("zmax", f"{z_max}[mm]")
            box_sel.set("condition", "inside")

            selected_entities = self._normalize_entity_ids(box_sel.entities())
            if len(selected_entities) == 1:
                selection_status = "inside_box_exact"
            if not selected_entities:
                box_sel.set("condition", "intersects")
                condition_used = "intersects"
                selected_entities = self._normalize_entity_ids(box_sel.entities())
                if len(selected_entities) == 1:
                    selection_status = "intersects_box_exact"

            if not selected_entities:
                resolved_domain, resolve_meta = self._resolve_missing_heat_domain(
                    comp=comp,
                    comp_index=comp_index,
                )
                if resolved_domain is not None:
                    selection_name = self._create_explicit_domain_selection(
                        selection_tag=f"{selection_name}_recovered",
                        domain_ids=[int(resolved_domain)],
                    )
                    selected_entities = [int(resolved_domain)]
                    condition_used = "explicit_recovered"
                    selection_status = "recovered_missing_domain"

            if len(selected_entities) > 1:
                ambiguous_candidates = list(selected_entities)
                box_sel.set("condition", "allvertices")
                condition_used = "allvertices"
                selected_entities = self._normalize_entity_ids(box_sel.entities())
                if len(selected_entities) == 1:
                    selection_status = "allvertices_box_exact"
                elif not selected_entities and ambiguous_candidates:
                    selected_entities = list(ambiguous_candidates)
                    condition_used = "intersects_fallback"

            if len(selected_entities) > 1:
                ambiguous_candidates = list(selected_entities)
                resolved_domain, resolve_meta = self._resolve_ambiguous_heat_domain(
                    comp=comp,
                    comp_index=comp_index,
                    domain_ids=selected_entities,
                )
                if resolved_domain is not None:
                    selection_name = self._create_explicit_domain_selection(
                        selection_tag=f"{selection_name}_resolved",
                        domain_ids=[int(resolved_domain)],
                    )
                    selected_entities = [int(resolved_domain)]
                    condition_used = "explicit_resolved"
                    selection_status = "resolved_ambiguous_domain"
                else:
                    selection_status = "ambiguous_multi_domain"
        except Exception as exc:
            return {
                "component_id": str(getattr(comp, "id", "") or ""),
                "selection_name": selection_name,
                "selection_ready": False,
                "bindable_single_domain": False,
                "selection_status": "selection_runtime_error",
                "selection_condition": condition_used,
                "domain_ids": [],
                "domain_count": 0,
                "ambiguous_domain_ids": list(ambiguous_candidates),
                "resolution_method": "",
                "distance_mm": None,
                "size_error": None,
                "box_bounds_mm": dict(box_bounds),
                "error": str(exc),
            }

        if len(selected_entities) == 1 and selection_status not in {
            "recovered_missing_domain",
            "resolved_ambiguous_domain",
            "inside_box_exact",
            "intersects_box_exact",
            "allvertices_box_exact",
        }:
            selection_status = "single_domain_exact"
        if not selected_entities and selection_status != "selection_runtime_error":
            selection_status = "missing_domain"

        return {
            "component_id": str(getattr(comp, "id", "") or ""),
            "selection_name": selection_name,
            "selection_ready": bool(selected_entities),
            "bindable_single_domain": len(selected_entities) == 1,
            "selection_status": str(selection_status),
            "selection_condition": str(condition_used),
            "domain_ids": [int(value) for value in list(selected_entities or [])],
            "domain_count": int(len(selected_entities)),
            "ambiguous_domain_ids": [int(value) for value in list(ambiguous_candidates or [])],
            "resolution_method": str(resolve_meta.get("method", "") or ""),
            "distance_mm": (
                float(resolve_meta["distance_mm"])
                if resolve_meta.get("distance_mm") is not None
                else None
            ),
            "size_error": (
                float(resolve_meta["size_error"])
                if resolve_meta.get("size_error") is not None
                else None
            ),
            "box_bounds_mm": dict(box_bounds),
        }

    def _create_component_face_box_selection(
        self,
        comp: Any,
        sel_name: str,
        *,
        face: str,
        band_mm: float = 1.0,
    ) -> None:
        pos = comp.position
        dim = comp.dimensions
        face_name = str(face or "").strip().upper()
        tol = max(float(band_mm), 0.5)
        x_min = float(pos.x - dim.x / 2.0) - tol
        x_max = float(pos.x + dim.x / 2.0) + tol
        y_min = float(pos.y - dim.y / 2.0) - tol
        y_max = float(pos.y + dim.y / 2.0) + tol
        z_min = float(pos.z - dim.z / 2.0) - tol
        z_max = float(pos.z + dim.z / 2.0) + tol

        if face_name == "+X":
            x_min = float(pos.x + dim.x / 2.0) - tol
            x_max = float(pos.x + dim.x / 2.0) + tol
        elif face_name == "-X":
            x_min = float(pos.x - dim.x / 2.0) - tol
            x_max = float(pos.x - dim.x / 2.0) + tol
        elif face_name == "+Y":
            y_min = float(pos.y + dim.y / 2.0) - tol
            y_max = float(pos.y + dim.y / 2.0) + tol
        elif face_name == "-Y":
            y_min = float(pos.y - dim.y / 2.0) - tol
            y_max = float(pos.y - dim.y / 2.0) + tol
        elif face_name == "+Z":
            z_min = float(pos.z + dim.z / 2.0) - tol
            z_max = float(pos.z + dim.z / 2.0) + tol
        elif face_name == "-Z":
            z_min = float(pos.z - dim.z / 2.0) - tol
            z_max = float(pos.z - dim.z / 2.0) + tol

        box_sel = self.model.java.selection().create(sel_name, "Box")
        box_sel.set("entitydim", "2")
        box_sel.set("condition", "intersects")
        box_sel.set("xmin", f"{x_min}[mm]")
        box_sel.set("xmax", f"{x_max}[mm]")
        box_sel.set("ymin", f"{y_min}[mm]")
        box_sel.set("ymax", f"{y_max}[mm]")
        box_sel.set("zmin", f"{z_min}[mm]")
        box_sel.set("zmax", f"{z_max}[mm]")

    def _select_shell_mount_face_boundaries(
        self,
        *,
        design_state: Any,
        mount_face: str,
        selection_tag: str,
    ) -> list[int]:
        if self.model is None:
            return []
        shell = self._resolve_shell_geometry(design_state)
        if not shell:
            return []

        outer_x = float(shell["outer_x"])
        outer_y = float(shell["outer_y"])
        outer_z = float(shell["outer_z"])
        thickness = float(shell["thickness"])
        inner_x = max(outer_x - 2.0 * thickness, 1e-6)
        inner_y = max(outer_y - 2.0 * thickness, 1e-6)
        inner_z = max(outer_z - 2.0 * thickness, 1e-6)
        half_outer_x = outer_x / 2.0
        half_outer_y = outer_y / 2.0
        half_outer_z = outer_z / 2.0
        half_inner_x = inner_x / 2.0
        half_inner_y = inner_y / 2.0
        half_inner_z = inner_z / 2.0
        tol = max(min(thickness * 0.35, 2.0), 0.5)
        face = str(mount_face or "").strip().upper()

        try:
            self.model.java.selection().remove(selection_tag)
        except Exception:
            pass
        box_sel = self.model.java.selection().create(selection_tag, "Box")
        box_sel.set("entitydim", "2")
        box_sel.set("condition", "intersects")

        if face == "+X":
            box_sel.set("xmin", f"{half_inner_x - tol}[mm]")
            box_sel.set("xmax", f"{half_inner_x + tol}[mm]")
            box_sel.set("ymin", f"{-half_outer_y - 1.0}[mm]")
            box_sel.set("ymax", f"{half_outer_y + 1.0}[mm]")
            box_sel.set("zmin", f"{-half_outer_z - 1.0}[mm]")
            box_sel.set("zmax", f"{half_outer_z + 1.0}[mm]")
        elif face == "-X":
            box_sel.set("xmin", f"{-half_inner_x - tol}[mm]")
            box_sel.set("xmax", f"{-half_inner_x + tol}[mm]")
            box_sel.set("ymin", f"{-half_outer_y - 1.0}[mm]")
            box_sel.set("ymax", f"{half_outer_y + 1.0}[mm]")
            box_sel.set("zmin", f"{-half_outer_z - 1.0}[mm]")
            box_sel.set("zmax", f"{half_outer_z + 1.0}[mm]")
        elif face == "+Y":
            box_sel.set("xmin", f"{-half_outer_x - 1.0}[mm]")
            box_sel.set("xmax", f"{half_outer_x + 1.0}[mm]")
            box_sel.set("ymin", f"{half_inner_y - tol}[mm]")
            box_sel.set("ymax", f"{half_inner_y + tol}[mm]")
            box_sel.set("zmin", f"{-half_outer_z - 1.0}[mm]")
            box_sel.set("zmax", f"{half_outer_z + 1.0}[mm]")
        elif face == "-Y":
            box_sel.set("xmin", f"{-half_outer_x - 1.0}[mm]")
            box_sel.set("xmax", f"{half_outer_x + 1.0}[mm]")
            box_sel.set("ymin", f"{-half_inner_y - tol}[mm]")
            box_sel.set("ymax", f"{-half_inner_y + tol}[mm]")
            box_sel.set("zmin", f"{-half_outer_z - 1.0}[mm]")
            box_sel.set("zmax", f"{half_outer_z + 1.0}[mm]")
        elif face == "+Z":
            box_sel.set("xmin", f"{-half_outer_x - 1.0}[mm]")
            box_sel.set("xmax", f"{half_outer_x + 1.0}[mm]")
            box_sel.set("ymin", f"{-half_outer_y - 1.0}[mm]")
            box_sel.set("ymax", f"{half_outer_y + 1.0}[mm]")
            box_sel.set("zmin", f"{half_inner_z - tol}[mm]")
            box_sel.set("zmax", f"{half_inner_z + tol}[mm]")
        elif face == "-Z":
            box_sel.set("xmin", f"{-half_outer_x - 1.0}[mm]")
            box_sel.set("xmax", f"{half_outer_x + 1.0}[mm]")
            box_sel.set("ymin", f"{-half_outer_y - 1.0}[mm]")
            box_sel.set("ymax", f"{half_outer_y + 1.0}[mm]")
            box_sel.set("zmin", f"{-half_inner_z - tol}[mm]")
            box_sel.set("zmax", f"{-half_inner_z + tol}[mm]")
        else:
            return []

        return self._normalize_entity_ids(box_sel.entities())

    def _apply_default_shell_mount_contact(
        self,
        *,
        design_state: Any,
        ht: Any,
        comp: Any,
        comp_index: int,
        mount_face: str,
        component_domain_index: Optional[Dict[str, list[int]]] = None,
        shell_domain_ids: Optional[list[int]] = None,
        boundary_adjacency: Optional[Dict[int, list[int]]] = None,
        geometry_is_assembly: Optional[bool] = None,
        shell_contact_report: Optional[Dict[str, Any]] = None,
    ) -> bool:
        comp_id = str(getattr(comp, "id", "") or "").strip()
        face = str(mount_face or "").strip().upper()
        entry: Dict[str, Any] = {
            "component_id": comp_id,
            "mount_face": face,
            "selection_status": "",
            "geometry_is_assembly": bool(geometry_is_assembly) if geometry_is_assembly is not None else False,
            "shell_domain_ids": [int(value) for value in list(shell_domain_ids or []) if int(value) > 0],
            "component_domain_ids": [
                int(value)
                for value in list((component_domain_index or {}).get(comp_id, []) or [])
                if int(value) > 0
            ],
            "shell_boundary_ids": [],
            "component_boundary_ids": [],
            "overlap_boundary_ids": [],
            "adjacency_boundary_ids": [],
            "effective_boundary_ids": [],
            "conductance_w_m2k": None,
            "applied": False,
        }
        if face not in {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}:
            entry["selection_status"] = "invalid_mount_face"
            if isinstance(shell_contact_report, dict):
                shell_contact_report.setdefault("components", []).append(dict(entry))
            return False

        shell_sel_name = f"boxsel_tc_shell_face_{comp_index}"
        comp_sel_name = f"boxsel_tc_shell_comp_{comp_index}"
        shell_entities = self._select_shell_mount_face_boundaries(
            design_state=design_state,
            mount_face=face,
            selection_tag=shell_sel_name,
        )
        entry["shell_boundary_ids"] = [int(value) for value in list(shell_entities or [])]
        if not shell_entities:
            logger.warning("      ⚠ 默认 shell mount contact 缺少 shell 边界: %s", comp.id)
            entry["selection_status"] = "shell_selection_empty"
            if isinstance(shell_contact_report, dict):
                shell_contact_report.setdefault("components", []).append(dict(entry))
            return False

        try:
            self._create_component_face_box_selection(comp, comp_sel_name, face=face, band_mm=1.0)
            comp_entities = self._normalize_entity_ids(self.model.java.selection(comp_sel_name).entities())
        except Exception as exc:
            logger.warning("      ⚠ 默认 shell mount contact 组件边界选择失败: %s (%s)", comp.id, exc)
            entry["selection_status"] = "component_selection_runtime_error"
            entry["error"] = str(exc)
            if isinstance(shell_contact_report, dict):
                shell_contact_report.setdefault("components", []).append(dict(entry))
            return False
        entry["component_boundary_ids"] = [int(value) for value in list(comp_entities or [])]
        if not comp_entities:
            logger.warning("      ⚠ 默认 shell mount contact 缺少组件边界: %s", comp.id)
            entry["selection_status"] = "component_selection_empty"
            if isinstance(shell_contact_report, dict):
                shell_contact_report.setdefault("components", []).append(dict(entry))
            return False

        if geometry_is_assembly is None:
            geometry_is_assembly = self._geometry_is_assembly()
        if boundary_adjacency is None:
            boundary_adjacency = self._boundary_domain_adjacency()
        if component_domain_index is None:
            component_domain_index = self._resolve_component_domain_index(design_state)
        if shell_domain_ids is None:
            shell_domain_ids = self._infer_shell_domain_ids(
                component_domain_index=component_domain_index,
            )

        filtered = self._filter_shared_interface_boundary_ids(
            shell_boundary_ids=shell_entities,
            component_boundary_ids=comp_entities,
            shell_domain_ids=list(shell_domain_ids or []),
            component_domain_ids=list(component_domain_index.get(comp_id, []) or []),
            boundary_adjacency=dict(boundary_adjacency or {}),
            geometry_is_assembly=bool(geometry_is_assembly),
        )
        entry.update(filtered)
        entry["geometry_is_assembly"] = bool(geometry_is_assembly)

        effective_entities = [int(value) for value in list(filtered.get("effective_boundary_ids", []) or []) if int(value) > 0]
        if bool(geometry_is_assembly):
            logger.warning(
                "      ⚠ 默认 shell mount contact 当前仅支持 Union 共享边界，assembly pair 模式未接入: %s",
                comp.id,
            )
            entry["selection_status"] = "assembly_pair_required"
            if isinstance(shell_contact_report, dict):
                shell_contact_report.setdefault("components", []).append(dict(entry))
            return False
        if not entry["component_domain_ids"]:
            logger.warning("      ⚠ 默认 shell mount contact 缺少组件域解析: %s", comp.id)
            entry["selection_status"] = "component_domain_unresolved"
            if isinstance(shell_contact_report, dict):
                shell_contact_report.setdefault("components", []).append(dict(entry))
            return False
        if not entry["shell_domain_ids"]:
            logger.warning("      ⚠ 默认 shell mount contact 缺少 shell 域解析: %s", comp.id)
            entry["selection_status"] = "shell_domain_unresolved"
            if isinstance(shell_contact_report, dict):
                shell_contact_report.setdefault("components", []).append(dict(entry))
            return False
        if not effective_entities:
            logger.warning(
                "      ⚠ 默认 shell mount contact 未找到真实共享界面: %s face=%s overlap=%d adjacency=%d",
                comp.id,
                face,
                len(list(entry.get("overlap_boundary_ids", []) or [])),
                len(list(entry.get("adjacency_boundary_ids", []) or [])),
            )
            entry["selection_status"] = "no_shared_interface"
            if isinstance(shell_contact_report, dict):
                shell_contact_report.setdefault("components", []).append(dict(entry))
            return False

        try:
            tc_name = f"tc_shell_{comp_index}"
            thermal_contact = ht.feature().create(tc_name, "ThermalContact")
            conductance = self._default_mount_contact_conductance(comp)
            entry["conductance_w_m2k"] = float(conductance)
            set_ok, set_desc, attempt_errors = self._set_thermal_contact_conductance(
                thermal_contact,
                conductance,
            )
            if not set_ok:
                raise ValueError(" | ".join(attempt_errors[-4:]))
            contact_sel_name = self._create_explicit_boundary_selection(
                selection_tag=f"sel_tc_shell_iface_{comp_index}",
                boundary_ids=effective_entities,
            )
            thermal_contact.selection().named(contact_sel_name)
            entry["applied"] = True
            entry["selection_status"] = "shared_interface_applied"
            logger.info(
                "      ✓ 默认 shell mount contact 已设置: %s face=%s, h=%.1f (%s), shared_boundaries=%d",
                comp.id,
                face,
                conductance,
                set_desc,
                len(effective_entities),
            )
            if isinstance(shell_contact_report, dict):
                shell_contact_report.setdefault("components", []).append(dict(entry))
            return True
        except Exception as exc:
            logger.warning("      ⚠ 默认 shell mount contact 设置失败: %s (%s)", comp.id, exc)
            entry["selection_status"] = "contact_feature_failed"
            entry["error"] = str(exc)
            if isinstance(shell_contact_report, dict):
                shell_contact_report.setdefault("components", []).append(dict(entry))
            return False

    def _assign_heat_sources_dynamic(
        self,
        design_state,
        ht: Any,
        geom: Any,
    ) -> Dict[str, Any]:
        """
        Use Box Selection to bind per-component heat sources.

        Returns:
            Binding report for upstream validity checks.
        """
        _ = geom
        total_heat_sources_assigned = 0
        ambiguous_heat_sources = []
        disambiguated_heat_sources = []
        failed_heat_sources = []
        active_heat_components = 0
        component_domain_bindings = []

        for i, comp in enumerate(design_state.components):
            if comp.power <= 0:
                continue
            active_heat_components += 1

            logger.info(f"    - 为组件 {comp.id} 创建热源 ({comp.power}W)")

            binding = self._resolve_component_domain_binding(
                comp=comp,
                comp_index=i,
                selection_tag=f"boxsel_comp_{i}",
            )
            binding["power_w"] = float(comp.power)
            binding["heat_source_assigned"] = False
            component_domain_bindings.append(binding)

            selected_entities = list(binding.get("domain_ids", []) or [])
            num_selected = int(binding.get("domain_count", len(selected_entities)))
            logger.info(
                "      Box Selection 解析结果: %d 个域 (%s)",
                num_selected,
                str(binding.get("selection_status", "") or "unknown"),
            )

            selection_status = str(binding.get("selection_status", "") or "")
            if selection_status == "recovered_missing_domain":
                disambiguated_heat_sources.append(comp.id)
                logger.warning(
                    "      ⚠️ 0-hit 已自动恢复: %s -> domain %s (method=%s, distance_mm=%s, size_error=%s)",
                    comp.id,
                    ",".join(str(item) for item in list(selected_entities or [])),
                    binding.get("resolution_method"),
                    binding.get("distance_mm"),
                    binding.get("size_error"),
                )
            elif selection_status == "resolved_ambiguous_domain":
                disambiguated_heat_sources.append(comp.id)
                logger.warning(
                    "      ⚠️ 多域歧义已自动收敛: %s -> domain %s (method=%s, distance_mm=%s)",
                    comp.id,
                    ",".join(str(item) for item in list(selected_entities or [])),
                    binding.get("resolution_method"),
                    binding.get("distance_mm"),
                )
            elif selection_status == "ambiguous_multi_domain":
                logger.error(
                    "      ✗ 热源绑定拒绝: 组件 %s 仍歧义命中 %d 个域，跳过该热源",
                    comp.id,
                    num_selected,
                )
                ambiguous_heat_sources.append(comp.id)
                failed_heat_sources.append(comp.id)
                continue
            elif not bool(binding.get("selection_ready", False)):
                box_bounds = dict(binding.get("box_bounds_mm", {}) or {})
                logger.warning(f"      ⚠️ 严重警告: 热源 Box Selection 失败！组件 {comp.id} 未选中任何域！")
                logger.warning(
                    "      Box 范围: X[%.1f, %.1f], Y[%.1f, %.1f], Z[%.1f, %.1f] mm",
                    float(box_bounds.get("xmin", 0.0)),
                    float(box_bounds.get("xmax", 0.0)),
                    float(box_bounds.get("ymin", 0.0)),
                    float(box_bounds.get("ymax", 0.0)),
                    float(box_bounds.get("zmin", 0.0)),
                    float(box_bounds.get("zmax", 0.0)),
                )
                logger.warning(
                    "      组件位置: [%.1f, %.1f, %.1f] mm",
                    float(comp.position.x),
                    float(comp.position.y),
                    float(comp.position.z),
                )
                logger.warning(
                    "      组件尺寸: [%.1f, %.1f, %.1f] mm",
                    float(comp.dimensions.x),
                    float(comp.dimensions.y),
                    float(comp.dimensions.z),
                )
                logger.error(f"      ✗ 热源绑定彻底失败！{comp.power}W 热源未施加到组件 {comp.id}！")
                failed_heat_sources.append(comp.id)
                continue

            hs_name = f"hs_{i}"
            heat_source = ht.feature().create(hs_name, "HeatSource")
            heat_source.selection().named(str(binding.get("selection_name", "")))

            volume = (comp.dimensions.x * comp.dimensions.y * comp.dimensions.z) / 1e9
            power_density = comp.power / volume if volume > 0 else 0
            power_expr_builder = getattr(self, "_heat_source_power_density_expression", None)
            heat_source_expr = (
                power_expr_builder(power_density)
                if callable(power_expr_builder)
                else f"{power_density}[W/m^3]"
            )
            heat_source.set("Q0", heat_source_expr)
            logger.info(
                "      ✓ 热源已设置: %s, 功率密度: %.2e W/m³",
                heat_source_expr,
                power_density,
            )
            binding["heat_source_assigned"] = True
            total_heat_sources_assigned += 1

        if total_heat_sources_assigned == 0:
            logger.error("  ✗ 严重错误: 没有任何热源被成功绑定！仿真结果将无效！")
        else:
            total_power = sum(c.power for c in design_state.components if c.power > 0)
            logger.info(f"  ✓ 热源绑定完成: {total_heat_sources_assigned} 个热源, 总功率 {total_power}W")
        if ambiguous_heat_sources:
            logger.warning(
                "  ⚠ 以下组件因 Box Selection 多域歧义被跳过热源绑定: "
                + ", ".join(ambiguous_heat_sources)
            )
        if disambiguated_heat_sources:
            logger.info(
                "  ✓ 以下组件通过自动歧义收敛完成热源绑定: "
                + ", ".join(disambiguated_heat_sources)
            )
        if failed_heat_sources:
            logger.warning("  ⚠ 以下组件热源绑定失败: " + ", ".join(failed_heat_sources))

        return {
            "active_components": int(active_heat_components),
            "assigned_count": int(total_heat_sources_assigned),
            "ambiguous_components": list(ambiguous_heat_sources),
            "disambiguated_components": list(disambiguated_heat_sources),
            "failed_components": list(failed_heat_sources),
            "component_domain_bindings": list(component_domain_bindings),
        }

    def _normalize_entity_ids(self, entities: Any) -> list[int]:
        """Normalize COMSOL selection output to deduplicated integer IDs."""
        if entities is None:
            return []
        try:
            values = list(entities)
        except Exception:
            values = [entities]

        normalized: list[int] = []
        for value in values:
            try:
                normalized.append(int(value))
            except Exception:
                continue

        deduped: list[int] = []
        seen = set()
        for value in normalized:
            if value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped

    def _resolve_ambiguous_heat_domain(
        self,
        *,
        comp: Any,
        comp_index: int,
        domain_ids: list[int],
    ) -> tuple[Optional[int], Dict[str, Any]]:
        """
        Resolve ambiguous multi-domain hit:
        1) nearest domain center first
        2) fallback to index proximity
        """
        if not domain_ids:
            return None, {"method": "none", "distance_mm": None}

        comp_center = (
            float(comp.position.x),
            float(comp.position.y),
            float(comp.position.z),
        )
        scored = []
        for domain_id in domain_ids:
            center = self._estimate_domain_center_mm(domain_id)
            if center is None:
                continue
            dx = center[0] - comp_center[0]
            dy = center[1] - comp_center[1]
            dz = center[2] - comp_center[2]
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            scored.append((float(distance), int(domain_id)))

        if scored:
            scored.sort(key=lambda item: (item[0], item[1]))
            best_distance, best_domain = scored[0]
            return int(best_domain), {
                "method": "bbox_centroid_distance",
                "distance_mm": float(best_distance),
            }

        expected_domain = int(comp_index + 1)
        fallback_domain = min(
            domain_ids,
            key=lambda domain_id: (abs(int(domain_id) - expected_domain), int(domain_id)),
        )
        return int(fallback_domain), {
            "method": "domain_index_fallback",
            "distance_mm": float(abs(int(fallback_domain) - expected_domain)),
        }

    def _list_domain_ids(self) -> list[int]:
        if self.model is None:
            return []
        try:
            geom = self.model.java.geom("geom1")
            count = int(geom.getNDomains())
        except Exception:
            return []
        return [domain_id for domain_id in range(1, count + 1)]

    def _resolve_missing_heat_domain(
        self,
        *,
        comp: Any,
        comp_index: int,
    ) -> tuple[Optional[int], Dict[str, Any]]:
        domain_ids = self._list_domain_ids()
        if not domain_ids:
            return None, {"method": "no_domains", "distance_mm": None, "size_error": None}

        comp_center = (
            float(comp.position.x),
            float(comp.position.y),
            float(comp.position.z),
        )
        comp_span = (
            max(float(comp.dimensions.x), 1.0),
            max(float(comp.dimensions.y), 1.0),
            max(float(comp.dimensions.z), 1.0),
        )
        comp_volume = float(comp_span[0] * comp_span[1] * comp_span[2])
        scored: list[tuple[float, float, float, int]] = []
        for domain_id in domain_ids:
            bbox = self._estimate_domain_bbox_mm(domain_id)
            if bbox is None:
                continue
            center = (
                float((bbox[0] + bbox[1]) / 2.0),
                float((bbox[2] + bbox[3]) / 2.0),
                float((bbox[4] + bbox[5]) / 2.0),
            )
            span = (
                max(float(abs(bbox[1] - bbox[0])), 1e-6),
                max(float(abs(bbox[3] - bbox[2])), 1e-6),
                max(float(abs(bbox[5] - bbox[4])), 1e-6),
            )
            dx = center[0] - comp_center[0]
            dy = center[1] - comp_center[1]
            dz = center[2] - comp_center[2]
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            size_error = sum(
                abs(span[idx] - comp_span[idx]) / comp_span[idx]
                for idx in range(3)
            )
            overlap_x = max(
                0.0,
                min(bbox[1], comp_center[0] + comp_span[0] / 2.0)
                - max(bbox[0], comp_center[0] - comp_span[0] / 2.0),
            )
            overlap_y = max(
                0.0,
                min(bbox[3], comp_center[1] + comp_span[1] / 2.0)
                - max(bbox[2], comp_center[1] - comp_span[1] / 2.0),
            )
            overlap_z = max(
                0.0,
                min(bbox[5], comp_center[2] + comp_span[2] / 2.0)
                - max(bbox[4], comp_center[2] - comp_span[2] / 2.0),
            )
            overlap_fraction = float((overlap_x * overlap_y * overlap_z) / max(comp_volume, 1.0))
            score = float(distance + 40.0 * size_error - 120.0 * overlap_fraction)
            scored.append((score, float(distance), float(size_error), int(domain_id)))

        if not scored:
            return None, {"method": "bbox_unavailable", "distance_mm": None, "size_error": None}

        scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        _, distance_mm, size_error, domain_id = scored[0]
        max_reasonable_distance = max(comp_span) * 1.75
        if distance_mm > max_reasonable_distance and size_error > 2.5:
            return None, {
                "method": "nearest_bbox_rejected",
                "distance_mm": float(distance_mm),
                "size_error": float(size_error),
            }
        return int(domain_id), {
            "method": "nearest_bbox_match",
            "distance_mm": float(distance_mm),
            "size_error": float(size_error),
        }

    def _estimate_domain_center_mm(self, domain_id: int) -> Optional[tuple[float, float, float]]:
        """Best-effort domain centroid extraction from bbox, return None on failure."""
        bbox = self._estimate_domain_bbox_mm(domain_id)
        if bbox is None:
            return None
        return (
            float((bbox[0] + bbox[1]) / 2.0),
            float((bbox[2] + bbox[3]) / 2.0),
            float((bbox[4] + bbox[5]) / 2.0),
        )

    def _estimate_domain_bbox_mm(self, domain_id: int) -> Optional[tuple[float, float, float, float, float, float]]:
        """Best-effort domain bounding box extraction in geometry units."""
        if self.model is None:
            return None

        try:
            geom = self.model.java.geom("geom1")
            measure = geom.measure()
            try:
                measure.selection().init(3)
            except Exception:
                pass
            measure.selection().set([int(domain_id)])
            bbox = None
            for method_name in ("getBoundingBox", "boundingBox", "bbox"):
                if hasattr(measure, method_name):
                    method = getattr(measure, method_name)
                    try:
                        bbox = method()
                        break
                    except Exception:
                        continue
            if bbox is None:
                return None

            values = []
            for value in list(bbox):
                try:
                    values.append(float(value))
                except Exception:
                    return None
            if len(values) < 6:
                return None

            return (
                float(values[0]),
                float(values[1]),
                float(values[2]),
                float(values[3]),
                float(values[4]),
                float(values[5]),
            )
        except Exception:
            return None

    def _set_thermal_contact_conductance(
        self,
        thermal_contact: Any,
        conductance: float,
    ) -> tuple[bool, str, list[str]]:
        """
        Set thermal-contact conductance across COMSOL API variants.

        Priority:
        1) direct keys: h_tc / h_joint / h
        2) TotalConductance: htot
        3) ConstrictionConductance: hconstr + hgap
        4) TotalResistance: Rtot
        """
        conductance_with_unit = f"{conductance}[W/(m^2*K)]"
        conductance_plain = f"{conductance}"
        resistance_with_unit = (
            f"{1.0 / conductance}[(m^2*K)/W]"
            if conductance > 0
            else "1e9[(m^2*K)/W]"
        )

        attempt_errors: list[str] = []

        def _try_set(param: str, values: list[str]) -> Optional[str]:
            for expr in values:
                try:
                    thermal_contact.set(param, expr)
                    return expr
                except Exception as exc:
                    attempt_errors.append(f"{param}={expr} 失败: {exc}")
            return None

        for param_name in ("h_tc", "h_joint", "h"):
            used_value = _try_set(param_name, [conductance_with_unit, conductance_plain])
            if used_value is not None:
                return True, f"{param_name}={used_value}", attempt_errors

        try:
            thermal_contact.set("ContactModel", "EquThinLayer")
            thermal_contact.set("Specify", "TotalConductance")
            used_value = _try_set("htot", [conductance_with_unit, conductance_plain])
            if used_value is not None:
                return True, f"EquThinLayer/htot={used_value}", attempt_errors
        except Exception as exc:
            attempt_errors.append(f"EquThinLayer 配置失败: {exc}")

        try:
            thermal_contact.set("ContactModel", "ConstrictionConductance")
            thermal_contact.set("hcType", "UserDef")
            hconstr_value = _try_set("hconstr", [conductance_with_unit, conductance_plain])
            thermal_contact.set("hgType", "UserDef")
            hgap_value = _try_set("hgap", [conductance_with_unit, conductance_plain])
            if hconstr_value is not None and hgap_value is not None:
                return (
                    True,
                    f"ConstrictionConductance/hconstr={hconstr_value},hgap={hgap_value}",
                    attempt_errors,
                )
        except Exception as exc:
            attempt_errors.append(f"ConstrictionConductance 配置失败: {exc}")

        try:
            thermal_contact.set("Specify", "TotalResistance")
            used_value = _try_set("Rtot", [resistance_with_unit])
            if used_value is not None:
                return True, f"Rtot={used_value}", attempt_errors
        except Exception as exc:
            attempt_errors.append(f"TotalResistance 配置失败: {exc}")

        return False, "", attempt_errors

    def _apply_thermal_properties_dynamic(
        self,
        design_state,
        ht: Any,
        geom: Any,
    ):
        """
        Apply component-level thermal properties:
        - coating overrides
        - thermal contacts
        """
        _ = geom
        coating_count = 0
        contact_count = 0
        mount_face_index = self._shell_contact_mount_face_index(design_state)
        component_domain_index = self._resolve_component_domain_index(design_state)
        shell_domain_ids = self._infer_shell_domain_ids(
            component_domain_index=component_domain_index,
        )
        geometry_is_assembly = self._geometry_is_assembly()
        boundary_adjacency = self._boundary_domain_adjacency()
        shell_contact_report: Dict[str, Any] = {
            "enabled": True,
            "geometry_is_assembly": bool(geometry_is_assembly),
            "shell_domain_ids": [int(value) for value in list(shell_domain_ids or []) if int(value) > 0],
            "components": [],
            "required_count": int(len(list(mount_face_index or {}))),
            "applied_count": 0,
            "unresolved_count": 0,
        }
        self._last_shell_contact_report = shell_contact_report

        for i, comp in enumerate(design_state.components):
            has_custom_coating = (
                hasattr(comp, "emissivity")
                and comp.emissivity != 0.8
                or hasattr(comp, "absorptivity")
                and comp.absorptivity != 0.3
                or hasattr(comp, "coating_type")
                and comp.coating_type != "default"
            )

            if has_custom_coating:
                emissivity = getattr(comp, "emissivity", 0.8)
                absorptivity = getattr(comp, "absorptivity", 0.3)
                coating_type = getattr(comp, "coating_type", "default")

                logger.info(
                    f"    - 组件 {comp.id} 应用自定义涂层: "
                    f"ε={emissivity}, α={absorptivity}, type={coating_type}"
                )

                mat_name = f"mat_coating_{i}"
                try:
                    mat = self.model.java.material().create(mat_name, "Common")
                    mat.label(f"Coating for {comp.id} ({coating_type})")
                    apply_defaults = getattr(self, "_apply_multiphysics_material_defaults", None)
                    if callable(apply_defaults):
                        apply_defaults(
                            mat,
                            emissivity=float(emissivity),
                            set_structural=True,
                            set_electrical=True,
                        )
                    else:
                        mat.propertyGroup("def").set("thermalconductivity", "167[W/(m*K)]")
                        mat.propertyGroup("def").set("density", "2700[kg/m^3]")
                        mat.propertyGroup("def").set("heatcapacity", "896[J/(kg*K)]")
                        mat.propertyGroup("def").set("epsilon_rad", str(emissivity))

                    sel_name = f"boxsel_coating_{i}"
                    self._create_component_box_selection(comp, sel_name, entity_dim=3)
                    mat.selection().named(sel_name)

                    logger.info("      ✓ 涂层材料已创建并应用")
                    coating_count += 1
                except Exception as exc:
                    logger.warning(f"      ⚠ 涂层应用失败: {exc}")

            thermal_contacts = getattr(comp, "thermal_contacts", None)
            if thermal_contacts and isinstance(thermal_contacts, dict):
                for contact_comp_id, conductance in thermal_contacts.items():
                    logger.info(
                        f"    - 设置接触热阻: {comp.id} ↔ {contact_comp_id}, "
                        f"h={conductance} W/m²·K"
                    )

                    try:
                        contact_comp = None
                        contact_idx = None
                        for j, candidate in enumerate(design_state.components):
                            if candidate.id == contact_comp_id:
                                contact_comp = candidate
                                contact_idx = j
                                break

                        if contact_comp is None:
                            logger.warning(f"      ⚠ 接触组件 {contact_comp_id} 未找到")
                            continue

                        tc_name = f"tc_{i}_{contact_idx}"
                        thermal_contact = ht.feature().create(tc_name, "ThermalContact")

                        conductance_value = float(conductance)
                        set_ok, set_desc, attempt_errors = self._set_thermal_contact_conductance(
                            thermal_contact, conductance_value
                        )
                        if not set_ok:
                            raise ValueError(
                                "无法设置接触热导参数 (尝试 h_tc/h_joint/h + "
                                "htot/hconstr/hgap/Rtot 均失败): "
                                + " | ".join(attempt_errors)
                            )
                        logger.info(f"      ✓ 接触热导参数已设置: {set_desc}")

                        sel_a_name = f"boxsel_tc_a_{i}_{contact_idx}"
                        sel_b_name = f"boxsel_tc_b_{i}_{contact_idx}"

                        self._create_component_box_selection(
                            comp, sel_a_name, entity_dim=2, condition="intersects"
                        )
                        self._create_component_box_selection(
                            contact_comp, sel_b_name, entity_dim=2, condition="intersects"
                        )

                        try:
                            sel_a_entities = list(self.model.java.selection(sel_a_name).entities())
                            sel_b_entities = list(self.model.java.selection(sel_b_name).entities())
                            merged_entities = sorted(set(sel_a_entities + sel_b_entities))
                            if not merged_entities:
                                raise ValueError("接触边界选择为空")
                            thermal_contact.selection().set(merged_entities)
                            logger.info(f"      ✓ 接触边界已绑定: {len(merged_entities)} 个边界实体")
                        except Exception as selection_error:
                            logger.warning(
                                f"      ⚠ 接触边界实体合并失败，回退到单侧选择: {selection_error}"
                            )
                            thermal_contact.selection().named(sel_a_name)

                        logger.info("      ✓ 接触热阻已设置")
                        contact_count += 1
                    except Exception as exc:
                        logger.warning(f"      ⚠ 接触热阻设置失败: {exc}")
            elif self._apply_default_shell_mount_contact(
                design_state=design_state,
                ht=ht,
                comp=comp,
                comp_index=i,
                mount_face=mount_face_index.get(str(comp.id), ""),
                component_domain_index=component_domain_index,
                shell_domain_ids=shell_domain_ids,
                boundary_adjacency=boundary_adjacency,
                geometry_is_assembly=geometry_is_assembly,
                shell_contact_report=shell_contact_report,
            ):
                contact_count += 1

        shell_contact_components = list(shell_contact_report.get("components", []) or [])
        shell_contact_report["applied_count"] = int(
            sum(1 for item in shell_contact_components if bool(dict(item or {}).get("applied", False)))
        )
        shell_contact_report["unresolved_count"] = int(
            sum(
                1
                for item in shell_contact_components
                if not bool(dict(item or {}).get("applied", False))
            )
        )
        self._last_shell_contact_report = shell_contact_report
        logger.info(f"  ✓ 热学属性应用完成: {coating_count} 个涂层, {contact_count} 个接触热阻")
