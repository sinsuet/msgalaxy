#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
COMSOL 动态仿真调试脚本

用于调试 COMSOL 动态 STEP 导入和仿真问题
"""

import sys
import os
import io
import logging
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Windows 编码修复
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from simulation.comsol_driver import ComsolDriver
from core.protocol import SimulationRequest, DesignState, ComponentGeometry, Vector3D, Envelope, SimulationType

print("=" * 80)
print("COMSOL 动态仿真调试测试")
print("=" * 80)

# 创建测试设计
components = [
    ComponentGeometry(
        id='payload_01',
        position=Vector3D(x=0, y=0, z=60),
        dimensions=Vector3D(x=180, y=180, z=120),
        mass=3.5,
        power=30.0,
        category='payload'
    ),
    ComponentGeometry(
        id='battery_01',
        position=Vector3D(x=0, y=0, z=-50),
        dimensions=Vector3D(x=200, y=150, z=100),
        mass=5.0,
        power=50.0,
        category='power'
    )
]

design_state = DesignState(
    iteration=1,
    components=components,
    envelope=Envelope(outer_size=Vector3D(x=300, y=300, z=250))
)

request = SimulationRequest(
    design_state=design_state,
    sim_type=SimulationType.COMSOL,
    parameters={'model_path': 'e:/Code/msgalaxy/models/satellite_thermal_heatflux.mph'}
)

print(f"\n设计状态:")
print(f"  组件数量: {len(components)}")
print(f"  舱体尺寸: {design_state.envelope.outer_size}")

print(f"\n初始化 COMSOL 驱动器...")
config = {
    'comsol_model': 'e:/Code/msgalaxy/models/satellite_thermal_heatflux.mph',
    'mode': 'dynamic'
}
driver = ComsolDriver(config=config)

print(f"\n运行仿真...")
try:
    result = driver.run_simulation(request)
    print(f"\n✓ 仿真成功!")
    print(f"  最高温度: {result.metrics.get('max_temp', 'N/A')} K")
    print(f"  最低温度: {result.metrics.get('min_temp', 'N/A')} K")
    print(f"  平均温度: {result.metrics.get('avg_temp', 'N/A')} K")
except Exception as e:
    print(f"\n✗ 仿真失败: {e}")
    print(f"\n完整错误堆栈:")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
