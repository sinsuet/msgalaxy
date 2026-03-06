from __future__ import annotations

import math
from typing import Any, Dict, Optional

from core.logger import get_logger

logger = get_logger(__name__)


class ComsolThermalOperatorMixin:
    """Heat-source binding and thermal operator execution helpers."""

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

        for i, comp in enumerate(design_state.components):
            if comp.power <= 0:
                continue
            active_heat_components += 1

            logger.info(f"    - 为组件 {comp.id} 创建热源 ({comp.power}W)")

            pos = comp.position
            dim = comp.dimensions
            tolerance = 1e-3
            x_min = pos.x - dim.x / 2 - tolerance
            x_max = pos.x + dim.x / 2 + tolerance
            y_min = pos.y - dim.y / 2 - tolerance
            y_max = pos.y + dim.y / 2 + tolerance
            z_min = pos.z - dim.z / 2 - tolerance
            z_max = pos.z + dim.z / 2 + tolerance

            sel_name = f"boxsel_comp_{i}"
            box_sel = self.model.java.selection().create(sel_name, "Box")
            box_sel.set("entitydim", "3")
            box_sel.set("xmin", f"{x_min}[mm]")
            box_sel.set("xmax", f"{x_max}[mm]")
            box_sel.set("ymin", f"{y_min}[mm]")
            box_sel.set("ymax", f"{y_max}[mm]")
            box_sel.set("zmin", f"{z_min}[mm]")
            box_sel.set("zmax", f"{z_max}[mm]")
            box_sel.set("condition", "inside")

            selection_name = sel_name
            try:
                selected_entities = self._normalize_entity_ids(box_sel.entities())
                num_selected = len(selected_entities)
                logger.info(f"      Box Selection 选中 {num_selected} 个域")

                if num_selected == 0:
                    logger.warning(f"      ⚠️ inside 条件选中 0 个域，回退到 intersects: {comp.id}")
                    box_sel.set("condition", "intersects")
                    selected_entities = self._normalize_entity_ids(box_sel.entities())
                    num_selected = len(selected_entities)
                    logger.info(f"      intersects 回退后选中 {num_selected} 个域")

                if num_selected > 1:
                    logger.warning(f"      ⚠️ 选中 {num_selected} 个域，尝试 allvertices 收紧: {comp.id}")
                    box_sel.set("condition", "allvertices")
                    selected_entities = self._normalize_entity_ids(box_sel.entities())
                    num_selected = len(selected_entities)
                    logger.info(f"      allvertices 收紧后选中 {num_selected} 个域")

                if num_selected > 1:
                    resolved_domain, resolve_meta = self._resolve_ambiguous_heat_domain(
                        comp=comp,
                        comp_index=i,
                        domain_ids=selected_entities,
                    )
                    if resolved_domain is None:
                        logger.error(
                            f"      ✗ 热源绑定拒绝: 组件 {comp.id} 仍歧义命中 {num_selected} 个域，跳过该热源"
                        )
                        ambiguous_heat_sources.append(comp.id)
                        failed_heat_sources.append(comp.id)
                        continue

                    resolved_sel_name = f"{sel_name}_resolved"
                    try:
                        resolved_sel = self.model.java.selection().create(resolved_sel_name, "Explicit")
                        resolved_sel.geom("geom1", 3)
                        resolved_sel.set([int(resolved_domain)])
                        selection_name = resolved_sel_name
                        disambiguated_heat_sources.append(comp.id)
                        logger.warning(
                            "      ⚠️ 多域歧义已自动收敛: "
                            f"{comp.id} -> domain {resolved_domain} "
                            f"(method={resolve_meta.get('method')}, "
                            f"distance_mm={resolve_meta.get('distance_mm')})"
                        )
                    except Exception as resolve_bind_error:
                        logger.error(
                            f"      ✗ 歧义域收敛后绑定失败: {comp.id}, error={resolve_bind_error}"
                        )
                        ambiguous_heat_sources.append(comp.id)
                        failed_heat_sources.append(comp.id)
                        continue

                if num_selected == 0:
                    logger.warning(f"      ⚠️ 严重警告: 热源 Box Selection 失败！组件 {comp.id} 未选中任何域！")
                    logger.warning(
                        f"      Box 范围: X[{x_min:.1f}, {x_max:.1f}], "
                        f"Y[{y_min:.1f}, {y_max:.1f}], Z[{z_min:.1f}, {z_max:.1f}] mm"
                    )
                    logger.warning(f"      组件位置: [{pos.x:.1f}, {pos.y:.1f}, {pos.z:.1f}] mm")
                    logger.warning(f"      组件尺寸: [{dim.x:.1f}, {dim.y:.1f}, {dim.z:.1f}] mm")
                    logger.error(f"      ✗ 热源绑定彻底失败！{comp.power}W 热源未施加到组件 {comp.id}！")
                    failed_heat_sources.append(comp.id)
                    continue
            except Exception as sel_check_error:
                logger.warning(f"      无法检查选中域数量: {sel_check_error}")
                failed_heat_sources.append(comp.id)
                continue

            hs_name = f"hs_{i}"
            heat_source = ht.feature().create(hs_name, "HeatSource")
            heat_source.selection().named(selection_name)

            volume = (dim.x * dim.y * dim.z) / 1e9
            power_density = comp.power / volume if volume > 0 else 0
            heat_source.set("Q0", f"{power_density} * P_scale [W/m^3]")
            logger.info(f"      ✓ 热源已设置: {comp.power}W * P_scale, 功率密度: {power_density:.2e} W/m³")
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

    def _estimate_domain_center_mm(self, domain_id: int) -> Optional[tuple[float, float, float]]:
        """Best-effort domain centroid extraction from bbox, return None on failure."""
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
                float((values[0] + values[1]) / 2.0),
                float((values[2] + values[3]) / 2.0),
                float((values[4] + values[5]) / 2.0),
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

        logger.info(f"  ✓ 热学属性应用完成: {coating_count} 个涂层, {contact_count} 个接触热阻")
