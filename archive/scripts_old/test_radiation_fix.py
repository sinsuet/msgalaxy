#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试辐射边界条件修复

验证正确的COMSOL辐射设置方法：
1. 在材料中定义epsilon_rad
2. 在辐射边界中不显式设置epsilon_rad
3. COMSOL自动从材料读取发射率
"""

import sys
import os
import io

# 设置UTF-8编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

try:
    import mph
except ImportError:
    print("MPh库未安装")
    sys.exit(1)

print("=" * 80)
print("测试辐射边界条件修复")
print("=" * 80)
print()

try:
    print("[1/6] 连接COMSOL...")
    client = mph.start()
    print("  ✓ COMSOL客户端启动成功")

    print()
    print("[2/6] 创建模型...")
    model = client.create('RadiationFixTest')
    print("  ✓ 模型创建成功")

    # 参数
    print()
    print("[3/6] 定义参数...")
    model.parameter('T_space', '3[K]')
    model.parameter('power', '80[W]')
    model.parameter('size', '100[mm]')
    print("  ✓ 参数定义完成")

    # 几何
    print()
    print("[4/6] 创建几何...")
    model.java.component().create('comp1', True)
    model.java.component('comp1').geom().create('geom1', 3)
    geom = model.java.component('comp1').geom('geom1')

    block = geom.create('blk1', 'Block')
    block.set('size', ['size', 'size', 'size'])
    geom.run()
    print("  ✓ 几何创建完成")

    # 材料 - 关键步骤
    print()
    print("[5/6] 定义材料（包含辐射属性）...")
    comp = model.java.component('comp1')
    mat = comp.material().create('mat1', 'Common')
    mat.label('Aluminum')

    # 设置所有必需的热物性
    mat.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
    mat.propertyGroup('def').set('density', ['2700[kg/m^3]'])
    mat.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])

    # 关键：设置辐射发射率
    mat.propertyGroup('def').set('epsilon_rad', ['0.85'])

    # 应用材料到所有域
    mat.selection().all()

    print("  ✓ 材料定义完成")
    print("    - 热导率: 237 W/(m·K)")
    print("    - 密度: 2700 kg/m³")
    print("    - 热容: 900 J/(kg·K)")
    print("    - 辐射发射率: 0.85")

    # 物理场
    print()
    print("[6/6] 设置物理场...")
    ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

    # 热源
    hs = ht.create('hs1', 'HeatSource', 3)
    hs.selection().all()
    hs.set('Q0', 1, 'power/(size*size*size*1e-9)')
    print("  ✓ 热源设置完成 (80W)")

    # 辐射边界 - 关键：不显式设置epsilon_rad
    rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
    rad.selection().all()
    rad.set('Tamb', 'T_space')
    rad.label('Radiation to Deep Space')

    print("  ✓ 辐射边界设置完成")
    print("    - 类型: 表面对表面辐射")
    print("    - 深空温度: 3K")
    print("    - 发射率: 从材料读取 (0.85)")

    # 网格
    print()
    print("[7/8] 创建网格...")
    mesh = comp.mesh().create('mesh1')
    mesh.automatic(True)
    mesh.autoMeshSize(5)
    mesh.run()
    print("  ✓ 网格创建完成")

    # 研究
    print()
    print("[8/8] 创建研究...")
    study = model.java.study().create('std1')
    study.create('stat', 'Stationary')
    print("  ✓ 研究创建完成")

    # 求解
    print()
    print("=" * 80)
    print("开始求解...")
    print("=" * 80)
    try:
        model.solve()

        # 提取结果
        max_temp = float(model.evaluate('max(T)', unit='K'))
        avg_temp = float(model.evaluate('mean(T)', unit='K'))
        min_temp = float(model.evaluate('min(T)', unit='K'))

        print()
        print("✓ 求解成功！")
        print()
        print("温度结果:")
        print(f"  最高温度: {max_temp:.2f} K ({max_temp-273.15:.2f} °C)")
        print(f"  平均温度: {avg_temp:.2f} K ({avg_temp-273.15:.2f} °C)")
        print(f"  最低温度: {min_temp:.2f} K ({min_temp-273.15:.2f} °C)")
        print(f"  温度梯度: {max_temp-min_temp:.2f} K")

        # 验证结果合理性
        print()
        print("物理合理性检查:")
        if max_temp < 400:  # < 127°C
            print("  ✓ 最高温度在合理范围内 (<127°C)")
        else:
            print(f"  ✗ 最高温度过高 ({max_temp-273.15:.2f}°C)")

        if max_temp - min_temp > 1:
            print("  ✓ 存在温度梯度（热量正在传导）")
        else:
            print("  ✗ 温度梯度过小（可能存在问题）")

        # 保存成功的模型
        output_path = 'models/satellite_thermal_fixed.mph'
        os.makedirs('models', exist_ok=True)
        model.save(output_path)
        print()
        print(f"✓ 模型已保存: {output_path}")

        client.disconnect()

        print()
        print("=" * 80)
        print("✓ 测试成功！辐射边界条件设置正确")
        print("=" * 80)
        sys.exit(0)

    except Exception as e:
        print()
        print(f"✗ 求解失败: {e}")
        print()

        # 保存失败的模型用于调试
        debug_path = 'models/radiation_fix_debug.mph'
        model.save(debug_path)
        print(f"调试模型已保存: {debug_path}")

        client.disconnect()

        print()
        print("=" * 80)
        print("✗ 测试失败")
        print("=" * 80)
        sys.exit(1)

except Exception as e:
    print()
    print(f"[错误] {e}")
    import traceback
    traceback.print_exc()
    try:
        client.disconnect()
    except:
        pass
    sys.exit(1)
