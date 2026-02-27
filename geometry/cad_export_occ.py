#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CAD导出模块 (OpenCASCADE版本) - DV2.0 动态几何实体生成

使用 pythonocc-core 生成符合 ISO 10303-21 标准的完整 STEP 文件，
支持 DV2.0 的 10 类算子：
- CHANGE_ENVELOPE: 包络切换 (Box → Cylinder)
- ADD_HEATSINK: 动态生成散热窗/板几何体
- ADD_BRACKET: 动态生成结构支架几何体

安装依赖:
    conda install -c conda-forge pythonocc-core
"""

import logging
import math
from pathlib import Path
from typing import Optional, List, Tuple, Any

from core.protocol import DesignState, ComponentGeometry
from core.exceptions import GeometryError

logger = logging.getLogger(__name__)


class OCCSTEPExporter:
    """
    基于 OpenCASCADE 的 STEP 导出器 (DV2.0)

    支持动态生成：
    - 长方体 (Box) 和圆柱体 (Cylinder) 包络
    - 散热窗/板附加几何体
    - 结构支架几何体
    """

    def __init__(self):
        """初始化 OCC STEP 导出器"""
        try:
            # 导入 pythonocc-core 模块
            from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
            from OCC.Core.gp import gp_Pnt, gp_Ax2, gp_Dir, gp_Trsf, gp_Vec
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
            from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
            from OCC.Core.IFSelect import IFSelect_RetDone
            from OCC.Core.Interface import Interface_Static_SetCVal
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
            from OCC.Core.Interface import Interface_Static_SetCVal
            from OCC.Core.TopoDS import TopoDS_Compound
            from OCC.Core.BRep import BRep_Builder

            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            logger.info(f"开始生成 STEP 文件 (DV2.0): {output_path}")
            logger.info(f"  组件数量: {len(design_state.components)}")

            # 创建 STEP 写入器
            step_writer = STEPControl_Writer()
            Interface_Static_SetCVal("write.step.schema", "AP214")  # 使用 AP214 协议

            # 创建复合体（Compound）来容纳所有组件
            compound = TopoDS_Compound()
            builder = BRep_Builder()
            builder.MakeCompound(compound)

            # 统计动态几何生成
            heatsink_count = 0
            bracket_count = 0
            cylinder_count = 0

            # 为每个组件创建 BREP 实体
            for i, comp in enumerate(design_state.components):
                logger.info(f"  [{i+1}/{len(design_state.components)}] 创建组件: {comp.id}")

                # === 1. 创建主体几何 (支持 Box 和 Cylinder) ===
                main_shape = self._create_component_shape(comp)
                builder.Add(compound, main_shape)

                # 检查是否为圆柱体
                envelope_type = getattr(comp, 'envelope_type', 'box')
                if envelope_type == 'cylinder':
                    cylinder_count += 1

                logger.info(f"    ✓ 主体: {envelope_type}, 位置: ({comp.position.x:.2f}, {comp.position.y:.2f}, {comp.position.z:.2f})")

                # === 2. 创建散热器几何 (ADD_HEATSINK) ===
                heatsink_params = getattr(comp, 'heatsink', None)
                if heatsink_params:
                    heatsink_shape = self._create_heatsink(comp, heatsink_params)
                    if heatsink_shape:
                        builder.Add(compound, heatsink_shape)
                        heatsink_count += 1
                        logger.info(f"    ✓ 散热器: face={heatsink_params.get('face', '+Y')}, thickness={heatsink_params.get('thickness', 2.0)}mm")

                # === 3. 创建支架几何 (ADD_BRACKET) ===
                bracket_params = getattr(comp, 'bracket', None)
                if bracket_params:
                    bracket_shape = self._create_bracket(comp, bracket_params)
                    if bracket_shape:
                        builder.Add(compound, bracket_shape)
                        bracket_count += 1
                        logger.info(f"    ✓ 支架: height={bracket_params.get('height', 20.0)}mm")

            # 将复合体写入 STEP 文件
            logger.info("  写入 STEP 文件...")
            step_writer.Transfer(compound, STEPControl_AsIs)
            status = step_writer.Write(str(output_file))

            if status != IFSelect_RetDone:
                raise GeometryError(f"STEP 写入失败，状态码: {status}")

            logger.info(f"✓ STEP 文件生成成功: {output_path}")
            logger.info(f"  文件大小: {output_file.stat().st_size / 1024:.2f} KB")
            logger.info(f"  动态几何: {heatsink_count} 散热器, {bracket_count} 支架, {cylinder_count} 圆柱体")

            return True

        except ImportError as e:
            raise GeometryError(f"pythonocc-core 导入失败: {e}")
        except Exception as e:
            logger.error(f"STEP 导出失败: {e}", exc_info=True)
            raise GeometryError(f"STEP 导出失败: {e}")

    def _create_component_shape(self, comp: ComponentGeometry) -> Any:
        """
        创建组件主体几何 (DV2.0: 支持 Box 和 Cylinder)

        Args:
            comp: 组件几何信息

        Returns:
            TopoDS_Shape 实体
        """
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir, gp_Trsf, gp_Vec
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

        # 检查包络类型
        envelope_type = getattr(comp, 'envelope_type', 'box')

        if envelope_type == 'cylinder':
            # === 圆柱体包络 (CHANGE_ENVELOPE) ===
            # 从 dimensions 计算等效圆柱参数
            # 假设 X/Y 为直径方向，Z 为高度
            radius = min(comp.dimensions.x, comp.dimensions.y) / 2.0
            height = comp.dimensions.z

            # 创建圆柱体（默认沿 Z 轴）
            # 圆柱体底面中心在原点
            cylinder = BRepPrimAPI_MakeCylinder(radius, height).Shape()

            # 平移到组件位置（圆柱体中心）
            trsf = gp_Trsf()
            trsf.SetTranslation(
                gp_Vec(
                    comp.position.x,
                    comp.position.y,
                    comp.position.z - height / 2.0  # 圆柱体底面中心
                )
            )

            return BRepBuilderAPI_Transform(cylinder, trsf, True).Shape()

        else:
            # === 长方体包络 (默认) ===
            box = BRepPrimAPI_MakeBox(
                comp.dimensions.x,
                comp.dimensions.y,
                comp.dimensions.z
            ).Shape()

            # 平移到组件位置（Box 从角点开始，需要调整到中心）
            trsf = gp_Trsf()
            trsf.SetTranslation(
                gp_Vec(
                    comp.position.x - comp.dimensions.x / 2,
                    comp.position.y - comp.dimensions.y / 2,
                    comp.position.z - comp.dimensions.z / 2
                )
            )

            return BRepBuilderAPI_Transform(box, trsf, True).Shape()

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
    output_path = "workspace/test_dv2_geometry.step"
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
