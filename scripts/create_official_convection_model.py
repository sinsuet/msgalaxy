#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
使用COMSOL官方对流边界条件创建模型
"""

import sys
import os
import io

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

try:
    import mph
except ImportError:
    print("MPh库未安装")
    sys.exit(1)

print("=" * 80)
print("使用COMSOL官方对流边界条件")
print("=" * 80)

try:
    print("\n[1/8] 连接COMSOL...")
    client = mph.start()
    print("  ✓ COMSOL客户端启动成功")

    print("\n[2/8] 创建模型...")
    model = client.create('OfficialConvection')
    print("  ✓ 模型创建成功")

    # 参数
    print("\n[3/8] 定义参数...")
    model.parameter('ambient_temp', '293.15[K]')
    model.parameter('h_conv', '10[W/(m^2*K)]')

    # 组件参数
    model.parameter('battery_01_x', '0[mm]')
    model.parameter('battery_01_y', '0[mm]')
    model.parameter('battery_01_z', '0[mm]')
    model.parameter('battery_01_dx', '200[mm]')
    model.parameter('battery_01_dy', '150[mm]')
    model.parameter('battery_01_dz', '100[mm]')
    model.parameter('battery_01_power', '50[W]')

    model.parameter('payload_01_x', '0[mm]')
    model.parameter('payload_01_y', '0[mm]')
    model.parameter('payload_01_z', '150[mm]')
    model.parameter('payload_01_dx', '180[mm]')
    model.parameter('payload_01_dy', '180[mm]')
    model.parameter('payload_01_dz', '120[mm]')
    model.parameter('payload_01_power', '30[W]')

    print("  ✓ 参数定义完成")

    # 几何
    print("\n[4/8] 创建几何...")
    model.java.component().create('comp1', True)
    model.java.component('comp1').geom().create('geom1', 3)
    geom = model.java.component('comp1').geom('geom1')

    battery = geom.create('battery', 'Block')
    battery.set('size', ['battery_01_dx', 'battery_01_dy', 'battery_01_dz'])
    battery.set('pos', ['battery_01_x-battery_01_dx/2', 'battery_01_y-battery_01_dy/2', 'battery_01_z'])
    battery.label('Battery')

    payload = geom.create('payload', 'Block')
    payload.set('size', ['payload_01_dx', 'payload_01_dy', 'payload_01_dz'])
    payload.set('pos', ['payload_01_x-payload_01_dx/2', 'payload_01_y-payload_01_dy/2', 'payload_01_z'])
    payload.label('Payload')

    geom.run()
    print("  ✓ 几何创建完成")

    # 材料
    print("\n[5/8] 定义材料...")
    comp = model.java.component('comp1')
    mat = comp.material().create('mat1', 'Common')
    mat.label('Aluminum')
    mat.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
    mat.propertyGroup('def').set('density', ['2700[kg/m^3]'])
    mat.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])
    mat.selection().all()
    print("  ✓ 材料定义完成")

    # 选择集
    print("\n[6/8] 创建选择集...")
    sel_battery = comp.selection().create('sel_battery', 'Explicit')
    sel_battery.geom('geom1', 3)
    sel_battery.set([1])
    sel_battery.label('Battery Domain')

    sel_payload = comp.selection().create('sel_payload', 'Explicit')
    sel_payload.geom('geom1', 3)
    sel_payload.set([2])
    sel_payload.label('Payload Domain')
    print("  ✓ 选择集创建完成")

    # 物理场
    print("\n[7/8] 设置物理场...")
    ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

    # 电池热源
    hs_battery = ht.create('hs_battery', 'HeatSource', 3)
    hs_battery.selection().named('sel_battery')
    hs_battery.set('Q0', 1, 'battery_01_power/(battery_01_dx*battery_01_dy*battery_01_dz*1e-9)')
    hs_battery.label('Battery Heat Source')

    # 载荷热源
    hs_payload = ht.create('hs_payload', 'HeatSource', 3)
    hs_payload.selection().named('sel_payload')
    hs_payload.set('Q0', 1, 'payload_01_power/(payload_01_dx*payload_01_dy*payload_01_dz*1e-9)')
    hs_payload.label('Payload Heat Source')

    # 使用COMSOL官方的Convection边界条件
    print("  [关键] 使用COMSOL官方Convection特征...")
    try:
        # 尝试创建Convection特征
        conv = ht.create('conv1', 'Convection', 2)
        conv.selection().all()
        conv.set('h', 'h_conv')  # 对流系数
        conv.set('Text', 'ambient_temp')  # 外部温度
        conv.label('Convection Boundary')
        print("    ✓ 使用Convection特征")
    except Exception as e:
        print(f"    ⚠ Convection特征失败: {e}")
        print("    尝试使用HeatFlux...")
        # 回退到HeatFlux
        hf = ht.create('hf1', 'HeatFluxBoundary', 2)
        hf.selection().all()
        hf.set('q0', 'h_conv*(ambient_temp-T)')  # 注意：流入为正
        hf.label('Convection (HeatFlux)')
        print("    ✓ 使用HeatFlux模拟对流")

    print("  ✓ 物理场设置完成")

    # 网格
    print("\n[8/8] 创建网格...")
    mesh = comp.mesh().create('mesh1')
    mesh.automatic(True)
    mesh.autoMeshSize(5)
    mesh.run()
    print("  ✓ 网格创建完成")

    # 算子
    print("\n[9/10] 添加算子...")
    maxop = comp.cpl().create('maxop1', 'Maximum')
    maxop.selection().geom('geom1', 3)
    maxop.selection().all()
    maxop.label('Maximum Operator')

    aveop = comp.cpl().create('aveop1', 'Average')
    aveop.selection().geom('geom1', 3)
    aveop.selection().all()
    aveop.label('Average Operator')

    intop = comp.cpl().create('intop1', 'Integration')
    intop.selection().geom('geom1', 3)
    intop.selection().all()
    intop.label('Integration Operator')
    print("  ✓ 算子定义完成")

    # 研究
    print("\n[10/10] 创建研究...")
    study = model.java.study().create('std1')
    study.create('stat', 'Stationary')
    study.label('Steady-State Thermal Analysis')
    print("  ✓ 研究创建完成")

    # 求解
    print("\n" + "=" * 80)
    print("测试求解...")
    print("=" * 80)

    try:
        model.solve()

        max_temp = float(model.evaluate('maxop1(T)', unit='K'))
        avg_temp = float(model.evaluate('aveop1(T)', unit='K'))

        print("\n✓ 求解成功！")
        print(f"  最高温度: {max_temp:.2f} K ({max_temp-273.15:.2f} °C)")
        print(f"  平均温度: {avg_temp:.2f} K ({avg_temp-273.15:.2f} °C)")

        # 验证合理性
        if 250 < max_temp < 400:
            print("  ✓ 温度在合理范围内")
        else:
            print(f"  ⚠ 温度异常")

    except Exception as e:
        print(f"\n⚠ 求解失败: {e}")
        print("  但模型已创建")

    # 保存模型
    output_path = 'models/satellite_thermal_v2.mph'
    model.save(output_path)
    print(f"\n✓ 模型已保存: {output_path}")
    print("  使用COMSOL官方对流边界条件")

    client.disconnect()

    print("\n" + "=" * 80)
    print("✓ 模型创建完成")
    print("=" * 80)
    sys.exit(0)

except Exception as e:
    print(f"\n[错误] {e}")
    import traceback
    traceback.print_exc()
    try:
        client.disconnect()
    except:
        pass
    sys.exit(1)
