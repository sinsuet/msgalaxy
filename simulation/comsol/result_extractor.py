from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.logger import get_logger
from simulation.comsol.field_registry import get_field_spec
from simulation.power_network_solver import solve_dc_power_network_metrics

logger = get_logger(__name__)


class ComsolResultExtractorMixin:
    """Structural/power/coupled branches and result extraction for dynamic COMSOL runs."""

    @staticmethod
    def _resolve_shell_geometry(design_state) -> Dict[str, float]:
        try:
            from geometry.shell_spec import resolve_shell_spec

            shell_spec = resolve_shell_spec(design_state)
        except Exception:
            shell_spec = None

        if shell_spec is not None:
            try:
                outer_x, outer_y, outer_z = (
                    float(value) for value in shell_spec.outer_size_mm()
                )
                thickness = float(getattr(shell_spec, "thickness_mm", 0.0) or 0.0)
                if min(outer_x, outer_y, outer_z) > 0.0 and thickness > 0.0:
                    return {
                        "outer_x": outer_x,
                        "outer_y": outer_y,
                        "outer_z": outer_z,
                        "thickness": thickness,
                    }
            except Exception:
                pass

        metadata = dict(getattr(design_state, "metadata", {}) or {})
        shell_truth = dict(metadata.get("resolved_shell_truth", {}) or {})
        outer_size = list(shell_truth.get("outer_size_mm", []) or [])
        thickness = float(shell_truth.get("thickness_mm", 0.0) or 0.0)
        if len(outer_size) >= 3 and thickness > 0.0:
            outer_x, outer_y, outer_z = (
                float(outer_size[0]),
                float(outer_size[1]),
                float(outer_size[2]),
            )
            if min(outer_x, outer_y, outer_z) > 0.0:
                return {
                    "outer_x": outer_x,
                    "outer_y": outer_y,
                    "outer_z": outer_z,
                    "thickness": thickness,
                }
        return {}

    def _select_power_terminal_components(self, design_state) -> Tuple[Optional[Any], Optional[Any]]:
        components = list(getattr(design_state, "components", []) or [])
        if not components:
            return None, None

        def _score_tokens(comp: Any, tokens: Tuple[str, ...]) -> int:
            text = (
                str(getattr(comp, "category", "") or "").lower()
                + " "
                + str(getattr(comp, "id", "") or "").lower()
            )
            return int(sum(1 for token in tokens if token in text))

        source_tokens = ("power", "battery", "eps", "bus", "pdu", "source")
        sink_tokens = ("payload", "avionics", "camera", "tx", "rx", "comm", "obc", "load")

        source_candidates = [comp for comp in components if _score_tokens(comp, source_tokens) > 0]
        sink_candidates = [comp for comp in components if _score_tokens(comp, sink_tokens) > 0]

        def _power(comp: Any) -> float:
            try:
                return max(float(getattr(comp, "power", 0.0) or 0.0), 0.0)
            except Exception:
                return 0.0

        source = None
        if source_candidates:
            source = max(source_candidates, key=_power)
        else:
            source = max(components, key=_power)

        sink = None
        sink_pool = [comp for comp in sink_candidates if str(getattr(comp, "id", "")) != str(getattr(source, "id", ""))]
        if sink_pool:
            sink = max(sink_pool, key=_power)
        else:
            fallback = [comp for comp in components if str(getattr(comp, "id", "")) != str(getattr(source, "id", ""))]
            if fallback:
                sink = min(fallback, key=_power)

        return source, sink

    def _configure_power_multiphysics(self, *, design_state, ht: Any) -> bool:
        """
        Configure COMSOL electric-current branch for power metrics.

        This is a real COMSOL path (ec + terminal/ground), not external network proxy.
        """
        _ = ht
        if not bool(self.enable_power_comsol_real):
            return False
        if self.model is None:
            return False

        try:
            ec = None
            try:
                ec = self.model.java.physics("ec")
            except Exception:
                ec = None

            if ec is None:
                create_errors = []
                for physics_name in ("ElectricCurrents", "ConductiveMedia"):
                    try:
                        ec = self.model.java.physics().create("ec", physics_name, "geom1")
                        break
                    except Exception as exc:
                        create_errors.append(f"{physics_name}:{exc}")
                if ec is None:
                    logger.warning("  ⚠ 电学场创建失败，回退电源网络求解: %s", " | ".join(create_errors))
                    return False

            try:
                ec.selection().all()
            except Exception:
                pass

            conductivity_expr = f"{max(self.electrical_conductivity_s_per_m, 1e-6)}[S/m]"
            sigma_set_ok = False
            sigma_errors = []
            for feature_tag in ("cucn1", "ccn1", "init1"):
                try:
                    feature_node = ec.feature(feature_tag)
                except Exception:
                    continue
                for sigma_mat_key in ("sigma_mat", "sigmaType"):
                    try:
                        feature_node.set(sigma_mat_key, "userdef")
                    except Exception:
                        continue
                for sigma_key in ("sigma", "sigma11"):
                    try:
                        feature_node.set(sigma_key, conductivity_expr)
                        sigma_set_ok = True
                        break
                    except Exception as exc:
                        sigma_errors.append(f"{feature_tag}:{sigma_key}:{exc}")
                if sigma_set_ok:
                    break
            if not sigma_set_ok:
                for prop_name in ("def", "FromMat", "PhysicalModelProperty"):
                    try:
                        ec.prop(prop_name).set("sigma", conductivity_expr)
                        sigma_set_ok = True
                        break
                    except Exception as exc:
                        sigma_errors.append(f"prop:{prop_name}:{exc}")
            if not sigma_set_ok:
                logger.warning(
                    "  ⚠ 电导率显式设置失败，ec 求解可能报 sigma 缺失: %s",
                    " | ".join(sigma_errors[-4:]),
                )

            source_comp, sink_comp = self._select_power_terminal_components(design_state)
            if source_comp is None or sink_comp is None:
                logger.warning("  ⚠ 电学端口组件无法确定，回退电源网络求解")
                return False

            source_sel_name = "boxsel_ec_source"
            sink_sel_name = "boxsel_ec_sink"
            self._create_component_box_selection(source_comp, source_sel_name, entity_dim=2, condition="intersects")
            self._create_component_box_selection(sink_comp, sink_sel_name, entity_dim=2, condition="intersects")

            ground_ok = False
            ground_errors = []
            for feature_type in ("Ground", "ElectricPotential"):
                try:
                    gnd = ec.feature().create("ec_gnd", feature_type, 2)
                    gnd.selection().named(sink_sel_name)
                    ground_ok = True
                    break
                except Exception as exc:
                    ground_errors.append(f"{feature_type}:{exc}")

            constraints = dict(self.config.get("constraints", {}) or {})
            bus_voltage_v = max(float(constraints.get("bus_voltage_v", 28.0) or 28.0), 1e-6)
            nominal_power_w = float(
                sum(max(float(getattr(comp, "power", 0.0) or 0.0), 0.0) for comp in design_state.components)
            )
            source_current_a = max(
                nominal_power_w / bus_voltage_v * max(float(self.power_source_current_scale), 1e-6),
                1e-6,
            )

            def _configure_terminal_excitation(term_node: Any) -> tuple[bool, str, str]:
                mode_raw = str(getattr(self, "power_terminal_mode", "auto") or "auto").strip().lower()
                mode_order = [mode_raw] if mode_raw in {"voltage", "current"} else ["voltage", "current"]
                detail_errors: list[str] = []
                for mode in mode_order:
                    configured = False
                    if mode == "voltage":
                        try:
                            term_node.set("TerminalType", "Voltage")
                        except Exception:
                            pass
                        for param in ("V0", "V", "TerminalVoltage", "Voltage"):
                            try:
                                term_node.set(param, f"{bus_voltage_v}[V]")
                                configured = True
                                break
                            except Exception as exc:
                                detail_errors.append(f"{mode}:{param}:{exc}")
                    else:
                        try:
                            term_node.set("TerminalType", "Current")
                        except Exception:
                            pass
                        for param in ("I0", "I", "TerminalCurrent", "Current"):
                            try:
                                term_node.set(param, f"{source_current_a}[A]")
                                configured = True
                                break
                            except Exception as exc:
                                detail_errors.append(f"{mode}:{param}:{exc}")
                    if configured:
                        return True, mode, ""
                return False, "", " | ".join(detail_errors[-6:])

            terminal_ok = False
            terminal_errors = []
            terminal_mode = ""
            for feature_type in ("Terminal", "CurrentTerminal"):
                try:
                    term = ec.feature().create("ec_term", feature_type, 2)
                    term.selection().named(source_sel_name)
                    terminal_ok, terminal_mode, terminal_error = _configure_terminal_excitation(term)
                    if terminal_ok:
                        break
                    terminal_errors.append(f"{feature_type}:{terminal_error}")
                except Exception as exc:
                    terminal_errors.append(f"{feature_type}:{exc}")

            if not ground_ok or not terminal_ok:
                logger.warning(
                    "  ⚠ 电学端口/接地配置失败，回退电源网络求解: ground=%s (%s), terminal=%s (%s)",
                    ground_ok,
                    " | ".join(ground_errors),
                    terminal_ok,
                    " | ".join(terminal_errors),
                )
                return False

            try:
                mp = self.model.java.multiphysics()
                try:
                    jh = mp.create("jh1", "JouleHeating", "geom1")
                except Exception:
                    jh = mp.create("jh1", "ElectromagneticHeating", "geom1")
                try:
                    jh.selection().all()
                except Exception:
                    pass
            except Exception as exc:
                logger.warning("  ⚠ 电热耦合节点创建失败（非致命）: %s", exc)

            try:
                study_tags = list(self.model.java.study().tags())
            except Exception:
                study_tags = []
            if "std_power" not in study_tags:
                std_power = self.model.java.study().create("std_power")
                std_power.feature().create("stat", "Stationary")
            self._set_study_step_activation(
                study_tag="std_power",
                step_tag="stat",
                activation={
                    "ht": False,
                    "ec": True,
                    "solid": False,
                },
            )

            self._power_terminal_meta = {
                "source_component_id": str(getattr(source_comp, "id", "") or ""),
                "sink_component_id": str(getattr(sink_comp, "id", "") or ""),
                "source_current_a": float(source_current_a),
                "bus_voltage_v": float(bus_voltage_v),
                "nominal_power_w": float(nominal_power_w),
                "terminal_mode": str(terminal_mode or ""),
            }
            logger.info(
                "  ✓ 电学场支路已配置 (std_power): source=%s sink=%s I=%.4fA Vbus=%.3fV mode=%s",
                self._power_terminal_meta.get("source_component_id"),
                self._power_terminal_meta.get("sink_component_id"),
                float(source_current_a),
                float(bus_voltage_v),
                str(self._power_terminal_meta.get("terminal_mode", "")),
            )
            return True
        except Exception as exc:
            logger.warning("  ⚠ 电学场支路配置失败，回退电源网络求解: %s", exc)
            return False

    def _configure_structural_multiphysics(self, *, design_state=None) -> bool:
        """
        Configure structural branch (Solid + Stationary + Eigenfrequency).

        Failures here should not interrupt thermal solve chain.
        """
        if not bool(self.enable_structural_real):
            return False
        if self.model is None:
            return False

        try:
            try:
                solid = self.model.java.physics("solid")
            except Exception:
                solid = self.model.java.physics().create("solid", "SolidMechanics", "geom1")

            fixed_entities = []
            try:
                fixed = solid.feature().create("fix_all", "Fixed", 2)
                fixed_entities = self._select_structural_support_boundaries(design_state=design_state)
                if fixed_entities:
                    try:
                        fixed.selection().set(fixed_entities)
                    except Exception:
                        fixed.selection().all()
                else:
                    fixed.selection().all()
            except Exception:
                pass

            try:
                body = solid.feature().create("bndl1", "BodyLoad", 3)
                body.selection().all()
                accel = max(float(self.structural_launch_accel_g), 0.0)
                lateral_ratio = max(float(getattr(self, "structural_lateral_accel_ratio", 0.18)), 0.0)
                density = max(float(self.structural_density_kg_m3), 1e-6)
                body.set(
                    "FperVol",
                    [
                        f"{lateral_ratio * accel * 9.81 * density}[N/m^3]",
                        "0[N/m^3]",
                        f"-{accel * 9.81 * density}[N/m^3]",
                    ],
                )
            except Exception:
                pass

            try:
                study_tags = list(self.model.java.study().tags())
            except Exception:
                study_tags = []

            if "std_struct" not in study_tags:
                std_struct = self.model.java.study().create("std_struct")
                std_struct.feature().create("stat", "Stationary")
            if "std_modal" not in study_tags:
                std_modal = self.model.java.study().create("std_modal")
                eig = std_modal.feature().create("eig", "Eigenfrequency")
                eig.set("neigs", str(int(self.structural_modal_count)))
            self._set_study_step_activation(
                study_tag="std_struct",
                step_tag="stat",
                activation={
                    "ht": False,
                    "ec": False,
                    "solid": True,
                },
            )
            self._set_study_step_activation(
                study_tag="std_modal",
                step_tag="eig",
                activation={
                    "ht": False,
                    "ec": False,
                    "solid": True,
                },
            )

            logger.info("  ✓ 结构场支路已配置 (std_struct/std_modal)")
            return True
        except Exception as exc:
            logger.warning("  ⚠ 结构场支路配置失败，回退 proxy: %s", exc)
            return False

    def _select_structural_support_boundaries(self, *, design_state=None) -> list[int]:
        if self.model is None or design_state is None:
            return []
        shell_entities: list[int] = []
        shell = self._resolve_shell_geometry(design_state)
        if shell:
            try:
                selection_tag = "sel_struct_support_shell_bottom"
                try:
                    self.model.java.selection().remove(selection_tag)
                except Exception:
                    pass
                selection = self.model.java.selection().create(selection_tag, "Box")
                selection.label("Structural Support Shell Bottom")
                selection.set("entitydim", "2")
                selection.set("condition", "intersects")
                half_x = float(shell["outer_x"]) / 2.0
                half_y = float(shell["outer_y"]) / 2.0
                half_z = float(shell["outer_z"]) / 2.0
                face_tol = max(0.5, min(float(shell["thickness"]) * 0.4, 3.0))
                selection.set("xmin", f"{-half_x - 1.0}[mm]")
                selection.set("xmax", f"{half_x + 1.0}[mm]")
                selection.set("ymin", f"{-half_y - 1.0}[mm]")
                selection.set("ymax", f"{half_y + 1.0}[mm]")
                selection.set("zmin", f"{-half_z - face_tol}[mm]")
                selection.set("zmax", f"{-half_z + face_tol}[mm]")
                entities = [int(entity) for entity in list(self.model.java.selection(selection_tag).entities())]
                shell_entities = sorted(set(entities))
                if shell_entities:
                    logger.info("  ✓ 结构支撑边界已加入机壳底板: %d 个边界实体", len(shell_entities))
            except Exception as exc:
                logger.warning("  ⚠ 机壳底板支撑边界选择失败，将回退组件底部支撑: %s", exc)
        components = list(getattr(design_state, "components", []) or [])
        if not components:
            return shell_entities
        try:
            entities: list[int] = []
            for index, comp in enumerate(components):
                selection_tag = f"sel_struct_support_{index}"
                try:
                    self.model.java.selection().remove(selection_tag)
                except Exception:
                    pass
                selection = self.model.java.selection().create(selection_tag, "Box")
                selection.label(f"Structural Support {getattr(comp, 'id', index)}")
                selection.set("entitydim", "2")
                selection.set("condition", "intersects")

                x_min = float(comp.position.x - comp.dimensions.x / 2.0) - 0.5
                x_max = float(comp.position.x + comp.dimensions.x / 2.0) + 0.5
                y_min = float(comp.position.y - comp.dimensions.y / 2.0) - 0.5
                y_max = float(comp.position.y + comp.dimensions.y / 2.0) + 0.5
                z_min = float(comp.position.z - comp.dimensions.z / 2.0)
                z_tol = max(1.0, min(float(comp.dimensions.z) * 0.1, 4.0))

                selection.set("xmin", f"{x_min}[mm]")
                selection.set("xmax", f"{x_max}[mm]")
                selection.set("ymin", f"{y_min}[mm]")
                selection.set("ymax", f"{y_max}[mm]")
                selection.set("zmin", f"{z_min - z_tol}[mm]")
                selection.set("zmax", f"{z_min + z_tol}[mm]")
                try:
                    current_entities = [int(entity) for entity in list(self.model.java.selection(selection_tag).entities())]
                except Exception:
                    current_entities = []
                entities.extend(current_entities)

            entities = sorted(set(entities + list(shell_entities)))
            if entities:
                logger.info("  ✓ 结构支撑边界已选择: %d 个底部边界实体", len(entities))
            return entities
        except Exception as exc:
            logger.warning("  ⚠ 结构支撑边界选择失败，将回退全边界固定: %s", exc)
            return []

    def _configure_coupled_multiphysics(self) -> bool:
        """
        Configure coupled study branch for thermal + structural + electrical stationary solve.
        """
        if not bool(self.enable_coupled_multiphysics_real):
            return False
        if self.model is None:
            return False

        coupled_ok = False
        try:
            try:
                study_tags = list(self.model.java.study().tags())
            except Exception:
                study_tags = []
            if "std_coupled" not in study_tags:
                std_coupled = self.model.java.study().create("std_coupled")
                std_coupled.feature().create("stat", "Stationary")
            self._set_study_step_activation(
                study_tag="std_coupled",
                step_tag="stat",
                activation={
                    "ht": True,
                    "ec": True,
                    "solid": True,
                },
            )
            coupled_ok = True
        except Exception as exc:
            logger.warning("  ⚠ 耦合 study 创建失败: %s", exc)

        try:
            solid = self.model.java.physics("solid")
            try:
                te = solid.feature().create("solid_te", "ThermalExpansion", 3)
                te.selection().all()
                coupled_ok = True
            except Exception:
                pass
        except Exception:
            pass

        if coupled_ok:
            logger.info("  ✓ 热-结构-电耦合支路已配置 (std_coupled)")
        return bool(coupled_ok)

    def _run_power_studies(self) -> Dict[str, Any]:
        runtime: Dict[str, Any] = {
            "enabled": bool(self.enable_power_comsol_real),
            "setup_ok": bool(self._power_setup_ok),
            "study_entered": False,
            "study_solved": False,
            "stat_solved": False,
            "error": "",
        }
        if not bool(runtime["enabled"]) or not bool(runtime["setup_ok"]):
            return runtime
        if self.model is None:
            runtime["error"] = "model_unavailable"
            return runtime

        try:
            runtime["study_entered"] = True
            self.model.java.study("std_power").run()
            runtime["stat_solved"] = True
            runtime["study_solved"] = True
        except Exception as exc:
            runtime["error"] = f"power_stationary_failed:{exc}"
            logger.warning("  ⚠ 电学稳态求解失败，将回退网络求解: %s", exc)
        return runtime

    def _run_coupled_study(self) -> Dict[str, Any]:
        runtime: Dict[str, Any] = {
            "enabled": bool(self.enable_coupled_multiphysics_real),
            "setup_ok": bool(self._coupled_setup_ok),
            "study_entered": False,
            "study_solved": False,
            "stat_solved": False,
            "error": "",
        }
        if not bool(runtime["enabled"]) or not bool(runtime["setup_ok"]):
            return runtime
        if self.model is None:
            runtime["error"] = "model_unavailable"
            return runtime

        try:
            runtime["study_entered"] = True
            self.model.java.study("std_coupled").run()
            runtime["stat_solved"] = True
            runtime["study_solved"] = True
        except Exception as exc:
            runtime["error"] = f"coupled_stationary_failed:{exc}"
            logger.warning("  ⚠ 耦合稳态求解失败，将保留分支求解结果: %s", exc)
        return runtime

    def _run_structural_studies(self) -> Dict[str, Any]:
        runtime: Dict[str, Any] = {
            "enabled": bool(self.enable_structural_real),
            "setup_ok": bool(self._structural_setup_ok),
            "study_entered": False,
            "study_solved": False,
            "stat_solved": False,
            "modal_solved": False,
            "error": "",
        }
        if not bool(runtime["enabled"]) or not bool(runtime["setup_ok"]):
            return runtime

        if self.model is None:
            runtime["error"] = "model_unavailable"
            return runtime

        try:
            runtime["study_entered"] = True
            self.model.java.study("std_struct").run()
            runtime["stat_solved"] = True
        except Exception as exc:
            runtime["error"] = f"struct_stationary_failed:{exc}"
            logger.warning("  ⚠ 结构静力求解失败，回退 proxy: %s", exc)
            return runtime

        try:
            self.model.java.study("std_modal").run()
            runtime["modal_solved"] = True
            runtime["study_solved"] = True
        except Exception as exc:
            runtime["error"] = f"modal_failed:{exc}"
            logger.warning("  ⚠ 模态求解失败，频率指标回退: %s", exc)
        return runtime

    def _extract_power_metrics_from_comsol(
        self,
        *,
        dataset_candidates,
        design_state=None,
    ) -> Optional[Dict[str, float]]:
        voltage_max = self._evaluate_expression_candidates(
            # Prefer the generic potential variable first; in some COMSOL models
            # `ec.V` triggers noisy stale-dataset probing while `V` is clean.
            expressions=["V", "ec.V"],
            unit="V",
            datasets=dataset_candidates,
            reducer="max",
        )
        voltage_min = self._evaluate_expression_candidates(
            expressions=["V", "ec.V"],
            unit="V",
            datasets=dataset_candidates,
            reducer="min",
        )
        if voltage_max is None or voltage_min is None:
            return None

        constraints = dict(self.config.get("constraints", {}) or {})
        max_power_w = max(float(constraints.get("max_power_w", 500.0) or 500.0), 1e-6)
        terminal_meta = dict(getattr(self, "_power_terminal_meta", {}) or {})
        bus_voltage_v = max(
            float(terminal_meta.get("bus_voltage_v", constraints.get("bus_voltage_v", 28.0)) or 28.0),
            1e-6,
        )
        source_current_a = float(terminal_meta.get("source_current_a", 0.0) or 0.0)
        if source_current_a <= 0.0:
            nominal_power = float(terminal_meta.get("nominal_power_w", 0.0) or 0.0)
            if nominal_power <= 0.0 and design_state is not None:
                nominal_power = float(
                    sum(max(float(getattr(comp, "power", 0.0) or 0.0), 0.0) for comp in design_state.components)
                )
            source_current_a = max(nominal_power / bus_voltage_v, 1e-6)

        voltage_drop = max(float(voltage_max) - float(voltage_min), 0.0)
        effective_voltage = max(bus_voltage_v - voltage_drop, 0.0)
        total_power = max(source_current_a * effective_voltage, 0.0)
        peak_power = max(source_current_a * bus_voltage_v, total_power)
        power_margin = (max_power_w - peak_power) / max_power_w * 100.0

        if not np.isfinite(total_power) or not np.isfinite(power_margin):
            return None
        return {
            "total_power": float(total_power),
            "peak_power": float(peak_power),
            "power_margin": float(power_margin),
            "voltage_drop": float(voltage_drop),
        }

    def _extract_component_thermal_audit(
        self,
        *,
        design_state=None,
        dataset_candidates: Optional[List[Optional[str]]] = None,
    ) -> Dict[str, Any]:
        if self.model is None or design_state is None:
            return {
                "enabled": False,
                "expected_component_count": 0,
                "evaluated_component_count": 0,
                "dataset_candidates": [],
                "components": [],
                "dominant_hotspot": {},
            }

        temperature_field = get_field_spec("temperature")
        raw_candidates = list(dataset_candidates or self._build_dataset_candidates(prefer_modal=False))
        ordered_datasets: List[Optional[str]] = []
        explicit_candidates: List[str] = []
        seen_explicit: set[str] = set()
        seen_default = False
        for item in raw_candidates:
            if item is None:
                if not seen_default:
                    ordered_datasets.append(None)
                    seen_default = True
                continue
            tag = str(item).strip()
            if not tag or tag in seen_explicit:
                continue
            seen_explicit.add(tag)
            ordered_datasets.append(tag)
            explicit_candidates.append(tag)
        if not ordered_datasets:
            ordered_datasets = [None]

        components_payload: List[Dict[str, Any]] = []
        for index, comp in enumerate(list(getattr(design_state, "components", []) or [])):
            binding = self._resolve_component_domain_binding(
                comp=comp,
                comp_index=index,
                selection_tag=f"boxsel_comp_audit_{index}",
            )
            item: Dict[str, Any] = {
                "component_id": str(getattr(comp, "id", "") or ""),
                "category": str(getattr(comp, "category", "") or ""),
                "power_w": float(getattr(comp, "power", 0.0) or 0.0),
                "position_mm": [
                    float(getattr(comp.position, "x", 0.0) or 0.0),
                    float(getattr(comp.position, "y", 0.0) or 0.0),
                    float(getattr(comp.position, "z", 0.0) or 0.0),
                ],
                "dimensions_mm": [
                    float(getattr(comp.dimensions, "x", 0.0) or 0.0),
                    float(getattr(comp.dimensions, "y", 0.0) or 0.0),
                    float(getattr(comp.dimensions, "z", 0.0) or 0.0),
                ],
                "selection_name": str(binding.get("selection_name", "") or ""),
                "selection_status": str(binding.get("selection_status", "") or ""),
                "selection_condition": str(binding.get("selection_condition", "") or ""),
                "domain_ids": [int(value) for value in list(binding.get("domain_ids", []) or [])],
                "domain_count": int(binding.get("domain_count", 0) or 0),
                "resolution_method": str(binding.get("resolution_method", "") or ""),
                "distance_mm": binding.get("distance_mm", None),
                "size_error": binding.get("size_error", None),
                "evaluated": False,
            }
            domain_ids = [int(value) for value in list(binding.get("domain_ids", []) or []) if int(value) > 0]
            if not domain_ids:
                item["error"] = "domain_unresolved"
                components_payload.append(item)
                continue

            max_temp_k = self._evaluate_expression_candidates_on_selection(
                expressions=list(temperature_field.expression_candidates),
                unit=temperature_field.unit,
                datasets=list(ordered_datasets),
                reducer="max",
                selection_entities=domain_ids,
                entity_dim=3,
            )
            min_temp_k = self._evaluate_expression_candidates_on_selection(
                expressions=list(temperature_field.expression_candidates),
                unit=temperature_field.unit,
                datasets=list(ordered_datasets),
                reducer="min",
                selection_entities=domain_ids,
                entity_dim=3,
            )
            avg_temp_k = self._evaluate_expression_candidates_on_selection(
                expressions=list(temperature_field.expression_candidates),
                unit=temperature_field.unit,
                datasets=list(ordered_datasets),
                reducer="mean",
                selection_entities=domain_ids,
                entity_dim=3,
            )
            if max_temp_k is None or min_temp_k is None or avg_temp_k is None:
                item["error"] = "temperature_eval_failed"
                components_payload.append(item)
                continue

            item["evaluated"] = True
            item["max_temp_c"] = float(max_temp_k - 273.15)
            item["min_temp_c"] = float(min_temp_k - 273.15)
            item["avg_temp_c"] = float(avg_temp_k - 273.15)
            item["temp_gradient_k"] = float(max(max_temp_k - min_temp_k, 0.0))
            components_payload.append(item)

        evaluated_components = [
            dict(item)
            for item in list(components_payload or [])
            if bool(item.get("evaluated", False))
        ]
        evaluated_components.sort(
            key=lambda item: (
                -float(item.get("max_temp_c", -9999.0) or -9999.0),
                str(item.get("component_id", "") or ""),
            )
        )
        components_payload.sort(
            key=lambda item: (
                0 if bool(item.get("evaluated", False)) else 1,
                -float(item.get("max_temp_c", -9999.0) or -9999.0),
                str(item.get("component_id", "") or ""),
            )
        )
        dominant_hotspot = dict(evaluated_components[0]) if evaluated_components else {}
        return {
            "enabled": True,
            "expected_component_count": int(len(list(getattr(design_state, "components", []) or []))),
            "evaluated_component_count": int(len(evaluated_components)),
            "dataset_candidates": [str(item) for item in list(explicit_candidates or [])],
            "components": list(components_payload),
            "dominant_hotspot": dominant_hotspot,
        }

    def _extract_dynamic_results(
        self,
        *,
        design_state=None,
        structural_runtime: Optional[Dict[str, Any]] = None,
        power_runtime: Optional[Dict[str, Any]] = None,
        coupled_runtime: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, float]:
        """
        Extract thermal/structural/power metrics from dynamic model.
        """
        metrics: Dict[str, float] = {}
        logger.info("  开始提取多物理结果...")
        self._last_component_thermal_audit = {}
        self._last_dominant_thermal_hotspot = {}

        dataset_candidates = self._build_dataset_candidates(prefer_modal=False)
        modal_dataset_candidates = self._build_dataset_candidates(prefer_modal=True)
        temperature_field = get_field_spec("temperature")
        stress_field = get_field_spec("von_mises")
        displacement_field = get_field_spec("displacement_magnitude")

        max_temp_k = self._evaluate_expression_candidates(
            expressions=list(temperature_field.expression_candidates),
            unit=temperature_field.unit,
            datasets=dataset_candidates,
            reducer="max",
        )
        min_temp_k = self._evaluate_expression_candidates(
            expressions=list(temperature_field.expression_candidates),
            unit=temperature_field.unit,
            datasets=dataset_candidates,
            reducer="min",
        )
        avg_temp_k = self._evaluate_expression_candidates(
            expressions=list(temperature_field.expression_candidates),
            unit=temperature_field.unit,
            datasets=dataset_candidates,
            reducer="mean",
        )
        if max_temp_k is None or min_temp_k is None or avg_temp_k is None:
            logger.error("  ✗ 温度结果提取失败，返回惩罚温度")
            metrics["max_temp"] = 9999.0
            metrics["min_temp"] = 9999.0
            metrics["avg_temp"] = 9999.0
            metrics["temp_gradient"] = 0.0
        else:
            metrics["max_temp"] = float(max_temp_k - 273.15)
            metrics["min_temp"] = float(min_temp_k - 273.15)
            metrics["avg_temp"] = float(avg_temp_k - 273.15)
            metrics["temp_gradient"] = float(max(max_temp_k - min_temp_k, 0.0))
            component_thermal_audit = self._extract_component_thermal_audit(
                design_state=design_state,
                dataset_candidates=list(dataset_candidates),
            )
            self._last_component_thermal_audit = dict(component_thermal_audit or {})
            self._last_dominant_thermal_hotspot = dict(
                component_thermal_audit.get("dominant_hotspot", {}) or {}
            )

        runtime = dict(structural_runtime or {})
        if bool(self.enable_structural_real) and bool(runtime.get("stat_solved", False)):
            modal_dataset = self._select_modal_result_dataset(
                dataset_candidates=modal_dataset_candidates
            )
            structural_dataset = self._select_structural_stationary_dataset(
                dataset_candidates=dataset_candidates
            )
            structural_eval_datasets = [structural_dataset] if structural_dataset else dataset_candidates
            stress_pa = self._evaluate_expression_candidates(
                expressions=list(stress_field.expression_candidates),
                unit=stress_field.unit,
                datasets=structural_eval_datasets,
                reducer="max",
            )
            disp_m = self._evaluate_expression_candidates(
                expressions=list(displacement_field.expression_candidates),
                unit=displacement_field.unit,
                datasets=structural_eval_datasets,
                reducer="max",
            )
            first_modal_hz = None
            if bool(runtime.get("modal_solved", False)):
                modal_expr_candidates = [
                    "eigfreq",
                    "re(eigfreq)",
                    "real(eigfreq)",
                    "freq",
                    "re(freq)",
                    "real(freq)",
                    "solid.freq",
                    "re(solid.freq)",
                    "real(solid.freq)",
                ]
                first_modal_hz = self._evaluate_expression_candidates(
                    expressions=modal_expr_candidates,
                    unit="Hz",
                    # Default dataset is usually resolvable even when stale dset tags
                    # linger in COMSOL result tree; probe it first to avoid error storms.
                    datasets=[None],
                    reducer="min_positive",
                )
                if first_modal_hz is None:
                    first_modal_hz = self._extract_modal_frequency_via_eval_group(
                        dataset_candidates=[modal_dataset] if modal_dataset else modal_dataset_candidates,
                    )
                if first_modal_hz is None:
                    first_modal_hz = self._extract_modal_frequency_from_solver_sequence()
                if first_modal_hz is None:
                    logger.warning(
                        "  ⚠ 模态求解已完成但频率提取失败，将由上游按分项回退。 datasets=%s, runtime=%s",
                        [modal_dataset] if modal_dataset else modal_dataset_candidates[:8],
                        runtime,
                    )

            if stress_pa is not None and disp_m is not None:
                max_stress_mpa = float(stress_pa / 1e6) if stress_pa > 1e4 else float(stress_pa)
                max_displacement_mm = float(disp_m * 1000.0) if disp_m < 10.0 else float(disp_m)
                safety_factor = float(
                    max(float(self.structural_allowable_stress_mpa), 1e-6)
                    / max(max_stress_mpa, 1e-6)
                )
                metrics["max_stress"] = float(max_stress_mpa)
                metrics["max_displacement"] = float(max_displacement_mm)
                metrics["safety_factor"] = float(safety_factor)
                if first_modal_hz is not None:
                    metrics["first_modal_freq"] = float(first_modal_hz)

        comsol_power_used = False
        power_rt = dict(power_runtime or {})
        if bool(self.enable_power_comsol_real) and bool(power_rt.get("stat_solved", False)):
            try:
                comsol_power = self._extract_power_metrics_from_comsol(
                    dataset_candidates=dataset_candidates,
                    design_state=design_state,
                )
                if comsol_power:
                    metrics.update(comsol_power)
                    comsol_power_used = True
            except Exception as exc:
                logger.warning("  ⚠ COMSOL 电源指标提取失败，将回退网络求解: %s", exc)

        if (not comsol_power_used) and bool(self.enable_power_network_real) and design_state is not None:
            try:
                power_metrics = self._solve_power_network_metrics(design_state)
                metrics["total_power"] = float(power_metrics.get("total_power", 0.0))
                metrics["peak_power"] = float(power_metrics.get("peak_power", 0.0))
                metrics["power_margin"] = float(power_metrics.get("power_margin", 0.0))
                metrics["voltage_drop"] = float(power_metrics.get("voltage_drop", 0.0))
            except Exception as exc:
                logger.warning("  ⚠ 电源网络求解失败，回退上游 proxy: %s", exc)

        coupled_rt = dict(coupled_runtime or {})
        if bool(coupled_rt.get("stat_solved", False)):
            metrics["_coupled_solved"] = 1.0

        return metrics

    def _solve_power_network_metrics(self, design_state) -> Dict[str, float]:
        constraints = dict(self.config.get("constraints", {}) or {})
        return solve_dc_power_network_metrics(
            design_state,
            max_power_w=float(constraints.get("max_power_w", 500.0)),
            bus_voltage_v=float(constraints.get("bus_voltage_v", 28.0)),
            cable_resistance_ohm_per_m=float(self.power_network_cable_resistance_ohm_per_m),
            k_neighbors=int(self.power_network_k_neighbors),
            congestion_alpha=float(self.power_network_congestion_alpha),
        )
