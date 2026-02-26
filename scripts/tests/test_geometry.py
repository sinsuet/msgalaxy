"""
简单测试脚本 - 验证几何模块

测试3D布局引擎的基本功能
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from geometry import LayoutEngine


def test_layout_engine():
    """测试布局引擎"""
    print("=" * 60)
    print("测试几何布局引擎")
    print("=" * 60)

    # 配置
    config = {
        'envelope': {
            'auto_envelope': True,
            'fill_ratio': 0.30,
            'size_ratio': [1.7, 1.8, 1.5],
            'shell_thickness_mm': 5.0,
            'origin': 'center'
        },
        'keep_out': [
            {
                'min_mm': [50, 50, 50],
                'max_mm': [100, 100, 100],
                'tag': 'sensor_fov'
            }
        ],
        'synth': {
            'n_parts': 10,
            'dims_min_mm': [50, 50, 50],
            'dims_max_mm': [150, 150, 100],
            'mass_range_kg': [0.5, 3.0],
            'power_range_W': [5, 30],
            'categories': ['payload', 'avionics', 'power'],
            'seed': 42
        },
        'clearance_mm': 10,
        'multistart': 2
    }

    # 创建布局引擎
    engine = LayoutEngine(config)

    # 生成布局
    result = engine.generate_layout()

    # 输出结果
    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)
    print(f"[OK] 已放置部件: {len(result.placed)}")
    print(f"[OK] 未放置部件: {len(result.unplaced)}")
    print(f"[OK] 重合数: {result.overlap_count}")
    print(f"[OK] 总质量: {engine.get_total_mass():.2f} kg")
    print(f"[OK] 总功率: {engine.get_total_power():.2f} W")

    # 显示设计摘要
    print("\n" + engine.get_design_summary())

    if result.overlap_count == 0 and len(result.placed) > 0:
        print("\n[OK] 测试通过！")
        return True
    else:
        print("\n[WARN]  测试警告：存在重合或无法放置部件")
        return False


if __name__ == "__main__":
    try:
        success = test_layout_engine()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[OK] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
