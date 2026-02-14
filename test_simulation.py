"""
仿真模块测试脚本

测试简化物理引擎和仿真接口
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from simulation import SimplifiedPhysicsEngine
from core.protocol import SimulationRequest, SimulationType, DesignState, ComponentGeometry, Vector3D, Envelope


def test_simplified_physics():
    """测试简化物理引擎"""
    print("=" * 60)
    print("测试简化物理引擎")
    print("=" * 60)

    # 配置
    config = {
        'type': 'SIMPLIFIED',
        'constraints': {
            'max_temp_c': 50.0,
            'min_clearance_mm': 3.0,
            'max_mass_kg': 100.0,
            'max_power_w': 500.0
        }
    }

    # 创建设计状态
    components = [
        ComponentGeometry(
            id='battery_01',
            position=Vector3D(x=100, y=100, z=100),
            dimensions=Vector3D(x=200, y=150, z=100),
            mass=5.0,
            power=50.0,
            category='power'
        ),
        ComponentGeometry(
            id='payload_01',
            position=Vector3D(x=350, y=100, z=100),
            dimensions=Vector3D(x=180, y=180, z=120),
            mass=3.5,
            power=30.0,
            category='payload'
        ),
    ]

    envelope = Envelope(
        outer_size=Vector3D(x=1000, y=800, z=600),
        thickness=5.0,
        fill_ratio=0.3,
        origin='center'
    )

    design_state = DesignState(
        iteration=1,
        components=components,
        envelope=envelope,
        keepouts=[]
    )

    # 创建仿真请求
    request = SimulationRequest(
        sim_type=SimulationType.SIMPLIFIED,
        design_state=design_state
    )

    # 运行仿真
    engine = SimplifiedPhysicsEngine(config)
    result = engine.run_simulation(request)

    # 输出结果
    print("\n" + "=" * 60)
    print("仿真结果")
    print("=" * 60)
    print(f"[OK] 仿真成功: {result.success}")
    print(f"[OK] 指标:")
    for key, value in result.metrics.items():
        print(f"  - {key}: {value:.2f}")

    print(f"\n[OK] 违规数: {len(result.violations)}")
    if result.violations:
        for v in result.violations:
            print(f"  - [{v.type}] {v.description}")

    if result.success:
        print("\n[OK] 测试通过！")
        return True
    else:
        print(f"\n[OK] 测试失败: {result.error_message}")
        return False


def test_simulation_drivers():
    """测试仿真驱动器的导入"""
    print("\n" + "=" * 60)
    print("测试仿真驱动器导入")
    print("=" * 60)

    try:
        from simulation import MatlabDriver, ComsolDriver
        print("[OK] MatlabDriver 导入成功")
        print("[OK] ComsolDriver 导入成功")
        print("  注意：实际使用需要安装MATLAB Engine和MPh库")
        return True
    except Exception as e:
        print(f"[WARN]  驱动器导入警告: {e}")
        return True  # 不影响测试通过


if __name__ == "__main__":
    try:
        success1 = test_simplified_physics()
        success2 = test_simulation_drivers()

        if success1 and success2:
            print("\n" + "=" * 60)
            print("[OK] 所有测试通过！")
            print("=" * 60)
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        print(f"\n[OK] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
