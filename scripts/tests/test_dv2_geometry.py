#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DV2.0 动态几何生成测试脚本

测试内容：
1. CHANGE_ENVELOPE: 圆柱体包络
2. ADD_HEATSINK: 散热器几何生成
3. ADD_BRACKET: 结构支架几何生成
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Windows UTF-8 编码
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from geometry.cad_export_occ import export_design_occ, OCCSTEPExporter
from core.protocol import DesignState, ComponentGeometry, Vector3D, Envelope


def main():
    print("=" * 60)
    print("DV2.0 动态几何生成测试")
    print("=" * 60)

    # 创建测试组件
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
        # 4. 圆柱体组件（飞轮）
        ComponentGeometry(
            id="reaction_wheel_01",
            position=Vector3D(x=0.0, y=150.0, z=0.0),
            dimensions=Vector3D(x=100.0, y=100.0, z=60.0),
            mass=4.5,
            power=15.0,
            category="adcs",
            envelope_type="cylinder"
        ),
    ]

    envelope = Envelope(outer_size=Vector3D(x=500.0, y=400.0, z=600.0))
    design_state = DesignState(iteration=1, components=components, envelope=envelope)

    # 确保输出目录存在
    os.makedirs("workspace", exist_ok=True)

    # 导出 STEP
    output_path = "workspace/test_dv2_geometry.step"

    try:
        export_design_occ(design_state, output_path)

        print()
        print("=" * 60)
        print("✓ DV2.0 动态几何测试成功！")
        print("=" * 60)
        print(f"  输出文件: {output_path}")
        print("  包含:")
        print("    - 1 个普通长方体 (battery_01)")
        print("    - 1 个带散热器的组件 (transmitter_01 + heatsink)")
        print("    - 1 个带支架的组件 (payload_camera + bracket)")
        print("    - 1 个圆柱体组件 (reaction_wheel_01)")
        print()
        print("  可使用 COMSOL、SolidWorks、FreeCAD 等软件打开验证")

        # 检查文件大小
        file_size = os.path.getsize(output_path)
        print(f"  文件大小: {file_size / 1024:.2f} KB")

        return 0

    except Exception as e:
        print(f"\n✗ DV2.0 动态几何测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
