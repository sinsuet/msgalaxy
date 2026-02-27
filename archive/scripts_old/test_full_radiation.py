#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创建并测试完整的辐射模型
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
print("创建并测试完整辐射模型")
print("=" * 80)

try:
    # 连接COMSOL
    print("\n[1/10] 连接COMSOL...")
    client = mph.start()
    print("  ✓ 连接成功")

    # 创建模型
    print("\n[2/10] 创建模型...")
    model = client.create('RadiationTestFull')

    # 定义参数
    print("\n[3/10] 定义参数...")
    model.parameter('T_space', '3[K]')
    model.parameter('emissivity', '0.85')
    print("  ✓ 参数定义完成")

    # 创建几何
    print("\n[4/10] 创建几何...")
    model.java.component().create('comp1', True)
    model.java.component('comp1').geom().create('geom1', 3)
    geom = model.java.component('comp1').geom('geom1')

    block = geom.create('blk1', 'Block')
    block.set('size', ['100', '100', '100'])
    geom.run()
    print("  ✓ 几何创建完成")

    # 创建材料（关键：必须设置epsilon_rad）
    print("\n[5/10] 创建材料...")
    comp = model.java.component('comp1')
    mat = comp.material().create('mat1', 'Common')
    mat.label('TestMaterial')
    mat.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
    mat.propertyGroup('def').set('density', ['2700[kg/m^3]'])
    mat.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])
    mat.propertyGroup('def').set('epsilon_rad', ['emissivity'])  # 使用参数
    mat.selection().all()
    print("  ✓ 材料定义完成（包含epsilon_rad）")

    # 创建物理场
    print("\n[6/10] 创建物理场...")
    ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

    # 添加热源
    hs = ht.create('hs1', 'HeatSource', 3)
    hs.selection().all()
    hs.set('Q0', 1, '1000')  # 1000 W/m³
    print("  ✓ 热源创建完成")

    # 创建辐射边界
    print("\n[7/10] 创建辐射边界...")
    rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
    rad.selection().all()
    # 方法1：使用用户定义值
    # rad.set('epsilon_rad', 1, 'emissivity')
    # 方法2：从材料读取（默认）
    rad.set('Tamb', 'T_space')
    rad.label('Radiation to Deep Space')
    print("  ✓ 辐射边界创建完成（从材料读取epsilon_rad）")

    # 创建网格
    print("\n[8/10] 创建网格...")
    mesh = comp.mesh().create('mesh1')
    mesh.automatic(True)
    mesh.autoMeshSize(5)
    mesh.run()
    print("  ✓ 网格创建完成")

    # 创建研究
    print("\n[9/10] 创建研究...")
    study = model.java.study().create('std1')
    study.create('stat', 'Stationary')
    print("  ✓ 研究创建完成")

    # 尝试求解
    print("\n[10/10] 尝试求解...")
    try:
        model.solve()
        print("  ✓ 求解成功！")

        # 提取结果
        max_temp = float(model.evaluate('max(T)', unit='K'))
        print(f"\n结果:")
        print(f"  最高温度: {max_temp:.2f} K ({max_temp-273.15:.2f} °C)")

    except Exception as e:
        print(f"  ✗ 求解失败: {e}")

    # 保存模型
    output_path = 'models/radiation_test_full.mph'
    model.save(output_path)
    print(f"\n模型已保存: {output_path}")

    client.disconnect()

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)

except Exception as e:
    print(f"\n[错误] {e}")
    import traceback
    traceback.print_exc()
    try:
        client.disconnect()
    except:
        pass
