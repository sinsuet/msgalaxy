from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from core.exceptions import SimulationError
from core.logger import get_logger
from core.protocol import SimulationResult

logger = get_logger(__name__)


class ComsolModelBuilderMixin:
    """Dynamic STEP import and COMSOL model assembly."""

    def _list_physics_tags(self) -> list[str]:
        if self.model is None:
            return []
        try:
            tags = list(self.model.java.physics().tags())
        except Exception:
            return []
        resolved: list[str] = []
        for tag in tags:
            text = str(tag).strip()
            if text:
                resolved.append(text)
        return resolved

    def _set_study_step_activation(
        self,
        *,
        study_tag: str,
        step_tag: str,
        activation: dict[str, bool],
    ) -> bool:
        """
        Scope one study step to selected physics using COMSOL's `activate` map.

        COMSOL Java API expects alternating String pairs:
          ["physTag1", "on|off", "physTag2", "on|off", ...]
        """
        if self.model is None:
            return False
        if not activation:
            return False

        available = set(self._list_physics_tags())
        encoded: list[str] = []
        for tag, enabled in activation.items():
            phys_tag = str(tag or "").strip()
            if not phys_tag:
                continue
            if available and phys_tag not in available:
                continue
            encoded.append(phys_tag)
            encoded.append("on" if bool(enabled) else "off")

        if len(encoded) < 2:
            return False

        try:
            self.model.java.study(str(study_tag)).feature(str(step_tag)).set("activate", encoded)
            logger.info(
                "  ✓ Study 激活映射已设置: %s/%s -> %s",
                study_tag,
                step_tag,
                encoded,
            )
            return True
        except Exception as exc:
            logger.warning(
                "  ⚠ Study 激活映射设置失败: %s/%s (%s), payload=%s",
                study_tag,
                step_tag,
                exc,
                encoded,
            )
            return False

    def _apply_multiphysics_material_defaults(
        self,
        mat: Any,
        *,
        emissivity: float = 0.8,
        set_structural: bool = True,
        set_electrical: bool = True,
    ) -> None:
        """
        Apply baseline thermal + structural + electrical properties to a COMSOL material node.

        Notes:
        - Structural branch may require dedicated Enu group (E/nu) on some COMSOL variants.
        - Coating materials must also carry structural keys, otherwise Solid mechanics may fail
          when coating selection overrides domain material assignment.
        """
        # Thermal baseline
        try:
            mat.propertyGroup("def").set("thermalconductivity", "167[W/(m*K)]")
        except Exception:
            pass
        try:
            mat.propertyGroup("def").set("density", f"{self.structural_density_kg_m3}[kg/m^3]")
        except Exception:
            pass
        try:
            mat.propertyGroup("def").set("heatcapacity", "896[J/(kg*K)]")
        except Exception:
            pass
        try:
            mat.propertyGroup("def").set("epsilon_rad", str(float(emissivity)))
        except Exception:
            pass

        if set_electrical:
            try:
                conductivity_expr = f"{max(self.electrical_conductivity_s_per_m, 1e-6)}[S/m]"
                for conductivity_key in ("electricconductivity", "sigma"):
                    try:
                        mat.propertyGroup("def").set(conductivity_key, conductivity_expr)
                    except Exception:
                        continue
            except Exception as mat_ec_exc:
                logger.warning("  ⚠ 电学材料参数写入失败，电学支路可能回退: %s", mat_ec_exc)

        if not set_structural:
            return

        try:
            young_expr = f"{max(self.structural_youngs_modulus_gpa, 1e-6)}[GPa]"
            nu_expr = str(float(np.clip(self.structural_poissons_ratio, 0.0, 0.499)))

            # Common aliases on def group.
            for young_key in ("youngsmodulus", "E"):
                try:
                    mat.propertyGroup("def").set(young_key, young_expr)
                except Exception:
                    continue
            for nu_key in ("poissonsratio", "poissonratio", "nu"):
                try:
                    mat.propertyGroup("def").set(nu_key, nu_expr)
                except Exception:
                    continue

            # Optional thermal expansion alias for coupled branch robustness.
            for alpha_key in ("thermalexpansioncoefficient", "alpha"):
                try:
                    mat.propertyGroup("def").set(alpha_key, "23e-6[1/K]")
                except Exception:
                    continue

            # Some COMSOL variants resolve Solid linear elasticity via Enu property group.
            enu_group = None
            try:
                enu_group = mat.propertyGroup("Enu")
            except Exception:
                enu_group = None
            if enu_group is None:
                for desc in (
                    "Young's modulus and Poisson's ratio",
                    "Youngs modulus and Poissons ratio",
                    "Linear Elastic Material",
                ):
                    try:
                        enu_group = mat.propertyGroup().create("Enu", desc)
                        break
                    except Exception:
                        continue
            if enu_group is not None:
                for young_key in ("E", "youngsmodulus"):
                    try:
                        enu_group.set(young_key, young_expr)
                    except Exception:
                        continue
                for nu_key in ("nu", "poissonsratio"):
                    try:
                        enu_group.set(nu_key, nu_expr)
                    except Exception:
                        continue
        except Exception as mat_struct_exc:
            logger.warning("  ⚠ 结构材料参数写入失败，结构场可能回退: %s", mat_struct_exc)

    def _get_or_generate_step_file(self, request) -> Path:
        """
        Resolve existing STEP file from request or generate one on-demand.
        """
        if "step_file" in request.parameters:
            step_file = Path(request.parameters["step_file"])
            if step_file.exists():
                logger.info(f"  使用提供的 STEP 文件: {step_file}")
                return step_file

        logger.info("  即时生成 STEP 文件（使用 OpenCASCADE）...")

        try:
            from geometry.cad_export_occ import export_design_occ
        except ImportError:
            logger.error("  ✗ 无法导入 cad_export_occ 模块")
            raise SimulationError("STEP 导出模块不可用。请确保 geometry/cad_export_occ.py 存在。")

        runtime_dir = Path("experiments/runtime/comsol_dynamic")
        runtime_dir.mkdir(parents=True, exist_ok=True)

        step_file = runtime_dir / f"design_iter_{request.design_state.iteration}.step"
        try:
            export_design_occ(request.design_state, str(step_file))
            logger.info(f"  ✓ STEP 文件已生成（OpenCASCADE）: {step_file}")
        except Exception as exc:
            logger.error(f"  ✗ STEP 文件生成失败: {exc}")
            raise SimulationError(f"STEP 文件生成失败: {exc}")
        return step_file

    def _create_dynamic_model(self, step_file: Path, design_state):
        """
        Build a fresh COMSOL model from STEP geometry.
        """
        try:
            if self.model is not None:
                logger.info("  清理旧模型...")
                try:
                    if self.client is not None:
                        self.client.remove(self.model)
                except Exception as cleanup_error:
                    logger.warning(f"  ⚠ 旧模型移除失败，将继续创建新模型: {cleanup_error}")
                finally:
                    self.model = None

            logger.info("  [1/6] 创建空模型...")
            self.model = self.client.create("DynamicThermalModel")

            logger.info(f"  [2/6] 导入 STEP 文件: {step_file}")
            abs_step_path = os.path.abspath(step_file).replace("\\", "/")
            logger.info(f"  转换后的绝对路径: {abs_step_path}")
            if not os.path.exists(abs_step_path):
                raise FileNotFoundError(f"STEP 文件不存在: {abs_step_path}")

            geom = self.model.java.geom().create("geom1", 3)
            import_node = geom.feature().create("imp1", "Import")
            import_node.set("filename", abs_step_path)
            logger.info("  设置 STEP 文件路径完成")

            try:
                logger.info("  执行导入节点...")
                geom.run("imp1")
                logger.info("  ✓ 导入节点执行成功")
            except Exception as import_node_error:
                logger.error(f"  ✗ 导入节点执行失败: {import_node_error}")
                logger.error(f"  Java 异常详情: {str(import_node_error)}")
                return SimulationResult(
                    success=False,
                    metrics={"max_temp": 9999.0, "min_temp": 9999.0, "avg_temp": 9999.0, "temp_gradient": 0.0},
                    violations=[],
                    error_message=f"COMSOL 导入节点执行失败: {str(import_node_error)}",
                )

            try:
                logger.info("  执行几何序列...")
                geom.run()
                logger.info("  ✓ 几何序列执行成功")
            except Exception as geom_run_error:
                logger.error(f"  ✗ 几何序列执行失败: {geom_run_error}")
                logger.error(f"  Java 异常详情: {str(geom_run_error)}")
                return SimulationResult(
                    success=False,
                    metrics={"max_temp": 9999.0, "min_temp": 9999.0, "avg_temp": 9999.0, "temp_gradient": 0.0},
                    violations=[],
                    error_message=f"COMSOL 几何序列执行失败: {str(geom_run_error)}",
                )

            try:
                num_domains = geom.getNDomains()
                logger.info(f"  检测到 {num_domains} 个几何域")
                if num_domains == 0:
                    raise ValueError(f"STEP 导入失败: 从 {abs_step_path} 生成了 0 个域")
                logger.info(f"  ✓ STEP 几何导入成功: {num_domains} 个域")
            except Exception as domain_check_error:
                logger.error(f"  ✗ 几何域校验失败: {domain_check_error}")
                return SimulationResult(
                    success=False,
                    metrics={"max_temp": 9999.0, "min_temp": 9999.0, "avg_temp": 9999.0, "temp_gradient": 0.0},
                    violations=[],
                    error_message=f"COMSOL 几何域校验失败: {str(domain_check_error)}",
                )

            logger.info("  [3/6] 创建热传导物理场...")
            ht = self.model.java.physics().create("ht", "HeatTransfer", "geom1")

            mat = self.model.java.material().create("mat1", "Common")
            mat.label("Aluminum Alloy (Default)")
            self._apply_multiphysics_material_defaults(
                mat,
                emissivity=0.8,
                set_structural=True,
                set_electrical=True,
            )
            mat.propertyGroup("def").set("ks", "167[W/(m*K)]")
            mat.selection().all()
            logger.info("  ✓ 材料已应用到所有域")

            self.model.java.param().set("P_scale", "0.01")
            logger.info("  ✓ 全局参数 P_scale 已设置: 0.01 (1% 功率)")

            logger.info("  [3.5/7] 建立全局默认导热网络...")
            try:
                thin_layer = ht.feature().create("tl_global", "ThinLayer")
                thin_layer.selection().geom("geom1", 2)
                thin_layer.selection().set([])
                thin_layer.selection().all()
                d_gap = 0.1
                thin_layer.set("ds", f"{d_gap}[mm]")
                try:
                    thin_layer.set("ks_mat", "userdef")
                except Exception:
                    pass
                thin_layer.set("ks", "167[W/(m*K)]")
                logger.info(f"      ✓ 全局默认导热网络已建立: ds={d_gap} mm, ks=167 W/(m*K)")
            except Exception as exc:
                logger.warning(f"      ⚠️ 全局导热网络创建失败（非致命）: {exc}")

            logger.info("  [4/6] 创建 Box Selection 并赋予热源...")
            self._last_heat_binding_report = self._assign_heat_sources_dynamic(design_state, ht, geom)

            logger.info("  [4.5/7] 应用组件级热学属性...")
            self._apply_thermal_properties_dynamic(design_state, ht, geom)

            logger.info("  [5/7] 识别外部边界并赋予辐射条件...")
            self._assign_radiation_boundaries_dynamic(design_state, ht, geom)

            logger.info("  [5.5/7] 配置电学场求解支路...")
            self._power_setup_ok = self._configure_power_multiphysics(
                design_state=design_state,
                ht=ht,
            )

            logger.info("  [5.6/7] 配置结构场求解支路...")
            self._structural_setup_ok = self._configure_structural_multiphysics()

            logger.info("  [5.7/7] 配置热-结构-电耦合支路...")
            self._coupled_setup_ok = self._configure_coupled_multiphysics()

            logger.info("  [6/7] 创建自动网格...")
            try:
                mesh = self.model.java.mesh().create("mesh1", "geom1")
                mesh.autoMeshSize(5)
                logger.info("  执行网格划分...")
                mesh.run()
                logger.info("  ✓ 网格生成成功")
            except Exception as mesh_error:
                logger.error(f"  ✗ 网格生成失败: {mesh_error}")
                logger.error(f"  Java 异常详情: {str(mesh_error)}")
                return SimulationResult(
                    success=False,
                    metrics={"max_temp": 9999.0, "min_temp": 9999.0, "avg_temp": 9999.0, "temp_gradient": 0.0},
                    violations=[],
                    error_message=f"COMSOL 网格生成失败: {str(mesh_error)}",
                )
            logger.info("  ✓ 网格生成成功")

            study = self.model.java.study().create("std1")
            study.feature().create("stat", "Stationary")
            self._set_study_step_activation(
                study_tag="std1",
                step_tag="stat",
                activation={
                    "ht": True,
                    "ec": False,
                    "solid": False,
                },
            )

            logger.info("  [7/7] 配置求解器...")
            ht.feature("init1").set("Tinit", "293.15[K]")
            logger.info("  ✓ 求解器配置完成: 使用 COMSOL 默认配置, 初始温度=293.15K")
            logger.info("  ✓ 动态模型创建完成")
        except Exception as exc:
            logger.error(f"动态模型创建失败: {exc}", exc_info=True)
            raise SimulationError(f"动态模型创建失败: {exc}")

    def _assign_radiation_boundaries_dynamic(
        self,
        design_state,
        ht: Any,
        geom: Any,
    ):
        """
        Select outer boundaries and apply stable boundary conditions.
        """
        _ = geom
        logger.info("    - 创建外部辐射边界...")

        margin = 20.0
        if design_state.components:
            x_min = min(c.position.x - c.dimensions.x / 2 for c in design_state.components) - margin
            x_max = max(c.position.x + c.dimensions.x / 2 for c in design_state.components) + margin
            y_min = min(c.position.y - c.dimensions.y / 2 for c in design_state.components) - margin
            y_max = max(c.position.y + c.dimensions.y / 2 for c in design_state.components) + margin
            z_min = min(c.position.z - c.dimensions.z / 2 for c in design_state.components) - margin
            z_max = max(c.position.z + c.dimensions.z / 2 for c in design_state.components) + margin
        else:
            env = design_state.envelope.outer_size
            x_min, x_max = -env.x / 2 - margin, env.x / 2 + margin
            y_min, y_max = -env.y / 2 - margin, env.y / 2 + margin
            z_min, z_max = -env.z / 2 - margin, env.z / 2 + margin

        sel_name = "boxsel_outer_boundary"
        box_sel = self.model.java.selection().create(sel_name, "Box")
        box_sel.set("entitydim", "2")
        box_sel.set("xmin", f"{x_min}[mm]")
        box_sel.set("xmax", f"{x_max}[mm]")
        box_sel.set("ymin", f"{y_min}[mm]")
        box_sel.set("ymax", f"{y_max}[mm]")
        box_sel.set("zmin", f"{z_min}[mm]")
        box_sel.set("zmax", f"{z_max}[mm]")
        box_sel.set("condition", "intersects")
        logger.info(
            f"      外边界选择框: X[{x_min:.1f},{x_max:.1f}] "
            f"Y[{y_min:.1f},{y_max:.1f}] Z[{z_min:.1f},{z_max:.1f}] mm"
        )

        selected_entities = []
        missing_anchor_components = []
        try:
            selected_entities = list(box_sel.entities())
            selected_set = set(selected_entities)
            for i, comp in enumerate(design_state.components):
                check_sel = f"boxsel_outer_check_{i}"
                self._create_component_box_selection(comp, check_sel, entity_dim=2, condition="intersects")
                comp_entities = list(self.model.java.selection(check_sel).entities())
                if comp_entities and selected_set.isdisjoint(comp_entities):
                    missing_anchor_components.append(comp.id)
        except Exception as exc:
            logger.warning(f"      外边界选择校验失败，将回退到全边界锚点: {exc}")
            missing_anchor_components = [c.id for c in design_state.components]

        temp_bc = ht.feature().create("temp1", "TemperatureBoundary")
        t_surface = 273.15
        temp_bc.set("T0", f"{t_surface}[K]")
        logger.info(f"      ✓ 温度边界已设置: T_surface={t_surface}K")

        logger.info("    - 添加数值稳定锚（微弱对流边界）...")
        conv_bc = ht.feature().create("conv_stabilizer", "HeatFluxBoundary")
        h_stabilizer = 0.1
        t_ambient = 293.15
        conv_bc.set("q0", f"{h_stabilizer}[W/(m^2*K)]*({t_ambient}[K]-T)")

        if selected_entities and not missing_anchor_components:
            temp_bc.selection().named(sel_name)
            conv_bc.selection().named(sel_name)
            logger.info(f"      ✓ 外边界锚点已绑定: {len(selected_entities)} 个边界实体")
        else:
            temp_bc.selection().all()
            conv_bc.selection().all()
            logger.warning(
                "      ⚠ 外边界锚点存在漏选，回退到全边界锚点。"
                f" 漏锚组件: {missing_anchor_components if missing_anchor_components else '未知'}"
            )

        logger.info(f"      ✓ 数值稳定锚已设置: h={h_stabilizer} W/(m^2*K), T_ambient={t_ambient}K")

    def _create_component_box_selection(
        self,
        comp,
        sel_name: str,
        entity_dim: int = 3,
        condition: str = "inside",
    ):
        """
        Create per-component Box Selection.
        """
        pos = comp.position
        dim = comp.dimensions
        tolerance = 1e-3

        x_min = pos.x - dim.x / 2 - tolerance
        x_max = pos.x + dim.x / 2 + tolerance
        y_min = pos.y - dim.y / 2 - tolerance
        y_max = pos.y + dim.y / 2 + tolerance
        z_min = pos.z - dim.z / 2 - tolerance
        z_max = pos.z + dim.z / 2 + tolerance

        box_sel = self.model.java.selection().create(sel_name, "Box")
        box_sel.set("entitydim", str(entity_dim))
        box_sel.set("xmin", f"{x_min}[mm]")
        box_sel.set("xmax", f"{x_max}[mm]")
        box_sel.set("ymin", f"{y_min}[mm]")
        box_sel.set("ymax", f"{y_max}[mm]")
        box_sel.set("zmin", f"{z_min}[mm]")
        box_sel.set("zmax", f"{z_max}[mm]")
        box_sel.set("condition", condition)
