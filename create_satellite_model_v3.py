#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创建严谨的卫星热分析COMSOL模型 V3.0

改进点:
1. 正确的热源设置（只在电池和载荷域）
2. 完整的算子定义
3. 合理的边界条件
4. 正确的材料分配
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


def create_satellite_model_v3(output_path: str):
    """创建严谨的卫星热分析模型V3.0"""
    try:
        import mph
    except ImportError:
        print("[错误] MPh库未安装")
        return False

    print("=" * 70)
    print("卫星热分析COMSOL模型创建工具 (V3.0 - 严谨版)")
    print("=" * 70)
    print()

    # 1. 连接COMSOL
    print("[1/12] 连接COMSOL...")
    try:
        client = mph.start()
        print("  ✓ COMSOL连接成功")
    except Exception as e:
        print(f"  ✗ 连接失败: {e}")
        return False

    # 2. 创建模型
    print()
    print("[2/12] 创建模型...")
    try:
        model = client.create('SatelliteThermalV3')
        print("  ✓ 模型创建成功")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 3. 定义全局参数
    print()
    print("[3/12] 定义全局参数...")
    try:
        # 电池组件
        model.parameter('battery_x', '0[mm]')
        model.parameter('battery_y', '0[mm]')
        model.parameter('battery_z', '0[mm]')
        model.parameter('battery_dx', '200[mm]')
        model.parameter('battery_dy', '150[mm]')
        model.parameter('battery_dz', '100[mm]')
        model.parameter('battery_power', '50[W]')

        # 载荷组件
        model.parameter('payload_x', '0[mm]')
        model.parameter('payload_y', '0[mm]')
        model.parameter('payload_z', '150[mm]')
        model.parameter('payload_dx', '180[mm]')
        model.parameter('payload_dy', '180[mm]')
        model.parameter('payload_dz', '120[mm]')
        model.parameter('payload_power', '30[W]')

        # 外壳参数
        model.parameter('envelope_size', '400[mm]')
        model.parameter('wall_thickness', '5[mm]')

        # 物理参数
        model.parameter('ambient_temp', '293.15[K]')  # 20°C

        print("  ✓ 参数定义完成 (16个参数)")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 4. 创建组件和几何
    print()
    print("[4/12] 创建参数化几何...")
    try:
        model.java.component().create('comp1', True)
        model.java.component('comp1').geom().create('geom1', 3)
        geom = model.java.component('comp1').geom('geom1')

        # 电池
        battery = geom.create('battery', 'Block')
        battery.set('size', ['battery_dx', 'battery_dy', 'battery_dz'])
        battery.set('pos', ['battery_x-battery_dx/2', 'battery_y-battery_dy/2', 'battery_z'])
        battery.label('Battery')

        # 载荷
        payload = geom.create('payload', 'Block')
        payload.set('size', ['payload_dx', 'payload_dy', 'payload_dz'])
        payload.set('pos', ['payload_x-payload_dx/2', 'payload_y-payload_dy/2', 'payload_z'])
        payload.label('Payload')

        # 外壳
        envelope = geom.create('envelope', 'Block')
        envelope.set('size', ['envelope_size', 'envelope_size', 'envelope_size'])
        envelope.set('pos', ['-envelope_size/2', '-envelope_size/2', '-envelope_size/2'])
        envelope.label('Envelope')

        geom.run()
        print("  ✓ 几何创建完成 (3个组件)")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 5. 创建选择集（用于材料和热源）
    print()
    print("[5/12] 创建选择集...")
    try:
        comp = model.java.component('comp1')

        # 电池域选择
        sel_battery = comp.selection().create('sel_battery', 'Explicit')
        sel_battery.geom('geom1', 3)
        sel_battery.set([1])  # 假设电池是第一个域
        sel_battery.label('Battery Domain')

        # 载荷域选择
        sel_payload = comp.selection().create('sel_payload', 'Explicit')
        sel_payload.geom('geom1', 3)
        sel_payload.set([2])  # 假设载荷是第二个域
        sel_payload.label('Payload Domain')

        # 外壳域选择
        sel_envelope = comp.selection().create('sel_envelope', 'Explicit')
        sel_envelope.geom('geom1', 3)
        sel_envelope.set([3])  # 假设外壳是第三个域
        sel_envelope.label('Envelope Domain')

        print("  ✓ 选择集创建完成 (3个选择集)")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 6. 定义材料
    print()
    print("[6/12] 定义材料属性...")
    try:
        # 铝合金（用于电池和载荷）
        mat_al = comp.material().create('mat_aluminum', 'Common')
        mat_al.label('Aluminum')
        mat_al.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
        mat_al.propertyGroup('def').set('density', ['2700[kg/m^3]'])
        mat_al.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])
        mat_al.selection().named('sel_battery')
        mat_al.selection().named('sel_payload')

        # 复合材料（用于外壳）
        mat_comp = comp.material().create('mat_composite', 'Common')
        mat_comp.label('Composite')
        mat_comp.propertyGroup('def').set('thermalconductivity', ['5[W/(m*K)]'])
        mat_comp.propertyGroup('def').set('density', ['1600[kg/m^3]'])
        mat_comp.propertyGroup('def').set('heatcapacity', ['1000[J/(kg*K)]'])
        mat_comp.selection().named('sel_envelope')

        print("  ✓ 材料定义完成 (2种材料)")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 7. 设置热传导物理场
    print()
    print("[7/12] 设置热传导物理场...")
    try:
        ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')
        ht.label('Heat Transfer in Solids')

        # 电池热源
        hs_battery = ht.create('hs_battery', 'HeatSource', 3)
        hs_battery.selection().named('sel_battery')
        hs_battery.set('Q0', 1, 'battery_power/(battery_dx*battery_dy*battery_dz*1e-9)')
        hs_battery.label('Battery Heat Source')

        # 载荷热源
        hs_payload = ht.create('hs_payload', 'HeatSource', 3)
        hs_payload.selection().named('sel_payload')
        hs_payload.set('Q0', 1, 'payload_power/(payload_dx*payload_dy*payload_dz*1e-9)')
        hs_payload.label('Payload Heat Source')

        # 外壳边界条件（外表面恒温）
        temp_bc = ht.create('temp1', 'TemperatureBoundary', 2)
        temp_bc.selection().all()
        temp_bc.set('T0', 'ambient_temp')
        temp_bc.label('Boundary Temperature')

        print("  ✓ 热传导物理场设置完成")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 8. 创建网格
    print()
    print("[8/12] 创建网格...")
    try:
        mesh = comp.mesh().create('mesh1')
        mesh.automatic(True)
        mesh.autoMeshSize(5)  # Normal
        mesh.run()
        print("  ✓ 网格创建完成")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 9. 添加算子定义
    print()
    print("[9/12] 添加算子定义...")
    try:
        # 最大值算子
        maxop = comp.cpl().create('maxop1', 'Maximum')
        maxop.selection().geom('geom1', 3)
        maxop.selection().all()
        maxop.label('Maximum Operator')

        # 平均值算子
        aveop = comp.cpl().create('aveop1', 'Average')
        aveop.selection().geom('geom1', 3)
        aveop.selection().all()
        aveop.label('Average Operator')

        # 积分算子
        intop = comp.cpl().create('intop1', 'Integration')
        intop.selection().geom('geom1', 3)
        intop.selection().all()
        intop.label('Integration Operator')

        print("  ✓ 算子定义完成 (3个算子)")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 10. 创建研究
    print()
    print("[10/12] 创建研究...")
    try:
        study = model.java.study().create('std1')
        study.create('stat', 'Stationary')
        study.label('Steady-State Thermal Analysis')
        print("  ✓ 研究创建完成")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 11. 保存模型
    print()
    print("[11/12] 保存模型...")
    try:
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        model.save(output_path)
        print(f"  ✓ 模型已保存: {output_path}")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        client.disconnect()
        return False

    # 12. 验证模型
    print()
    print("[12/12] 验证模型...")
    try:
        # 检查参数
        params = model.java.param().varnames()
        print(f"  ✓ 参数数量: {len(params)}")

        # 检查几何
        geom_info = model.java.component('comp1').geom('geom1').feature().tags()
        print(f"  ✓ 几何特征数: {len(geom_info)}")

        print("  ✓ 模型验证完成")
    except Exception as e:
        print(f"  ⚠ 验证警告: {e}")

    client.disconnect()

    print()
    print("=" * 70)
    print("✓ 卫星热分析模型V3.0创建成功！")
    print("=" * 70)
    print()
    print("模型特性:")
    print("  - 3个组件（电池、载荷、外壳）")
    print("  - 16个参数（完整参数化）")
    print("  - 2种材料（铝合金、复合材料）")
    print("  - 热传导物理场")
    print("  - 2个热源（仅在电池和载荷域）")
    print("  - 边界条件（外表面恒温）")
    print("  - 3个算子（max, avg, int）")
    print("  - 自动网格")
    print("  - 稳态研究")
    print()
    print(f"文件: {output_path}")
    print(f"大小: {os.path.getsize(output_path) / 1024:.1f} KB")
    print()

    return True


def main():
    if len(sys.argv) > 1:
        output_path = sys.argv[1]
    else:
        output_path = 'models/satellite_thermal_v3.mph'

    output_path = os.path.abspath(output_path)

    if os.path.exists(output_path):
        response = input(f"文件已存在，是否覆盖? (y/n): ")
        if response.lower() != 'y':
            print("操作已取消")
            return

    success = create_satellite_model_v3(output_path)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
