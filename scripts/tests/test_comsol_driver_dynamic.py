#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
COMSOL Driver 动态模式单元测试

测试 comsol_driver.py 的动态模式功能：
1. 接收 STEP 文件路径
2. 动态导入几何
3. Box Selection 识别组件
4. 求解并返回结果

这是一个独立的单元测试，不依赖完整的 Orchestrator
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import logging
from typing import Dict, Any

from core.protocol import (
    DesignState, ComponentGeometry, Vector3D, Envelope,
    SimulationRequest, SimulationType
)
from geometry.cad_export import export_design, CADExportOptions
from simulation.comsol_driver import ComsolDriver

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_test_design() -> DesignState:
    """创建测试设计"""
    logger.info("创建测试设计...")

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

    design_state = DesignState(
        iteration=0,
        components=components,
        envelope=envelope
    )

    logger.info(f"✓ 创建了包含 {len(components)} 个组件的设计")
    return design_state


def export_test_step(design_state: DesignState) -> Path:
    """导出测试STEP文件"""
    logger.info("导出STEP文件...")

    workspace = Path("workspace/comsol_driver_test")
    workspace.mkdir(parents=True, exist_ok=True)

    step_file = workspace / "test_design.step"

    options = CADExportOptions(
        unit="mm",
        precision=3,
        author="ComsolDriverTest",
        description="Test design for dynamic COMSOL driver"
    )

    export_design(design_state, str(step_file), format="step", options=options)

    logger.info(f"✓ STEP文件已导出: {step_file}")
    return step_file


def test_dynamic_mode():
    """测试动态模式"""
    logger.info("=" * 60)
    logger.info("测试 COMSOL Driver 动态模式")
    logger.info("=" * 60)

    try:
        # 1. 创建测试设计
        design_state = create_test_design()

        # 2. 导出STEP
        step_file = export_test_step(design_state)

        # 3. 初始化COMSOL Driver（动态模式）
        logger.info("\n初始化COMSOL Driver（动态模式）...")
        config = {
            "mode": "dynamic",  # 关键：启用动态模式
            "comsol_model": "dummy.mph",  # 动态模式不需要预存模型
            "environment": "orbit"
        }

        driver = ComsolDriver(config)

        # 4. 连接COMSOL
        logger.info("\n连接COMSOL...")
        driver.connect()
        logger.info("✓ COMSOL连接成功")

        # 5. 创建仿真请求
        logger.info("\n创建仿真请求...")
        sim_request = SimulationRequest(
            sim_type=SimulationType.COMSOL,
            design_state=design_state,
            parameters={
                "step_file": str(step_file)  # 传递STEP文件路径
            }
        )

        # 6. 运行仿真
        logger.info("\n运行动态仿真（这可能需要几分钟）...")
        result = driver.run_simulation(sim_request)

        # 7. 验证结果
        logger.info("\n验证结果...")
        if not result.success:
            logger.error(f"✗ 仿真失败: {result.error_message}")
            return False

        logger.info("✓ 仿真成功")
        logger.info(f"  最高温度: {result.metrics.get('max_temp', 'N/A')} °C")
        logger.info(f"  平均温度: {result.metrics.get('avg_temp', 'N/A')} °C")
        logger.info(f"  最低温度: {result.metrics.get('min_temp', 'N/A')} °C")
        logger.info(f"  违反数量: {len(result.violations)}")

        # 8. 断开连接
        driver.disconnect()

        logger.info("\n" + "=" * 60)
        logger.info("✓✓✓ COMSOL Driver 动态模式测试成功！")
        logger.info("=" * 60)

        return True

    except Exception as e:
        logger.error(f"✗ 测试失败: {e}", exc_info=True)
        return False


def test_static_mode():
    """测试静态模式（向下兼容性）"""
    logger.info("=" * 60)
    logger.info("测试 COMSOL Driver 静态模式（向下兼容）")
    logger.info("=" * 60)

    try:
        # 1. 创建测试设计
        design_state = create_test_design()

        # 2. 初始化COMSOL Driver（静态模式）
        logger.info("\n初始化COMSOL Driver（静态模式）...")
        config = {
            "mode": "static",  # 静态模式
            "comsol_model": "e:/Code/msgalaxy/models/satellite_thermal_heatflux.mph",
            "comsol_parameters": [
                "battery_x", "battery_y", "battery_z",
                "battery_dx", "battery_dy", "battery_dz",
                "battery_power",
                "payload_x", "payload_y", "payload_z",
                "payload_dx", "payload_dy", "payload_dz",
                "payload_power"
            ],
            "environment": "orbit"
        }

        driver = ComsolDriver(config)

        # 3. 连接COMSOL
        logger.info("\n连接COMSOL...")
        driver.connect()
        logger.info("✓ COMSOL连接成功")

        # 4. 创建仿真请求（不需要STEP文件）
        logger.info("\n创建仿真请求...")
        sim_request = SimulationRequest(
            sim_type=SimulationType.COMSOL,
            design_state=design_state,
            parameters={}  # 静态模式不需要STEP文件
        )

        # 5. 运行仿真
        logger.info("\n运行静态仿真...")
        result = driver.run_simulation(sim_request)

        # 6. 验证结果
        logger.info("\n验证结果...")
        if not result.success:
            logger.error(f"✗ 仿真失败: {result.error_message}")
            return False

        logger.info("✓ 仿真成功")
        logger.info(f"  最高温度: {result.metrics.get('max_temp', 'N/A')} °C")

        # 7. 断开连接
        driver.disconnect()

        logger.info("\n" + "=" * 60)
        logger.info("✓✓✓ COMSOL Driver 静态模式测试成功！")
        logger.info("=" * 60)

        return True

    except Exception as e:
        logger.error(f"✗ 测试失败: {e}", exc_info=True)
        return False


def main():
    """主函数"""
    print("COMSOL Driver 单元测试")
    print("=" * 60)

    # 测试1：动态模式
    print("\n[测试 1/2] 动态模式")
    dynamic_success = test_dynamic_mode()

    # 测试2：静态模式（向下兼容）
    print("\n[测试 2/2] 静态模式（向下兼容）")
    static_success = test_static_mode()

    # 总结
    print("\n" + "=" * 60)
    print("测试总结:")
    print(f"  动态模式: {'✓ 通过' if dynamic_success else '✗ 失败'}")
    print(f"  静态模式: {'✓ 通过' if static_success else '✗ 失败'}")
    print("=" * 60)

    if dynamic_success and static_success:
        print("\n✓ 所有测试通过！")
        sys.exit(0)
    else:
        print("\n✗ 部分测试失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
