#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
STEP导出验证脚本（无需COMSOL）

仅测试：
1. DesignState -> STEP导出
2. 验证STEP文件格式
3. 计算Box Selection坐标

用于在没有COMSOL环境时验证前置流程
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import logging
from typing import List, Tuple

from core.protocol import DesignState, ComponentGeometry, Vector3D, Envelope

# 直接导入cad_export模块，避免geometry包的其他依赖
import importlib.util
spec = importlib.util.spec_from_file_location(
    "cad_export",
    project_root / "geometry" / "cad_export.py"
)
cad_export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cad_export)

export_design = cad_export.export_design
CADExportOptions = cad_export.CADExportOptions

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_test_design() -> DesignState:
    """创建测试设计"""
    components = [
        ComponentGeometry(
            id="battery_01",
            position=Vector3D(x=50.0, y=50.0, z=50.0),
            dimensions=Vector3D(x=100.0, y=80.0, z=50.0),
            mass=5.0,
            power=10.0,
            category="power"
        ),
        ComponentGeometry(
            id="payload_01",
            position=Vector3D(x=200.0, y=50.0, z=50.0),
            dimensions=Vector3D(x=80.0, y=80.0, z=60.0),
            mass=3.0,
            power=5.0,
            category="payload"
        )
    ]

    envelope = Envelope(
        outer_size=Vector3D(x=400.0, y=200.0, z=200.0)
    )

    return DesignState(
        iteration=0,
        components=components,
        envelope=envelope
    )


def calculate_box_selection(comp: ComponentGeometry) -> dict:
    """
    计算组件的Box Selection坐标

    Args:
        comp: 组件几何

    Returns:
        包含xmin, xmax等的字典
    """
    pos = comp.position
    dim = comp.dimensions

    return {
        "component_id": comp.id,
        "xmin": pos.x - dim.x / 2,
        "xmax": pos.x + dim.x / 2,
        "ymin": pos.y - dim.y / 2,
        "ymax": pos.y + dim.y / 2,
        "zmin": pos.z - dim.z / 2,
        "zmax": pos.z + dim.z / 2,
        "volume_mm3": dim.x * dim.y * dim.z,
        "volume_m3": (dim.x * dim.y * dim.z) / 1e9,
        "power_w": comp.power,
        "power_density_w_m3": comp.power / ((dim.x * dim.y * dim.z) / 1e9) if comp.power > 0 else 0
    }


def verify_step_file(step_file: Path) -> bool:
    """
    验证STEP文件格式

    Args:
        step_file: STEP文件路径

    Returns:
        是否有效
    """
    logger.info(f"验证STEP文件: {step_file}")

    if not step_file.exists():
        logger.error("文件不存在")
        return False

    with open(step_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查必要的STEP标记
    required_markers = [
        "ISO-10303-21",
        "HEADER",
        "FILE_DESCRIPTION",
        "FILE_NAME",
        "FILE_SCHEMA",
        "DATA",
        "ENDSEC",
        "END-ISO-10303-21"
    ]

    missing = []
    for marker in required_markers:
        if marker not in content:
            missing.append(marker)

    if missing:
        logger.error(f"缺少必要标记: {missing}")
        return False

    # 统计实体数量
    entity_count = content.count("CARTESIAN_POINT")
    logger.info(f"  ✓ 包含 {entity_count} 个CARTESIAN_POINT实体")

    block_count = content.count("BLOCK")
    logger.info(f"  ✓ 包含 {block_count} 个BLOCK实体")

    logger.info("  ✓ STEP文件格式有效")
    return True


def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("STEP导出验证测试")
    logger.info("=" * 60)

    # 1. 创建测试设计
    logger.info("\n[1/4] 创建测试设计...")
    design_state = create_test_design()
    logger.info(f"  ✓ 创建了 {len(design_state.components)} 个组件")

    # 2. 导出STEP
    logger.info("\n[2/4] 导出STEP文件...")
    workspace = Path("workspace/step_test")
    workspace.mkdir(parents=True, exist_ok=True)
    step_file = workspace / "test_design.step"

    options = CADExportOptions(
        unit="mm",
        precision=3,
        author="StepExportTest"
    )

    export_design(design_state, str(step_file), format="step", options=options)
    logger.info(f"  ✓ STEP文件: {step_file}")

    # 3. 验证STEP文件
    logger.info("\n[3/4] 验证STEP文件格式...")
    if not verify_step_file(step_file):
        logger.error("STEP文件验证失败")
        return False

    # 4. 计算Box Selection坐标
    logger.info("\n[4/4] 计算Box Selection坐标...")
    for comp in design_state.components:
        box = calculate_box_selection(comp)
        logger.info(f"\n  组件: {box['component_id']}")
        logger.info(f"    Box范围: X[{box['xmin']:.1f}, {box['xmax']:.1f}] "
                   f"Y[{box['ymin']:.1f}, {box['ymax']:.1f}] "
                   f"Z[{box['zmin']:.1f}, {box['zmax']:.1f}]")
        logger.info(f"    体积: {box['volume_m3']:.6f} m³")
        if box['power_w'] > 0:
            logger.info(f"    发热功率: {box['power_w']} W")
            logger.info(f"    功率密度: {box['power_density_w_m3']:.2e} W/m³")

    # 5. 计算外边界Box Selection
    logger.info("\n  外部辐射边界:")
    env = design_state.envelope.outer_size
    margin = 10.0
    logger.info(f"    Box范围: X[{-margin:.1f}, {env.x + margin:.1f}] "
               f"Y[{-margin:.1f}, {env.y + margin:.1f}] "
               f"Z[{-margin:.1f}, {env.z + margin:.1f}]")

    logger.info("\n" + "=" * 60)
    logger.info("✓ STEP导出验证通过！")
    logger.info("=" * 60)
    logger.info("\n下一步：运行 test_dynamic_comsol_import.py（需要COMSOL环境）")

    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
