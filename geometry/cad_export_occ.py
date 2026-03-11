#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CAD导出模块 (OpenCASCADE版本) - DV2.0 动态几何实体生成

使用 pythonocc-core 生成符合 ISO 10303-21 标准的完整 STEP 文件，
当前最小切片支持：
- 目录件几何合同适配 (`CatalogComponentSpec`)
- shell/panel/aperture 合同
- STEP 阶段 aperture Boolean cut
- 形状族：box / cylinder / frustum / ellipsoid / extruded_profile / composite_primitive
- heatsink / bracket 附体

安装依赖:
    conda install -c conda-forge pythonocc-core
"""

import logging
import math
import json
from pathlib import Path
from typing import Optional, List, Tuple, Any, Dict

from core.protocol import DesignState, ComponentGeometry
from core.exceptions import GeometryError
from geometry.catalog_geometry import (
    CatalogComponentSpec,
    GeometryProfileSpec,
    PROFILE_KIND_BOX,
    PROFILE_KIND_COMPOSITE,
    PROFILE_KIND_CYLINDER,
    PROFILE_KIND_ELLIPSOID,
    PROFILE_KIND_EXTRUDED,
    PROFILE_KIND_FRUSTUM,
    resolve_catalog_component_spec,
)
from geometry.geometry_proxy import build_geometry_proxy_manifest
from geometry.shell_spec import (
    ApertureSiteSpec,
    PanelSpec,
    ShellSpec,
    plan_box_panel_aperture,
    plan_box_panel_variant,
    resolve_shell_spec,
)

logger = logging.getLogger(__name__)


class OCCSTEPExporter:
    """
    基于 OpenCASCADE 的 STEP 导出器 (DV2.0)

    支持动态生成：
    - 目录件精确几何（box/cylinder/frustum/ellipsoid/extruded_profile/composite_primitive）
    - shell/panel/aperture 最小合同
    - 散热窗/板附加几何体
    - 结构支架几何体
    """

    def __init__(self):
        """初始化 OCC STEP 导出器"""
        self.last_geometry_manifest: List[Dict[str, Any]] = []
        try:
            # 导入 pythonocc-core 模块
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
            from OCC.Core.gp import gp_Pnt, gp_Ax2, gp_Dir, gp_Trsf, gp_Vec
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
            from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
            from OCC.Core.IFSelect import IFSelect_RetDone
            from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse

            self.occ_available = True
            logger.info("✓ pythonocc-core 可用，将生成真实 STEP 文件")

        except ImportError as e:
            self.occ_available = False
            logger.warning(f"⚠ pythonocc-core 不可用: {e}")
            logger.warning("  将回退到简化 STEP 导出（COMSOL 无法导入）")
            logger.warning("  安装方法: conda install -c conda-forge pythonocc-core")

    def export(self, design_state: DesignState, output_path: str) -> bool:
        """
        导出设计状态为 STEP 文件 (DV2.0: 支持动态几何生成)

        Args:
            design_state: 设计状态
            output_path: 输出文件路径

        Returns:
            是否成功
        """
        if not self.occ_available:
            raise GeometryError(
                "pythonocc-core 未安装，无法生成真实 STEP 文件。\n"
                "安装方法: conda install -c conda-forge pythonocc-core"
            )

        try:
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
            from OCC.Core.gp import gp_Pnt, gp_Ax2, gp_Dir, gp_Trsf, gp_Vec
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
            from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
            from OCC.Core.IFSelect import IFSelect_RetDone
            from OCC.Core.Interface import Interface_Static
            from OCC.Core.TopoDS import TopoDS_Compound
            from OCC.Core.BRep import BRep_Builder

            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            logger.info(f"开始生成 STEP 文件 (DV2.0): {output_path}")
            logger.info(f"  组件数量: {len(design_state.components)}")
            self.last_geometry_manifest = []

            # 创建 STEP 写入器
            step_writer = STEPControl_Writer()
            Interface_Static.SetCVal("write.step.schema", "AP214")  # 使用 AP214 协议

            # 创建复合体（Compound）来容纳所有组件
            compound = TopoDS_Compound()
            builder = BRep_Builder()
            builder.MakeCompound(compound)

            # 统计动态几何生成
            heatsink_count = 0
            bracket_count = 0
            shape_family_counts: Dict[str, int] = {}
            shell_added = False

            shell_shape = self._create_enclosure_shell_shape(design_state)
            if shell_shape is not None:
                builder.Add(compound, shell_shape)
                shell_added = True
                logger.info("  ✓ 舱体机壳已加入 STEP 几何（带厚度空腔）")

            # 为每个组件创建 BREP 实体
            for i, comp in enumerate(design_state.components):
                logger.info(f"  [{i+1}/{len(design_state.components)}] 创建组件: {comp.id}")

                # === 1. 创建主体几何 (支持 Box 和 Cylinder) ===
                catalog_spec = resolve_catalog_component_spec(comp, design_state)
                main_shape = self._create_component_shape(comp, catalog_spec)
                builder.Add(compound, main_shape)

                profile_kind = catalog_spec.geometry_profile.normalized_kind()
                shape_family_counts[profile_kind] = shape_family_counts.get(profile_kind, 0) + 1
                self.last_geometry_manifest.append(
                    {
                        "id": comp.id,
                        "role": "component",
                        "geometry_kind": profile_kind,
                        "proxy_kind": catalog_spec.resolved_proxy().normalized_kind(),
                        "size_mm": list(catalog_spec.geometry_profile.approximate_size_mm()),
                    }
                )

                logger.info(
                    "    ✓ 主体: %s, 位置: (%.2f, %.2f, %.2f)",
                    profile_kind,
                    comp.position.x,
                    comp.position.y,
                    comp.position.z,
                )

                # === 2. 创建散热器几何 (ADD_HEATSINK) ===
                heatsink_params = getattr(comp, 'heatsink', None)
                if heatsink_params:
                    heatsink_shape = self._create_heatsink(comp, heatsink_params)
                    if heatsink_shape:
                        builder.Add(compound, heatsink_shape)
                        heatsink_count += 1
                        self.last_geometry_manifest.append(
                            {
                                "id": f"{comp.id}:heatsink",
                                "role": "appendage",
                                "parent_id": comp.id,
                                "appendage_kind": "heatsink",
                                "face": heatsink_params.get("face", "+Y"),
                            }
                        )
                        logger.info(f"    ✓ 散热器: face={heatsink_params.get('face', '+Y')}, thickness={heatsink_params.get('thickness', 2.0)}mm")

                # === 3. 创建支架几何 (ADD_BRACKET) ===
                bracket_params = getattr(comp, 'bracket', None)
                if bracket_params:
                    bracket_shape = self._create_bracket(comp, bracket_params)
                    if bracket_shape:
                        builder.Add(compound, bracket_shape)
                        bracket_count += 1
                        self.last_geometry_manifest.append(
                            {
                                "id": f"{comp.id}:bracket",
                                "role": "appendage",
                                "parent_id": comp.id,
                                "appendage_kind": "bracket",
                                "attach_face": bracket_params.get("attach_face", "-Z"),
                            }
                        )
                        logger.info(f"    ✓ 支架: height={bracket_params.get('height', 20.0)}mm")

            # 将复合体写入 STEP 文件
            logger.info("  写入 STEP 文件...")
            step_writer.Transfer(compound, STEPControl_AsIs)
            status = step_writer.Write(str(output_file))

            if status != IFSelect_RetDone:
                raise GeometryError(f"STEP 写入失败，状态码: {status}")

            manifest_path = self._write_geometry_manifest(output_file)
            proxy_manifest_path = self._write_geometry_proxy_manifest(output_file, design_state)

            logger.info(f"✓ STEP 文件生成成功: {output_path}")
            logger.info(f"  文件大小: {output_file.stat().st_size / 1024:.2f} KB")
            logger.info(
                "  动态几何: %d 散热器, %d 支架, shell=%s, 形状族=%s",
                heatsink_count,
                bracket_count,
                "on" if shell_added else "off",
                dict(sorted(shape_family_counts.items())),
            )
            logger.info("  geometry manifest: %s", manifest_path)
            logger.info("  geometry proxy manifest: %s", proxy_manifest_path)

            return True

        except ImportError as e:
            raise GeometryError(f"pythonocc-core 导入失败: {e}")
        except Exception as e:
            logger.error(f"STEP 导出失败: {e}", exc_info=True)
            raise GeometryError(f"STEP 导出失败: {e}")

    def _create_enclosure_shell_shape(self, design_state: DesignState) -> Any:
        shell_spec = resolve_shell_spec(design_state)
        if shell_spec is None:
            return None

        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut

        outer_kind = shell_spec.outer_profile.normalized_kind()
        outer_size = shell_spec.outer_size_mm()
        thickness = float(shell_spec.thickness_mm or 0.0)
        if outer_kind not in {PROFILE_KIND_BOX, PROFILE_KIND_CYLINDER, PROFILE_KIND_FRUSTUM}:
            logger.warning("  ⚠ 最小 shell 实现仅支持 box/cylinder/frustum shell，收到 %s，退化为实体外轮廓", outer_kind)
            shell_shape = self._build_shape_from_profile(shell_spec.outer_profile)
            self.last_geometry_manifest.append(
                {
                    "id": shell_spec.shell_id,
                    "role": "shell",
                    "geometry_kind": outer_kind,
                    "aperture_count": 0,
                    "notes": ["shell_cavity_not_applied_for_non_box_shape"],
                }
            )
            return shell_shape

        outer_x, outer_y, outer_z = outer_size
        inner_x = outer_x - 2.0 * thickness
        inner_y = outer_y - 2.0 * thickness
        inner_z = outer_z - 2.0 * thickness
        if min(outer_x, outer_y, outer_z) <= 0.0 or min(inner_x, inner_y, inner_z) <= 0.0:
            return None

        if outer_kind == PROFILE_KIND_CYLINDER:
            outer_radius = float(shell_spec.outer_profile.radius_mm or min(outer_x, outer_y) / 2.0)
            outer_height = float(shell_spec.outer_profile.height_mm or outer_z)
            inner_radius = outer_radius - thickness
            inner_height = outer_height - 2.0 * thickness
            if min(outer_radius, outer_height, inner_radius, inner_height) <= 0.0:
                return None
            logger.info(
                "  创建圆柱舱体机壳: outer=(r=%.1f, h=%.1f) mm, inner=(r=%.1f, h=%.1f) mm, thickness=%.1f mm",
                outer_radius,
                outer_height,
                inner_radius,
                inner_height,
                thickness,
            )
            outer_shell = self._build_cylinder_centered(outer_radius, outer_height)
            inner_shell = self._build_cylinder_centered(inner_radius, inner_height)
            shell_shape = BRepAlgoAPI_Cut(outer_shell, inner_shell).Shape()
        elif outer_kind == PROFILE_KIND_FRUSTUM:
            outer_bottom_radius = float(shell_spec.outer_profile.bottom_radius_mm or min(outer_x, outer_y) / 2.0)
            outer_top_radius = float(shell_spec.outer_profile.top_radius_mm or outer_bottom_radius)
            outer_height = float(shell_spec.outer_profile.height_mm or outer_z)
            inner_bottom_radius = outer_bottom_radius - thickness
            inner_top_radius = outer_top_radius - thickness
            inner_height = outer_height - 2.0 * thickness
            if min(outer_bottom_radius, outer_top_radius, outer_height, inner_bottom_radius, inner_top_radius, inner_height) <= 0.0:
                return None
            logger.info(
                "  创建截锥舱体机壳: outer=(rb=%.1f, rt=%.1f, h=%.1f) mm, inner=(rb=%.1f, rt=%.1f, h=%.1f) mm, thickness=%.1f mm",
                outer_bottom_radius,
                outer_top_radius,
                outer_height,
                inner_bottom_radius,
                inner_top_radius,
                inner_height,
                thickness,
            )
            outer_shell = self._build_frustum_centered(outer_bottom_radius, outer_top_radius, outer_height)
            inner_shell = self._build_frustum_centered(inner_bottom_radius, inner_top_radius, inner_height)
            shell_shape = BRepAlgoAPI_Cut(outer_shell, inner_shell).Shape()
        else:
            logger.info(
                "  创建舱体机壳: outer=(%.1f, %.1f, %.1f) mm, inner=(%.1f, %.1f, %.1f) mm, thickness=%.1f mm",
                outer_x,
                outer_y,
                outer_z,
                inner_x,
                inner_y,
                inner_z,
                thickness,
            )
            outer_box = self._build_box_centered((outer_x, outer_y, outer_z))
            inner_box = self._build_box_centered((inner_x, inner_y, inner_z))
            shell_shape = BRepAlgoAPI_Cut(outer_box, inner_box).Shape()

        shell_shape = self._apply_panel_variants(shell_shape, shell_spec)
        shell_shape, aperture_count = self._apply_aperture_cutouts(shell_shape, shell_spec)

        self.last_geometry_manifest.append(
            {
                "id": shell_spec.shell_id,
                "role": "shell",
                "geometry_kind": outer_kind,
                "size_mm": list(outer_size),
                "aperture_count": aperture_count,
                "panel_count": len(shell_spec.resolved_panels()),
            }
        )
        for panel in shell_spec.resolved_panels():
            self.last_geometry_manifest.append(
                {
                    "id": panel.panel_id,
                    "role": "panel",
                    "panel_face": panel.normalized_face(),
                    "span_mm": list(panel.span_mm) if panel.span_mm is not None else None,
                    "active_variant": panel.active_variant,
                    "surface_semantics": list(panel.surface_semantics or []),
                }
            )
        return shell_shape

    def _apply_panel_variants(self, shell_shape: Any, shell_spec: ShellSpec) -> Any:
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse

        active_variants = []
        for panel in shell_spec.resolved_panels():
            if not panel.active_variant:
                continue
            active_variants.append(panel.active_variant)
            variant_plan = plan_box_panel_variant(shell_spec=shell_spec, panel=panel)
            if variant_plan is None:
                logger.warning("  ⚠ panel %s 的 active_variant=%s 当前无可执行几何", panel.panel_id, panel.active_variant)
                continue
            variant_profile = variant_plan.get("profile")
            if not isinstance(variant_profile, GeometryProfileSpec):
                logger.warning("  ⚠ panel variant kind=%s 缺少 GeometryProfileSpec", variant_plan["variant_kind"])
                continue
            variant_shape = self._build_shape_from_profile(variant_profile)
            variant_shape = self._orient_shape_from_local_z(variant_shape, variant_plan["panel_face"])
            variant_shape = self._translate_shape(variant_shape, variant_plan["center_mm"])
            shell_shape = BRepAlgoAPI_Fuse(shell_shape, variant_shape).Shape()
            self.last_geometry_manifest.append(
                {
                    "id": f"{panel.panel_id}:{variant_plan['variant_id']}",
                    "role": "panel_variant",
                    "panel_id": panel.panel_id,
                    "panel_face": variant_plan["panel_face"],
                    "variant_id": variant_plan["variant_id"],
                    "variant_kind": variant_plan["variant_kind"],
                    "profile_kind": variant_plan.get("profile_kind"),
                    "center_mm": list(variant_plan["center_mm"]),
                    "size_mm": list(variant_plan["size_mm"]),
                    "local_size_mm": list(variant_plan.get("local_size_mm", variant_plan["size_mm"])),
                }
            )
        if active_variants:
            logger.info("  面板变体已应用: %s", active_variants)
        return shell_shape

    def _apply_aperture_cutouts(self, shell_shape: Any, shell_spec: ShellSpec) -> Tuple[Any, int]:
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut

        applied = 0
        panel_index = shell_spec.panel_index()
        for aperture in shell_spec.aperture_sites:
            if not aperture.enabled:
                continue
            if aperture.normalized_shape() not in {"rectangular_cutout", "circular_cutout", "profile_cutout"}:
                logger.warning(
                    "  ⚠ aperture %s 尚未实现 %s，仅支持 rectangular_cutout/circular_cutout/profile_cutout",
                    aperture.aperture_id,
                    aperture.shape,
                )
                continue
            panel = panel_index.get(aperture.panel_id)
            if panel is None:
                logger.warning("  ⚠ aperture %s 引用了未知 panel_id=%s", aperture.aperture_id, aperture.panel_id)
                continue
            cut_plan = self._plan_box_panel_aperture_cutout(shell_spec=shell_spec, panel=panel, aperture=aperture)
            if cut_plan is None:
                continue
            cutout_shape = self._build_aperture_cutout_shape(cut_plan)
            cutout_shape = self._translate_shape(cutout_shape, cut_plan["center_mm"])
            shell_shape = BRepAlgoAPI_Cut(shell_shape, cutout_shape).Shape()
            applied += 1
            self.last_geometry_manifest.append(
                {
                    "id": aperture.aperture_id,
                    "role": "aperture",
                    "panel_id": panel.panel_id,
                    "panel_face": cut_plan["panel_face"],
                    "shape_kind": cut_plan["shape_kind"],
                    "size_mm": list(cut_plan["size_mm"]),
                    "center_mm": list(cut_plan["center_mm"]),
                }
            )
            logger.info(
                "    ✓ aperture cutout: %s -> panel=%s face=%s shape=%s size=%s center=%s",
                aperture.aperture_id,
                panel.panel_id,
                cut_plan["panel_face"],
                cut_plan["shape_kind"],
                tuple(round(value, 2) for value in cut_plan["size_mm"]),
                tuple(round(value, 2) for value in cut_plan["center_mm"]),
            )
        return shell_shape, applied

    @staticmethod
    def _plan_box_panel_aperture_cutout(
        *,
        shell_spec: ShellSpec,
        panel: PanelSpec,
        aperture: ApertureSiteSpec,
    ) -> Optional[Dict[str, Any]]:
        return plan_box_panel_aperture(
            shell_spec=shell_spec,
            panel=panel,
            aperture=aperture,
            mode="cutout",
        )

    def _build_aperture_cutout_shape(self, cut_plan: Dict[str, Any]) -> Any:
        shape_kind = str(cut_plan.get("shape_kind", "rectangular_cutout")).strip().lower()
        if shape_kind == "circular_cutout":
            radius = float(cut_plan.get("radius_mm", 0.0) or 0.0)
            axis = str(cut_plan.get("axis", "z") or "z").strip().lower()
            depth_axis = {"x": 0, "y": 1, "z": 2}.get(axis, 2)
            height = float(cut_plan["size_mm"][depth_axis])
            return self._build_cylinder_along_axis_centered(radius, height, axis)
        if shape_kind == "profile_cutout":
            local_size_mm = tuple(float(value) for value in cut_plan.get("local_size_mm", (0.0, 0.0, 0.0)))
            cutout_shape = self._build_extruded_profile_centered(
                list(cut_plan.get("profile_points_mm", []) or []),
                max(local_size_mm[2], 1e-3),
            )
            return self._orient_shape_from_local_z(cutout_shape, str(cut_plan.get("panel_face", "+Z")))
        return self._build_box_centered(cut_plan["size_mm"])

    def _create_component_shape(self, comp: ComponentGeometry, catalog_spec: CatalogComponentSpec) -> Any:
        """
        创建组件主体几何，优先使用 CatalogComponentSpec，兼容旧 ComponentGeometry。

        Args:
            comp: 组件几何信息
            catalog_spec: 目录件几何真值；缺省时由旧 ComponentGeometry 适配得到

        Returns:
            TopoDS_Shape 实体
        """
        profile = catalog_spec.geometry_profile
        shape = self._build_shape_from_profile(profile)
        return self._translate_shape(shape, (comp.position.x, comp.position.y, comp.position.z))

    def _build_shape_from_profile(self, profile: GeometryProfileSpec) -> Any:
        kind = profile.normalized_kind()
        size_mm = profile.approximate_size_mm()

        if kind == PROFILE_KIND_CYLINDER:
            radius = float(profile.radius_mm or min(size_mm[0], size_mm[1]) / 2.0)
            height = float(profile.height_mm or size_mm[2])
            return self._build_cylinder_centered(radius, height)

        if kind == PROFILE_KIND_FRUSTUM:
            bottom_radius = float(profile.bottom_radius_mm or min(size_mm[0], size_mm[1]) / 2.0)
            top_radius = float(profile.top_radius_mm or bottom_radius)
            height = float(profile.height_mm or size_mm[2])
            return self._build_frustum_centered(bottom_radius, top_radius, height)

        if kind == PROFILE_KIND_ELLIPSOID:
            if profile.semi_axes_mm is not None:
                semi_axes = tuple(float(value) for value in profile.semi_axes_mm)
            else:
                semi_axes = (size_mm[0] / 2.0, size_mm[1] / 2.0, size_mm[2] / 2.0)
            return self._build_ellipsoid_centered(semi_axes)

        if kind == PROFILE_KIND_EXTRUDED:
            return self._build_extruded_profile_centered(profile.profile_points_mm, float(profile.depth_mm or size_mm[2]))

        if kind == PROFILE_KIND_COMPOSITE:
            return self._build_composite_primitive_centered(profile)

        return self._build_box_centered(size_mm)

    def _build_box_centered(self, size_mm: Tuple[float, float, float]) -> Any:
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCC.Core.gp import gp_Pnt

        size_x = max(float(size_mm[0]), 1e-3)
        size_y = max(float(size_mm[1]), 1e-3)
        size_z = max(float(size_mm[2]), 1e-3)
        return BRepPrimAPI_MakeBox(
            gp_Pnt(-size_x / 2.0, -size_y / 2.0, -size_z / 2.0),
            size_x,
            size_y,
            size_z,
        ).Shape()

    def _build_cylinder_centered(self, radius: float, height: float) -> Any:
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder

        cylinder = BRepPrimAPI_MakeCylinder(max(radius, 1e-3), max(height, 1e-3)).Shape()
        return self._translate_shape(cylinder, (0.0, 0.0, -max(height, 1e-3) / 2.0))

    def _build_cylinder_along_axis_centered(self, radius: float, height: float, axis: str) -> Any:
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCC.Core.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

        cylinder = self._build_cylinder_centered(radius, height)
        normalized_axis = str(axis or "z").strip().lower()
        if normalized_axis == "z":
            return cylinder

        trsf = gp_Trsf()
        if normalized_axis == "x":
            trsf.SetRotation(gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 1.0, 0.0)), math.pi / 2.0)
        elif normalized_axis == "y":
            trsf.SetRotation(gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(1.0, 0.0, 0.0)), -math.pi / 2.0)
        else:
            return cylinder
        return BRepBuilderAPI_Transform(cylinder, trsf, True).Shape()

    def _build_frustum_centered(self, bottom_radius: float, top_radius: float, height: float) -> Any:
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCone

        frustum = BRepPrimAPI_MakeCone(max(bottom_radius, 1e-3), max(top_radius, 1e-3), max(height, 1e-3)).Shape()
        return self._translate_shape(frustum, (0.0, 0.0, -max(height, 1e-3) / 2.0))

    def _build_ellipsoid_centered(self, semi_axes_mm: Tuple[float, float, float]) -> Any:
        try:
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeSphere
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_GTransform
            from OCC.Core.gp import gp_GTrsf, gp_Pnt

            sphere = BRepPrimAPI_MakeSphere(gp_Pnt(0.0, 0.0, 0.0), 1.0).Shape()
            transform = gp_GTrsf()
            transform.SetValue(1, 1, max(float(semi_axes_mm[0]), 1e-3))
            transform.SetValue(2, 2, max(float(semi_axes_mm[1]), 1e-3))
            transform.SetValue(3, 3, max(float(semi_axes_mm[2]), 1e-3))
            return BRepBuilderAPI_GTransform(sphere, transform, True).Shape()
        except Exception as exc:
            logger.warning("  ⚠ ellipsoid 真实 OCC 变换失败，退化为 box proxy: %s", exc)
            return self._build_box_centered(
                (
                    max(float(semi_axes_mm[0]), 1e-3) * 2.0,
                    max(float(semi_axes_mm[1]), 1e-3) * 2.0,
                    max(float(semi_axes_mm[2]), 1e-3) * 2.0,
                )
            )

    def _build_extruded_profile_centered(self, profile_points_mm: List[Tuple[float, float]], depth_mm: float) -> Any:
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCC.Core.gp import gp_Pnt, gp_Vec

        if len(profile_points_mm) < 3:
            logger.warning("  ⚠ extruded_profile 缺少足够剖面点，退化为 box")
            return self._build_box_centered((1.0, 1.0, max(depth_mm, 1e-3)))

        polygon = BRepBuilderAPI_MakePolygon()
        for point_x, point_y in profile_points_mm:
            polygon.Add(gp_Pnt(float(point_x), float(point_y), 0.0))
        polygon.Close()
        face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
        prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, max(depth_mm, 1e-3))).Shape()
        return self._translate_shape(prism, (0.0, 0.0, -max(depth_mm, 1e-3) / 2.0))

    def _build_composite_primitive_centered(self, profile: GeometryProfileSpec) -> Any:
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse

        combined_shape = None
        for child in profile.children:
            child_shape = self._build_shape_from_profile(child.profile)
            child_shape = self._translate_shape(child_shape, child.offset_mm)
            if combined_shape is None:
                combined_shape = child_shape
            else:
                combined_shape = BRepAlgoAPI_Fuse(combined_shape, child_shape).Shape()

        if combined_shape is None:
            logger.warning("  ⚠ composite_primitive 无子节点，退化为 box proxy")
            return self._build_box_centered(profile.approximate_size_mm())
        return combined_shape

    def _translate_shape(self, shape: Any, translation_mm: Tuple[float, float, float]) -> Any:
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCC.Core.gp import gp_Trsf, gp_Vec

        trsf = gp_Trsf()
        trsf.SetTranslation(
            gp_Vec(
                float(translation_mm[0]),
                float(translation_mm[1]),
                float(translation_mm[2]),
            )
        )
        return BRepBuilderAPI_Transform(shape, trsf, True).Shape()

    def _orient_shape_from_local_z(self, shape: Any, panel_face: str) -> Any:
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCC.Core.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

        face = str(panel_face or "+Z").strip().upper()
        trsf = gp_Trsf()
        origin = gp_Pnt(0.0, 0.0, 0.0)
        if face == "+Z":
            return shape
        if face == "-Z":
            trsf.SetRotation(gp_Ax1(origin, gp_Dir(1.0, 0.0, 0.0)), math.pi)
        elif face == "+X":
            trsf.SetRotation(gp_Ax1(origin, gp_Dir(0.0, 1.0, 0.0)), math.pi / 2.0)
        elif face == "-X":
            trsf.SetRotation(gp_Ax1(origin, gp_Dir(0.0, 1.0, 0.0)), -math.pi / 2.0)
        elif face == "+Y":
            trsf.SetRotation(gp_Ax1(origin, gp_Dir(1.0, 0.0, 0.0)), -math.pi / 2.0)
        elif face == "-Y":
            trsf.SetRotation(gp_Ax1(origin, gp_Dir(1.0, 0.0, 0.0)), math.pi / 2.0)
        else:
            return shape
        return BRepBuilderAPI_Transform(shape, trsf, True).Shape()

    def _write_geometry_manifest(self, output_file: Path) -> Path:
        manifest_path = output_file.with_suffix(".geometry_manifest.json")
        manifest_payload = {
            "step_path": str(output_file),
            "manifest_version": "catalog-shell-geometry-v1",
            "aperture_topology_stage": "step_boolean",
            "entries": list(self.last_geometry_manifest),
        }
        manifest_path.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest_path

    def _write_geometry_proxy_manifest(self, output_file: Path, design_state: DesignState) -> Path:
        manifest_path = output_file.with_suffix(".geometry_proxy_manifest.json")
        manifest_payload = build_geometry_proxy_manifest(design_state)
        manifest_payload["step_path"] = str(output_file)
        manifest_path.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest_path

    def _create_heatsink(self, comp: ComponentGeometry, params: dict) -> Optional[Any]:
        """
        创建散热器几何 (ADD_HEATSINK)

        在组件指定面上附加一个薄板几何体

        Args:
            comp: 组件几何信息
            params: 散热器参数 {"face": "+Y", "thickness": 2.0, "conductivity": 400}

        Returns:
            TopoDS_Shape 实体，或 None
        """
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCC.Core.gp import gp_Trsf, gp_Vec
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

        try:
            face = params.get('face', '+Y')
            thickness = params.get('thickness', 2.0)  # mm
            extension = params.get('extension', 10.0)  # 面积扩展量 (mm)

            # 根据面方向确定散热板尺寸和位置
            if face in ['+X', '-X']:
                # X 方向面：散热板在 YZ 平面
                hs_width = comp.dimensions.y + extension
                hs_height = comp.dimensions.z + extension
                hs_depth = thickness

                box = BRepPrimAPI_MakeBox(hs_depth, hs_width, hs_height).Shape()

                if face == '+X':
                    # 贴在 +X 面
                    offset_x = comp.position.x + comp.dimensions.x / 2
                else:
                    # 贴在 -X 面
                    offset_x = comp.position.x - comp.dimensions.x / 2 - thickness

                offset_y = comp.position.y - hs_width / 2
                offset_z = comp.position.z - hs_height / 2

            elif face in ['+Y', '-Y']:
                # Y 方向面：散热板在 XZ 平面
                hs_width = comp.dimensions.x + extension
                hs_height = comp.dimensions.z + extension
                hs_depth = thickness

                box = BRepPrimAPI_MakeBox(hs_width, hs_depth, hs_height).Shape()

                offset_x = comp.position.x - hs_width / 2

                if face == '+Y':
                    # 贴在 +Y 面（深空冷背景方向）
                    offset_y = comp.position.y + comp.dimensions.y / 2
                else:
                    # 贴在 -Y 面
                    offset_y = comp.position.y - comp.dimensions.y / 2 - thickness

                offset_z = comp.position.z - hs_height / 2

            elif face in ['+Z', '-Z']:
                # Z 方向面：散热板在 XY 平面
                hs_width = comp.dimensions.x + extension
                hs_height = comp.dimensions.y + extension
                hs_depth = thickness

                box = BRepPrimAPI_MakeBox(hs_width, hs_height, hs_depth).Shape()

                offset_x = comp.position.x - hs_width / 2
                offset_y = comp.position.y - hs_height / 2

                if face == '+Z':
                    # 贴在 +Z 面
                    offset_z = comp.position.z + comp.dimensions.z / 2
                else:
                    # 贴在 -Z 面
                    offset_z = comp.position.z - comp.dimensions.z / 2 - thickness

            else:
                logger.warning(f"    ⚠ 未知的散热器面方向: {face}")
                return None

            # 应用平移变换
            trsf = gp_Trsf()
            trsf.SetTranslation(gp_Vec(offset_x, offset_y, offset_z))

            return BRepBuilderAPI_Transform(box, trsf, True).Shape()

        except Exception as e:
            logger.warning(f"    ⚠ 散热器创建失败: {e}")
            return None

    def _create_bracket(self, comp: ComponentGeometry, params: dict) -> Optional[Any]:
        """
        创建结构支架几何 (ADD_BRACKET)

        在组件底部生成支撑结构，连接组件和舱壁

        Args:
            comp: 组件几何信息
            params: 支架参数 {"height": 20.0, "material": "aluminum", "attach_face": "-Z", "shape": "cylinder"}

        Returns:
            TopoDS_Shape 实体，或 None
        """
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCC.Core.gp import gp_Trsf, gp_Vec
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

        try:
            height = params.get('height', 20.0)  # mm
            attach_face = params.get('attach_face', '-Z')
            shape = params.get('shape', 'cylinder')  # cylinder 或 box
            diameter = params.get('diameter', 15.0)  # 圆柱支架直径 (mm)

            # 目前只支持 -Z 方向（底部支架）
            if attach_face != '-Z':
                logger.warning(f"    ⚠ 暂不支持 {attach_face} 方向的支架，仅支持 -Z")
                return None

            # 计算支架位置（组件底部中心）
            comp_bottom_z = comp.position.z - comp.dimensions.z / 2

            if shape == 'cylinder':
                # 圆柱形支架
                radius = diameter / 2.0
                bracket = BRepPrimAPI_MakeCylinder(radius, height).Shape()

                # 支架顶部贴合组件底部
                trsf = gp_Trsf()
                trsf.SetTranslation(
                    gp_Vec(
                        comp.position.x,
                        comp.position.y,
                        comp_bottom_z - height  # 支架底部
                    )
                )

            else:
                # 方形支架
                bracket_size = params.get('size', 20.0)  # 方形支架边长
                bracket = BRepPrimAPI_MakeBox(bracket_size, bracket_size, height).Shape()

                trsf = gp_Trsf()
                trsf.SetTranslation(
                    gp_Vec(
                        comp.position.x - bracket_size / 2,
                        comp.position.y - bracket_size / 2,
                        comp_bottom_z - height
                    )
                )

            return BRepBuilderAPI_Transform(bracket, trsf, True).Shape()

        except Exception as e:
            logger.warning(f"    ⚠ 支架创建失败: {e}")
            return None


def export_design_occ(design_state: DesignState, output_path: str) -> bool:
    """
    使用 OpenCASCADE 导出设计状态为 STEP 文件 (DV2.0)

    Args:
        design_state: 设计状态
        output_path: 输出文件路径

    Returns:
        是否成功
    """
    exporter = OCCSTEPExporter()
    return exporter.export(design_state, output_path)


# ============ DV2.0 测试脚本 ============

if __name__ == "__main__":
    import sys

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=" * 60)
    print("DV2.0 动态几何生成测试")
    print("=" * 60)

    # 创建测试设计状态（包含散热器、支架、圆柱体）
    from core.protocol import Vector3D, Envelope

    components = [
        # 1. 普通长方体组件
        ComponentGeometry(
            id="battery_01",
            position=Vector3D(x=0.0, y=0.0, z=-50.0),
            dimensions=Vector3D(x=200.0, y=150.0, z=100.0),
            mass=5.0,
            power=50.0,
            category="power"
        ),
        # 2. 带散热器的组件（热刺客）
        ComponentGeometry(
            id="transmitter_01",
            position=Vector3D(x=150.0, y=0.0, z=0.0),
            dimensions=Vector3D(x=80.0, y=60.0, z=40.0),
            mass=1.2,
            power=80.0,
            category="comm",
            heatsink={"face": "+Y", "thickness": 3.0, "conductivity": 400.0}
        ),
        # 3. 带支架的组件
        ComponentGeometry(
            id="payload_camera",
            position=Vector3D(x=-100.0, y=0.0, z=80.0),
            dimensions=Vector3D(x=250.0, y=200.0, z=350.0),
            mass=12.0,
            power=25.0,
            category="payload",
            bracket={"height": 30.0, "shape": "cylinder", "diameter": 20.0}
        ),
    ]

    # 添加圆柱体组件（需要手动设置 envelope_type）
    cylinder_comp = ComponentGeometry(
        id="reaction_wheel_01",
        position=Vector3D(x=0.0, y=150.0, z=0.0),
        dimensions=Vector3D(x=100.0, y=100.0, z=60.0),  # X/Y 为直径，Z 为高度
        mass=4.5,
        power=15.0,
        category="adcs"
    )
    # 手动设置为圆柱体包络
    cylinder_comp.envelope_type = "cylinder"
    components.append(cylinder_comp)

    envelope = Envelope(
        outer_size=Vector3D(x=500.0, y=400.0, z=600.0)
    )

    design_state = DesignState(
        iteration=1,
        components=components,
        envelope=envelope
    )

    # 导出 STEP
    output_path = "tests/manual/artifacts/test_dv2_geometry.step"
    try:
        export_design_occ(design_state, output_path)
        print("\n" + "=" * 60)
        print("✓ DV2.0 动态几何测试成功！")
        print("=" * 60)
        print(f"  输出文件: {output_path}")
        print("  包含:")
        print("    - 1 个普通长方体 (battery_01)")
        print("    - 1 个带散热器的组件 (transmitter_01 + heatsink)")
        print("    - 1 个带支架的组件 (payload_camera + bracket)")
        print("    - 1 个圆柱体组件 (reaction_wheel_01)")
        print("\n  可使用 COMSOL、SolidWorks、FreeCAD 等软件打开验证")
    except Exception as e:
        print(f"\n✗ DV2.0 动态几何测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
