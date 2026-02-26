#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试卫星V3.0模型
"""

import sys
import os

# 设置UTF-8编码
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from simulation.comsol_driver import ComsolDriver
from core.protocol import (
    DesignState, ComponentGeometry, Envelope, Vector3D,
    SimulationRequest, SimulationType
)


def test_satellite_v3_model():
    """测试卫星V3.0模型"""
    print("=" * 70)
    print("卫星热分析模型V3.0测试")
    print("=" * 70)
    print()

    model_file = "models/satellite_thermal_v3.mph"

    if not os.path.exists(model_file):
        print(f"[错误] 模型文件不存在: {model_file}")
        return False

    try:
        # 配置
        config = {
            'comsol_model': model_file,
            'comsol_parameters': [
                'battery_x', 'battery_y', 'battery_z',
                'battery_dx', 'battery_dy', 'battery_dz', 'battery_power',
                'payload_x', 'payload_y', 'payload_z',
                'payload_dx', 'payload_dy', 'payload_dz', 'payload_power'
            ],
            'constraints': {'max_temp_c': 50.0}
        }

        print("[1/5] 初始化COMSOL驱动...")
        driver = ComsolDriver(config)
        print("  ✓ 驱动初始化成功")

        print("\n[2/5] 连接COMSOL...")
        driver.connect()
        print("  ✓ COMSOL连接成功")

        print("\n[3/5] 创建测试设计状态...")
        design_state = DesignState(
            iteration=1,
            components=[
                ComponentGeometry(
                    id="battery_01",
                    position=Vector3D(x=0, y=0, z=0),
                    dimensions=Vector3D(x=200, y=150, z=100),
                    rotation=Vector3D(x=0, y=0, z=0),
                    mass=5.0,
                    power=50.0,
                    category="power"
                ),
                ComponentGeometry(
                    id="payload_01",
                    position=Vector3D(x=0, y=0, z=150),
                    dimensions=Vector3D(x=180, y=180, z=120),
                    rotation=Vector3D(x=0, y=0, z=0),
                    mass=3.5,
                    power=30.0,
                    category="payload"
                )
            ],
            envelope=Envelope(
                outer_size=Vector3D(x=400, y=400, z=400),
                inner_size=Vector3D(x=390, y=390, z=390),
                thickness=5.0
            )
        )
        print("  ✓ 设计状态创建成功")

        print("\n[4/5] 运行COMSOL仿真...")
        print("  (这可能需要1-2分钟...)")
        request = SimulationRequest(
            sim_type=SimulationType.COMSOL,
            design_state=design_state,
            parameters={}
        )

        result = driver.run_simulation(request)

        if result.success:
            print("  ✓ 仿真成功完成")
        else:
            print(f"  ✗ 仿真失败: {result.error_message}")
            return False

        print("\n[5/5] 仿真结果:")
        if result.metrics:
            for key, value in result.metrics.items():
                if 'temp' in key.lower():
                    print(f"  {key}: {value:.2f}°C")
                else:
                    print(f"  {key}: {value:.2f}")
        else:
            print("  (无指标数据)")

        if result.violations:
            print(f"\n违规数: {len(result.violations)}")
            for v in result.violations:
                print(f"  - {v.description} (severity: {v.severity:.2f})")
        else:
            print("\n无违规")

        print("\n断开COMSOL连接...")
        driver.disconnect()
        print("  ✓ 连接已关闭")

        print("\n" + "=" * 70)
        print("✓ V3.0模型测试通过！")
        print("=" * 70)
        print()
        print("模型信息:")
        print(f"  - 文件: {model_file}")
        print(f"  - 大小: {os.path.getsize(model_file) / 1024:.1f} KB")
        print(f"  - 组件数: {len(design_state.components)}")
        print(f"  - 参数数: {len(config['comsol_parameters'])}")
        print()

        return True

    except Exception as e:
        print(f"\n[错误] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = test_satellite_v3_model()
    sys.exit(0 if success else 1)
