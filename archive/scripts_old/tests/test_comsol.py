"""
COMSOL仿真接入测试

测试COMSOL Multiphysics真实物理仿真集成
"""

import sys
import io
from pathlib import Path

# 修复Windows控制台编码问题
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from simulation.comsol_driver import ComsolDriver
from core.protocol import (
    SimulationRequest,
    SimulationType,
    DesignState,
    ComponentGeometry,
    Vector3D,
    Envelope
)


def test_comsol_import():
    """测试MPh库导入"""
    print("="*60)
    print("测试1: MPh库导入")
    print("="*60)

    try:
        import mph
        print("[OK] MPh库导入成功")
        print(f"  MPh版本: {mph.__version__ if hasattr(mph, '__version__') else 'unknown'}")
        return True
    except ImportError as e:
        print(f"[FAIL] MPh库未安装: {e}")
        print("\n安装方法:")
        print("  pip install mph")
        print("\n注意事项:")
        print("  1. 需要COMSOL Multiphysics已安装")
        print("  2. 默认路径: D:\\Program Files\\COMSOL63")
        print("  3. 需要有效的COMSOL许可证")
        return False


def test_comsol_connection(model_file: str = None):
    """测试COMSOL连接"""
    print("\n" + "="*60)
    print("测试2: COMSOL连接")
    print("="*60)

    if not model_file:
        print("[SKIP] 未提供COMSOL模型文件路径")
        print("\n使用方法:")
        print("  python test_comsol.py <model_file.mph>")
        return False

    if not Path(model_file).exists():
        print(f"[FAIL] 模型文件不存在: {model_file}")
        return False

    try:
        config = {
            'comsol_model': model_file,
            'comsol_parameters': [
                'battery_01_x', 'battery_01_y', 'battery_01_z',
                'battery_01_power',
                'payload_01_x', 'payload_01_y', 'payload_01_z',
                'payload_01_power'
            ]
        }

        driver = ComsolDriver(config)

        print(f"[1/3] 连接COMSOL服务器...")
        driver.connect()
        print("[OK] COMSOL连接成功")

        print(f"\n[2/3] 模型信息:")
        print(f"  模型文件: {model_file}")
        print(f"  参数数量: {len(config['comsol_parameters'])}")

        print(f"\n[3/3] 断开连接...")
        driver.disconnect()
        print("[OK] 连接已关闭")

        return True

    except Exception as e:
        print(f"[FAIL] COMSOL连接失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_comsol_simulation(model_file: str = None):
    """测试COMSOL仿真运行"""
    print("\n" + "="*60)
    print("测试3: COMSOL仿真运行")
    print("="*60)

    if not model_file or not Path(model_file).exists():
        print("[SKIP] 需要有效的COMSOL模型文件")
        return False

    try:
        # 根据模型文件名选择参数配置
        if 'simple' in model_file.lower():
            # 简化模型参数
            comsol_params = ['test_x', 'test_y', 'test_z', 'test_size', 'test_power']
        else:
            # 完整模型参数
            comsol_params = [
                'battery_01_x', 'battery_01_y', 'battery_01_z',
                'battery_01_power',
                'payload_01_x', 'payload_01_y', 'payload_01_z',
                'payload_01_power'
            ]

        # 配置
        config = {
            'comsol_model': model_file,
            'comsol_parameters': comsol_params,
            'constraints': {
                'max_temp_c': 50.0,
                'max_stress_mpa': 100.0
            }
        }

        driver = ComsolDriver(config)
        driver.connect()

        # 创建测试设计状态
        print("[1/4] 创建测试设计状态...")
        components = [
            ComponentGeometry(
                id='battery_01',
                position=Vector3D(x=0.0, y=0.0, z=0.0),
                dimensions=Vector3D(x=200.0, y=150.0, z=100.0),
                rotation=Vector3D(x=0, y=0, z=0),
                mass=5.0,
                power=50.0,
                category='power'
            ),
            ComponentGeometry(
                id='payload_01',
                position=Vector3D(x=0.0, y=0.0, z=150.0),
                dimensions=Vector3D(x=180.0, y=180.0, z=120.0),
                rotation=Vector3D(x=0, y=0, z=0),
                mass=3.5,
                power=30.0,
                category='payload'
            )
        ]

        design_state = DesignState(
            iteration=0,
            components=components,
            envelope=Envelope(
                outer_size=Vector3D(x=300, y=300, z=400),
                inner_size=Vector3D(x=290, y=290, z=390),
                thickness=5.0
            )
        )
        print("[OK] 设计状态创建成功")

        # 创建仿真请求
        print("\n[2/4] 创建仿真请求...")
        request = SimulationRequest(
            sim_type=SimulationType.COMSOL,
            design_state=design_state,
            parameters={}
        )
        print("[OK] 仿真请求创建成功")

        # 运行仿真
        print("\n[3/4] 运行COMSOL仿真...")
        print("  (这可能需要几分钟...)")
        result = driver.run_simulation(request)

        if result.success:
            print("[OK] 仿真成功完成")
        else:
            print(f"[FAIL] 仿真失败: {result.error_message}")
            return False

        # 显示结果
        print("\n[4/4] 仿真结果:")
        for key, value in result.metrics.items():
            print(f"  {key}: {value:.2f}")

        print(f"\n违规数: {len(result.violations)}")
        if result.violations:
            print("违规详情:")
            for v in result.violations:
                print(f"  - [{v.type}] {v.description}")

        driver.disconnect()

        print("\n" + "="*60)
        print("[SUCCESS] COMSOL仿真测试通过！")
        print("="*60)

        return True

    except Exception as e:
        print(f"[FAIL] COMSOL仿真测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    print("\n" + "="*60)
    print("COMSOL仿真接入测试")
    print("="*60)

    # 获取模型文件路径
    model_file = None
    if len(sys.argv) > 1:
        model_file = sys.argv[1]
        print(f"\n模型文件: {model_file}")
    else:
        print("\n未提供模型文件，将跳过连接和仿真测试")
        print("使用方法: python test_comsol.py <model_file.mph>")

    # 运行测试
    results = []

    # 测试1: MPh库导入
    results.append(("MPh库导入", test_comsol_import()))

    # 测试2: COMSOL连接
    if model_file:
        results.append(("COMSOL连接", test_comsol_connection(model_file)))

        # 测试3: COMSOL仿真
        results.append(("COMSOL仿真", test_comsol_simulation(model_file)))

    # 总结
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)

    for name, success in results:
        status = "[OK]" if success else "[FAIL]"
        print(f"{status} {name}")

    passed = sum(1 for _, s in results if s)
    total = len(results)

    print(f"\n通过: {passed}/{total}")

    if passed == total:
        print("\n[SUCCESS] 所有测试通过！")
        return 0
    else:
        print(f"\n[FAIL] {total - passed} 个测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
