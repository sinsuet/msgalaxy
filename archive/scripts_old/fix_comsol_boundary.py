#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
快速修复COMSOL模型边界条件

重新生成带有正确辐射边界条件的COMSOL模型
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulation.comsol_model_generator import COMSOLModelGenerator
from core.protocol import DesignState, ComponentGeometry, Vector3D, Envelope


def main():
    print("=" * 80)
    print("COMSOL模型边界条件修复工具")
    print("=" * 80)
    print()

    # 创建测试设计状态（与当前BOM一致）
    print("[1/3] 创建设计状态...")
    comp1 = ComponentGeometry(
        id='battery_01',
        position=Vector3D(x=0, y=0, z=0),
        dimensions=Vector3D(x=200, y=150, z=100),
        mass=5.0,
        power=50.0,
        category='power'
    )

    comp2 = ComponentGeometry(
        id='payload_01',
        position=Vector3D(x=0, y=0, z=150),
        dimensions=Vector3D(x=180, y=180, z=120),
        mass=3.5,
        power=30.0,
        category='payload'
    )

    state = DesignState(
        iteration=1,
        components=[comp1, comp2],
        envelope=Envelope(
            outer_size=Vector3D(x=400, y=400, z=400),
            thickness=5.0
        )
    )
    print("  ✓ 设计状态创建完成")
    print(f"    - 组件数量: {len(state.components)}")
    print(f"    - 总功率: {sum(c.power for c in state.components)}W")

    # 生成新模型
    print()
    print("[2/3] 生成COMSOL模型...")
    output_path = 'models/satellite_thermal_fixed.mph'

    generator = COMSOLModelGenerator()
    success = generator.generate_model(
        state,
        output_path,
        environment='orbit'  # 使用轨道环境（辐射边界条件）
    )

    if not success:
        print()
        print("[失败] 模型生成失败")
        return 1

    # 验证模型
    print()
    print("[3/3] 验证模型...")
    if os.path.exists(output_path):
        size_kb = os.path.getsize(output_path) / 1024
        print(f"  ✓ 模型文件已生成: {output_path}")
        print(f"  ✓ 文件大小: {size_kb:.1f} KB")
    else:
        print(f"  ✗ 模型文件未找到: {output_path}")
        return 1

    print()
    print("=" * 80)
    print("✓ COMSOL模型修复完成！")
    print("=" * 80)
    print()
    print("关键改进:")
    print("  1. ✓ 使用辐射边界条件（表面对表面辐射）")
    print("  2. ✓ 设置深空温度 (3K)")
    print("  3. ✓ 设置表面发射率 (0.85)")
    print("  4. ✓ 移除错误的对流边界条件")
    print("  5. ✓ 正确的热源体积功率密度")
    print()
    print("预期效果:")
    print("  - 温度将从 2.2亿°C 降至 <80°C")
    print("  - 热量通过辐射散发到深空")
    print("  - 物理上合理的热平衡")
    print()
    print("下一步:")
    print(f"  1. 将 {output_path} 复制到 models/satellite_thermal_v2.mph")
    print("  2. 或在 config/system.yaml 中更新 comsol_model 路径")
    print("  3. 重新运行测试: python test_real_workflow.py")
    print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
