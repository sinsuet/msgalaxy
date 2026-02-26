#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动创建COMSOL参数化热分析模型

此脚本使用MPh库连接COMSOL，自动创建一个参数化的卫星热分析模型。
模型包含：
- 参数化几何（电池、载荷组件）
- 热传导物理场
- 边界条件
- 网格设置

用法:
    python create_comsol_model.py [output_file.mph]

示例:
    python create_comsol_model.py models/satellite_thermal.mph
"""

import sys
import os
from pathlib import Path

# 设置UTF-8编码
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def create_satellite_thermal_model(output_path: str):
    """
    创建卫星热分析COMSOL模型

    Args:
        output_path: 输出.mph文件路径
    """
    try:
        import mph
    except ImportError:
        print("[错误] MPh库未安装")
        print("请运行: pip install mph")
        return False

    print("=" * 60)
    print("COMSOL模型自动创建工具")
    print("=" * 60)
    print()

    # 1. 连接COMSOL
    print("[1/8] 连接COMSOL服务器...")
    try:
        client = mph.start()
        print("  ✓ COMSOL连接成功")
    except Exception as e:
        print(f"  ✗ COMSOL连接失败: {e}")
        print()
        print("请确保:")
        print("  1. COMSOL Multiphysics已安装")
        print("  2. COMSOL许可证有效")
        print("  3. 没有其他COMSOL实例正在运行")
        return False

    # 2. 创建新模型
    print()
    print("[2/8] 创建新模型...")
    try:
        model = client.create('SatelliteThermal')
        print("  ✓ 模型创建成功")
    except Exception as e:
        print(f"  ✗ 模型创建失败: {e}")
        client.disconnect()
        return False

    # 3. 定义全局参数
    print()
    print("[3/8] 定义全局参数...")
    try:
        # 电池组件参数
        model.parameter('battery_01_x', '0[mm]', description='Battery X position')
        model.parameter('battery_01_y', '0[mm]', description='Battery Y position')
        model.parameter('battery_01_z', '0[mm]', description='Battery Z position')
        model.parameter('battery_01_dx', '200[mm]', description='Battery X dimension')
        model.parameter('battery_01_dy', '150[mm]', description='Battery Y dimension')
        model.parameter('battery_01_dz', '100[mm]', description='Battery Z dimension')
        model.parameter('battery_01_power', '50[W]', description='Battery power dissipation')

        # 载荷组件参数
        model.parameter('payload_01_x', '0[mm]', description='Payload X position')
        model.parameter('payload_01_y', '0[mm]', description='Payload Y position')
        model.parameter('payload_01_z', '150[mm]', description='Payload Z position')
        model.parameter('payload_01_dx', '180[mm]', description='Payload X dimension')
        model.parameter('payload_01_dy', '180[mm]', description='Payload Y dimension')
        model.parameter('payload_01_dz', '120[mm]', description='Payload Z dimension')
        model.parameter('payload_01_power', '30[W]', description='Payload power dissipation')

        # 外壳参数
        model.parameter('envelope_size', '300[mm]', description='Envelope size')
        model.parameter('wall_thickness', '5[mm]', description='Wall thickness')

        print("  ✓ 参数定义完成 (15个参数)")
    except Exception as e:
        print(f"  ✗ 参数定义失败: {e}")
        client.disconnect()
        return False

    # 4. 创建几何
    print()
    print("[4/8] 创建参数化几何...")
    try:
        # 首先创建组件
        model.java.component().create('comp1', True)

        # 创建电池几何
        model.java.component('comp1').geom().create('geom1', 3)
        geom = model.java.component('comp1').geom('geom1')

        # 电池块
        battery = geom.create('battery', 'Block')
        battery.set('size', ['battery_01_dx', 'battery_01_dy', 'battery_01_dz'])
        battery.set('pos', ['battery_01_x', 'battery_01_y', 'battery_01_z'])
        battery.label('Battery')

        # 载荷块
        payload = geom.create('payload', 'Block')
        payload.set('size', ['payload_01_dx', 'payload_01_dy', 'payload_01_dz'])
        payload.set('pos', ['payload_01_x', 'payload_01_y', 'payload_01_z'])
        payload.label('Payload')

        # 外壳
        envelope = geom.create('envelope', 'Block')
        envelope.set('size', ['envelope_size', 'envelope_size', 'envelope_size'])
        envelope.set('pos', ['-envelope_size/2', '-envelope_size/2', '-envelope_size/2'])
        envelope.label('Envelope')

        # 构建几何
        geom.run()

        print("  ✓ 几何创建完成 (3个组件)")
    except Exception as e:
        print(f"  ✗ 几何创建失败: {e}")
        client.disconnect()
        return False

    # 5. 添加物理场
    print()
    print("[5/8] 设置热传导物理场...")
    try:
        # 创建热传导物理场
        ht = model.java.component('comp1').physics().create('ht', 'HeatTransfer', 'geom1')
        ht.label('Heat Transfer in Solids')

        # 电池热源
        hs1 = ht.create('hs1', 'HeatSource', 3)
        hs1.selection().named('geom1_battery_dom')
        hs1.set('Q0', 1, 'battery_01_power/(battery_01_dx*battery_01_dy*battery_01_dz*1e-9)')
        hs1.label('Battery Heat Source')

        # 载荷热源
        hs2 = ht.create('hs2', 'HeatSource', 3)
        hs2.selection().named('geom1_payload_dom')
        hs2.set('Q0', 1, 'payload_01_power/(payload_01_dx*payload_01_dy*payload_01_dz*1e-9)')
        hs2.label('Payload Heat Source')

        # 外壳边界条件（恒温）
        temp = ht.create('temp1', 'TemperatureBoundary', 2)
        temp.selection().named('geom1_envelope_bnd')
        temp.set('T0', '293.15[K]')  # 20°C
        temp.label('Envelope Temperature')

        print("  ✓ 物理场设置完成")
    except Exception as e:
        print(f"  ✗ 物理场设置失败: {e}")
        client.disconnect()
        return False

    # 6. 创建网格
    print()
    print("[6/8] 创建网格...")
    try:
        mesh = model.java.component('comp1').mesh().create('mesh1')
        mesh.automatic(True)
        mesh.autoMeshSize(5)  # Normal mesh
        mesh.run()

        print("  ✓ 网格创建完成")
    except Exception as e:
        print(f"  ✗ 网格创建失败: {e}")
        client.disconnect()
        return False

    # 7. 创建研究
    print()
    print("[7/8] 创建稳态研究...")
    try:
        study = model.java.study().create('std1')
        study.create('stat', 'Stationary')
        study.label('Steady-State Thermal Analysis')

        print("  ✓ 研究创建完成")
    except Exception as e:
        print(f"  ✗ 研究创建失败: {e}")
        client.disconnect()
        return False

    # 8. 保存模型
    print()
    print("[8/8] 保存模型...")
    try:
        # 确保输出目录存在
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # 保存模型
        model.save(output_path)
        print(f"  ✓ 模型已保存: {output_path}")
    except Exception as e:
        print(f"  ✗ 模型保存失败: {e}")
        client.disconnect()
        return False

    # 断开连接
    print()
    print("断开COMSOL连接...")
    client.disconnect()

    print()
    print("=" * 60)
    print("✓ 模型创建成功！")
    print("=" * 60)
    print()
    print("模型信息:")
    print(f"  文件路径: {output_path}")
    print(f"  文件大小: {os.path.getsize(output_path) / 1024:.1f} KB")
    print()
    print("模型包含:")
    print("  - 15个全局参数")
    print("  - 3个几何组件（电池、载荷、外壳）")
    print("  - 热传导物理场")
    print("  - 2个热源（电池、载荷）")
    print("  - 边界条件（外壳恒温20°C）")
    print("  - 自动网格")
    print("  - 稳态研究")
    print()
    print("下一步:")
    print(f"  1. 测试模型: run_with_msgalaxy_env.bat test_comsol.py {output_path}")
    print(f"  2. 在config/system.yaml中配置模型路径")
    print(f"  3. 运行优化: run_with_msgalaxy_env.bat -m api.cli optimize")
    print()

    return True


def main():
    """主函数"""
    # 解析命令行参数
    if len(sys.argv) > 1:
        output_path = sys.argv[1]
    else:
        output_path = 'models/satellite_thermal.mph'

    # 转换为绝对路径
    output_path = os.path.abspath(output_path)

    print(f"输出文件: {output_path}")
    print()

    # 检查文件是否已存在
    if os.path.exists(output_path):
        response = input(f"文件已存在: {output_path}\n是否覆盖? (y/n): ")
        if response.lower() != 'y':
            print("操作已取消")
            return

    # 创建模型
    success = create_satellite_thermal_model(output_path)

    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
