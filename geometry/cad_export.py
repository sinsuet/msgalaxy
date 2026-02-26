#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CAD导出模块 - 支持STEP/IGES格式导出

将设计状态导出为标准CAD格式，用于后续详细设计和制造。
"""

import logging
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass

from core.protocol import DesignState, ComponentGeometry, Vector3D
from core.exceptions import GeometryError

logger = logging.getLogger(__name__)


@dataclass
class CADExportOptions:
    """CAD导出选项"""
    include_metadata: bool = True
    unit: str = "mm"  # mm, cm, m
    precision: int = 3  # 小数位数
    author: str = "MsGalaxy"
    description: str = "Satellite design export"


class STEPExporter:
    """
    STEP格式导出器

    STEP (Standard for the Exchange of Product Data) 是ISO 10303标准，
    广泛用于CAD数据交换。

    注意: 完整的STEP导出需要pythonocc-core库，这里提供简化实现。
    """

    def __init__(self, options: Optional[CADExportOptions] = None):
        """
        初始化STEP导出器

        Args:
            options: 导出选项
        """
        self.options = options or CADExportOptions()

    def export(self, design_state: DesignState, output_path: str) -> bool:
        """
        导出设计状态为STEP文件

        Args:
            design_state: 设计状态
            output_path: 输出文件路径

        Returns:
            是否成功
        """
        try:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            # 生成STEP内容
            step_content = self._generate_step_content(design_state)

            # 写入文件
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(step_content)

            logger.info(f"STEP file exported successfully: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to export STEP file: {e}")
            raise GeometryError(f"STEP export failed: {e}")

    def _generate_step_content(self, design_state: DesignState) -> str:
        """
        生成STEP文件内容

        这是一个简化的STEP格式实现，包含基本的几何信息。
        完整实现需要使用pythonocc-core库。

        Args:
            design_state: 设计状态

        Returns:
            STEP文件内容
        """
        lines = []

        # STEP文件头
        lines.append("ISO-10303-21;")
        lines.append("HEADER;")
        lines.append(f"FILE_DESCRIPTION(('MsGalaxy Satellite Design Export'),'{self.options.description}');")
        lines.append(f"FILE_NAME('{design_state.iteration}','2026-02-25T00:00:00',")
        lines.append(f"  ('{self.options.author}'),('MsGalaxy'),'','','');")
        lines.append("FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));")
        lines.append("ENDSEC;")
        lines.append("")

        # 数据段
        lines.append("DATA;")

        entity_id = 1

        # 导出每个组件
        for comp in design_state.components:
            comp_lines, entity_id = self._export_component(comp, entity_id)
            lines.extend(comp_lines)

        lines.append("ENDSEC;")
        lines.append("END-ISO-10303-21;")

        return '\n'.join(lines)

    def _export_component(
        self,
        component: ComponentGeometry,
        start_id: int
    ) -> tuple[List[str], int]:
        """
        导出单个组件

        Args:
            component: 组件几何
            start_id: 起始实体ID

        Returns:
            (STEP行列表, 下一个实体ID)
        """
        lines = []
        entity_id = start_id

        # 组件名称
        lines.append(f"/* Component: {component.id} */")

        # 位置点
        pos = component.position
        lines.append(
            f"#{entity_id} = CARTESIAN_POINT('{component.id}_origin',"
            f"({pos.x:.{self.options.precision}f},"
            f"{pos.y:.{self.options.precision}f},"
            f"{pos.z:.{self.options.precision}f}));"
        )
        origin_id = entity_id
        entity_id += 1

        # 方向向量（X, Y, Z轴）
        lines.append(f"#{entity_id} = DIRECTION('X',(1.0,0.0,0.0));")
        x_dir_id = entity_id
        entity_id += 1

        lines.append(f"#{entity_id} = DIRECTION('Y',(0.0,1.0,0.0));")
        y_dir_id = entity_id
        entity_id += 1

        lines.append(f"#{entity_id} = DIRECTION('Z',(0.0,0.0,1.0));")
        z_dir_id = entity_id
        entity_id += 1

        # 坐标系
        lines.append(
            f"#{entity_id} = AXIS2_PLACEMENT_3D('{component.id}_placement',"
            f"#{origin_id},#{z_dir_id},#{x_dir_id});"
        )
        placement_id = entity_id
        entity_id += 1

        # 长方体尺寸
        dim = component.dimensions
        lines.append(
            f"#{entity_id} = BLOCK('{component.id}_block',#{placement_id},"
            f"{dim.x:.{self.options.precision}f},"
            f"{dim.y:.{self.options.precision}f},"
            f"{dim.z:.{self.options.precision}f});"
        )
        block_id = entity_id
        entity_id += 1

        # 产品定义
        lines.append(
            f"#{entity_id} = PRODUCT('{component.id}','{component.category}',"
            f"'Mass: {component.mass}kg, Power: {component.power}W',());"
        )
        product_id = entity_id
        entity_id += 1

        # 产品定义形状
        lines.append(
            f"#{entity_id} = PRODUCT_DEFINITION_SHAPE('',"
            f"'Shape of {component.id}',#{product_id});"
        )
        entity_id += 1

        lines.append("")

        return lines, entity_id


class IGESExporter:
    """
    IGES格式导出器

    IGES (Initial Graphics Exchange Specification) 是另一种常用的CAD交换格式。
    """

    def __init__(self, options: Optional[CADExportOptions] = None):
        """初始化IGES导出器"""
        self.options = options or CADExportOptions()

    def export(self, design_state: DesignState, output_path: str) -> bool:
        """
        导出设计状态为IGES文件

        Args:
            design_state: 设计状态
            output_path: 输出文件路径

        Returns:
            是否成功
        """
        try:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            # 生成IGES内容
            iges_content = self._generate_iges_content(design_state)

            # 写入文件
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(iges_content)

            logger.info(f"IGES file exported successfully: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to export IGES file: {e}")
            raise GeometryError(f"IGES export failed: {e}")

    def _generate_iges_content(self, design_state: DesignState) -> str:
        """生成IGES文件内容（简化版本）"""
        lines = []

        # Start段
        lines.append(f"MsGalaxy Satellite Design Export                                       S      1")

        # Global段
        global_section = [
            "1H,,1H;,",
            f"7HMsGalaxy,",
            f"25H{self.options.description},",
            "6H2026.2,32,308,15,308,15,",
            f"7H{self.options.author},1,2,",
            "13H20260225.000000,1.0E-6,1.0,",
            f"4H{self.options.unit.upper()},1,0.01,",
            "15H20260225.000000,;",
        ]

        for i, line in enumerate(global_section, 1):
            lines.append(f"{line:<72}G{i:>6}")

        # Directory Entry段和Parameter Data段
        # 这里简化处理，实际IGES格式更复杂
        entity_count = 0
        for comp in design_state.components:
            entity_count += 1
            # 简化的实体定义
            lines.append(f"     150       {entity_count}       0       0       0       0       0       0D{entity_count*2-1:>6}")
            lines.append(f"     150       0       0       1       0                               D{entity_count*2:>6}")

        # Terminate段
        lines.append(f"S      1G{len(global_section):>6}D{entity_count*2:>6}P{entity_count:>6}                                        T      1")

        return '\n'.join(lines)


def export_design(
    design_state: DesignState,
    output_path: str,
    format: str = "step",
    options: Optional[CADExportOptions] = None
) -> bool:
    """
    导出设计状态为CAD文件

    Args:
        design_state: 设计状态
        output_path: 输出文件路径
        format: 格式 ("step" 或 "iges")
        options: 导出选项

    Returns:
        是否成功
    """
    format = format.lower()

    if format == "step":
        exporter = STEPExporter(options)
        return exporter.export(design_state, output_path)
    elif format == "iges":
        exporter = IGESExporter(options)
        return exporter.export(design_state, output_path)
    else:
        raise ValueError(f"Unsupported format: {format}. Use 'step' or 'iges'.")


# 示例使用
if __name__ == "__main__":
    import time

    # 配置日志
    logging.basicConfig(level=logging.INFO)

    # 创建测试设计状态
    components = [
        ComponentGeometry(
            id="battery_01",
            position=Vector3D(x=10.0, y=10.0, z=10.0),
            dimensions=Vector3D(x=100.0, y=80.0, z=50.0),
            mass=5.0,
            power=50.0,
            category="power"
        ),
        ComponentGeometry(
            id="payload_01",
            position=Vector3D(x=120.0, y=10.0, z=10.0),
            dimensions=Vector3D(x=80.0, y=80.0, z=60.0),
            mass=3.0,
            power=30.0,
            category="payload"
        )
    ]

    from core.protocol import Envelope

    envelope = Envelope(
        outer_size=Vector3D(x=300.0, y=200.0, z=200.0)
    )

    design_state = DesignState(
        iteration=1,
        components=components,
        envelope=envelope
    )

    # 导出STEP
    export_design(design_state, "output/test_design.step", format="step")

    # 导出IGES
    export_design(design_state, "output/test_design.iges", format="iges")

    print("CAD export completed!")
