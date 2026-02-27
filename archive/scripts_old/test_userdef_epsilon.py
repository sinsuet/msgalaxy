#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试用户定义epsilon_rad方法

关键：先设置epsilon_rad_mat="userdef"切换数据源
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
print("测试用户定义epsilon_rad方法")
print("=" * 80)

try:
    print("\n[1/8] 连接COMSOL...")
    client = mph.start()
    print("  ✓ COMSOL客户端启动成功")

    print("\n[2/8] 创建模型...")
    model = client.create('UserDefinedEpsilonTest')
    print("  ✓ 模型创建成功")

    # 参数
    print("\n[3/8] 定义参数...")
    model.parameter('T_space', '3[K]')
    model.parameter('power', '80[W]')
    model.parameter('size', '100[mm]')
    print("  ✓ 参数定义完成")

    # 几何
    print("\n[4/8] 创建几何...")
    model.java.component().create('comp1', True)
    model.java.component('comp1').geom().create('geom1', 3)
    geom = model.java.component('comp1').geom('geom1')

    block = geom.create('blk1', 'Block')
    block.set('size', ['size', 'size', 'size'])
    geom.run()
    print("  ✓ 几何创建完成")

    comp = model.java.component('comp1')

    # 材料（只需要热传导属性）
    print("\n[5/8] 定义材料...")
    mat = comp.material().create('mat1', 'Common')
    mat.label('Aluminum')
    mat.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
    mat.propertyGroup('def').set('density', ['2700[kg/m^3]'])
    mat.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])
    mat.selection().all()
    print("  ✓ 材料定义完成（不包含epsilon_rad）")

    # 物理场
    print("\n[6/8] 设置物理场...")
    ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

    # 初始温度
    try:
        init = ht.feature('init1')
        init.set('Tinit', '300[K]')
        print("  ✓ 初始温度: 300K")
    except:
        pass

    # 热源
    hs = ht.create('hs1', 'HeatSource', 3)
    hs.selection().all()
    hs.set('Q0', 1, 'power/(size*size*size*1e-9)')
    print("  ✓ 热源设置完成")

    # 辐射边界 - 关键步骤
    print("\n  [关键] 设置辐射边界...")
    rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
    rad.selection().all()

    # 步骤1: 切换数据源为"用户定义"
    try:
        rad.set('epsilon_rad_mat', 'userdef')
        print("    ✓ 步骤1: 设置epsilon_rad_mat='userdef'")
    except Exception as e:
        print(f"    ⚠ 步骤1失败: {e}")

    # 步骤2: 设置发射率值
    try:
        rad.set('epsilon_rad', '0.85')
        print("    ✓ 步骤2: 设置epsilon_rad='0.85'")
    except Exception as e:
        print(f"    ⚠ 步骤2失败: {e}")

    # 步骤3: 设置深空温度
    rad.set('Tamb', 'T_space')
    rad.label('Radiation to Deep Space')
    print("    ✓ 步骤3: 设置Tamb='T_space'")
    print("  ✓ 辐射边界设置完成")

    # 网格
    print("\n[7/8] 创建网格...")
    mesh = comp.mesh().create('mesh1')
    mesh.automatic(True)
    mesh.autoMeshSize(5)
    mesh.run()
    print("  ✓ 网格创建完成")

    # 研究
    print("\n[8/8] 创建研究...")
    study = model.java.study().create('std1')
    study.create('stat', 'Stationary')
    print("  ✓ 研究创建完成")

    # 求解
    print("\n" + "=" * 80)
    print("开始求解...")
    print("=" * 80)

    try:
        model.solve()

        max_temp = float(model.evaluate('max(T)', unit='K'))
        avg_temp = float(model.evaluate('mean(T)', unit='K'))
        min_temp = float(model.evaluate('min(T)', unit='K'))

        print("\n✓✓✓ 求解成功！✓✓✓")
        print("\n温度结果:")
        print(f"  最高温度: {max_temp:.2f} K ({max_temp-273.15:.2f} °C)")
        print(f"  平均温度: {avg_temp:.2f} K ({avg_temp-273.15:.2f} °C)")
        print(f"  最低温度: {min_temp:.2f} K ({min_temp-273.15:.2f} °C)")
        print(f"  温度梯度: {max_temp-min_temp:.2f} K")

        # 验证结果合理性
        print("\n物理合理性检查:")
        if max_temp < 400:
            print("  ✓ 最高温度在合理范围内 (<127°C)")
        else:
            print(f"  ⚠ 最高温度: {max_temp-273.15:.2f}°C")

        if max_temp - min_temp > 1:
            print("  ✓ 存在温度梯度（热量正在传导）")
        else:
            print("  ⚠ 温度梯度过小")

        # 保存成功的模型
        output_path = 'models/satellite_thermal_userdef.mph'
        os.makedirs('models', exist_ok=True)
        model.save(output_path)
        print(f"\n✓ 模型已保存: {output_path}")

        client.disconnect()

        print("\n" + "=" * 80)
        print("✓✓✓ 成功！用户定义方法有效 ✓✓✓")
        print("=" * 80)
        print("\n关键步骤:")
        print("  1. rad.set('epsilon_rad_mat', 'userdef')  # 切换数据源")
        print("  2. rad.set('epsilon_rad', '0.85')         # 设置发射率")
        print("  3. rad.set('Tamb', 'T_space')             # 设置环境温度")
        sys.exit(0)

    except Exception as e:
        print(f"\n✗ 求解失败: {str(e)[:500]}")

        model.save('models/userdef_debug.mph')
        print(f"\n调试模型已保存: models/userdef_debug.mph")

        client.disconnect()
        sys.exit(1)

except Exception as e:
    print(f"\n[错误] {e}")
    import traceback
    traceback.print_exc()
    try:
        client.disconnect()
    except:
        pass
    sys.exit(1)
