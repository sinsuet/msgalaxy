#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试使用COMSOL内置材料库
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
print("测试COMSOL内置材料库")
print("=" * 80)

try:
    client = mph.start()
    model = client.create('BuiltInMaterialTest')

    # 参数
    model.parameter('T_space', '3[K]')

    # 几何
    model.java.component().create('comp1', True)
    model.java.component('comp1').geom().create('geom1', 3)
    geom = model.java.component('comp1').geom('geom1')
    block = geom.create('blk1', 'Block')
    block.set('size', ['100', '100', '100'])
    geom.run()

    comp = model.java.component('comp1')

    # 尝试使用COMSOL内置材料库
    print("\n[方法1] 使用COMSOL内置材料...")
    try:
        # 创建材料链接到内置库
        mat = comp.material().create('mat1', 'Common')
        mat.label('Aluminum (Built-in)')

        # 尝试从内置库加载铝材料
        # COMSOL内置材料路径通常是 'Built-In/Aluminum'
        try:
            mat.materialModel('def').set('material', 'Aluminum')
            print("  ✓ 加载内置铝材料成功")
        except Exception as e:
            print(f"  ✗ 加载内置材料失败: {e}")
            # 手动设置属性
            mat.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
            mat.propertyGroup('def').set('density', ['2700[kg/m^3]'])
            mat.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])
            mat.propertyGroup('def').set('emissivity', ['0.85'])  # 尝试使用emissivity
            print("  ✓ 手动设置材料属性（使用emissivity）")

        mat.selection().all()

    except Exception as e:
        print(f"  ✗ 方法1失败: {e}")

    # 物理场
    print("\n[创建物理场]...")
    ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

    # 热源
    hs = ht.create('hs1', 'HeatSource', 3)
    hs.selection().all()
    hs.set('Q0', 1, '1000')
    print("  ✓ 热源创建完成")

    # 辐射边界 - 尝试使用Diffuse Surface特征
    print("\n[创建辐射边界]...")
    try:
        # 方法A: 使用Diffuse Surface（漫反射表面）
        rad = ht.create('ds1', 'DiffuseSurface', 2)
        rad.selection().all()
        rad.set('Tamb', 'T_space')
        rad.label('Diffuse Surface Radiation')
        print("  ✓ 使用Diffuse Surface特征")
    except Exception as e:
        print(f"  ✗ Diffuse Surface失败: {e}")
        # 回退到Surface-to-Surface Radiation
        rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
        rad.selection().all()
        rad.set('Tamb', 'T_space')
        rad.label('Surface-to-Surface Radiation')
        print("  ✓ 使用Surface-to-Surface Radiation")

    # 网格
    print("\n[创建网格]...")
    mesh = comp.mesh().create('mesh1')
    mesh.automatic(True)
    mesh.autoMeshSize(5)
    mesh.run()
    print("  ✓ 网格创建完成")

    # 研究
    print("\n[创建研究]...")
    study = model.java.study().create('std1')
    study.create('stat', 'Stationary')
    print("  ✓ 研究创建完成")

    # 求解
    print("\n[求解]...")
    try:
        model.solve()
        print("  ✓ 求解成功！")
        max_temp = float(model.evaluate('max(T)', unit='K'))
        avg_temp = float(model.evaluate('mean(T)', unit='K'))
        print(f"  最高温度: {max_temp:.2f} K ({max_temp-273.15:.2f} °C)")
        print(f"  平均温度: {avg_temp:.2f} K ({avg_temp-273.15:.2f} °C)")

        model.save('models/builtin_material_success.mph')
        print("\n✓ 成功！模型已保存: models/builtin_material_success.mph")

    except Exception as e:
        print(f"  ✗ 求解失败: {str(e)[:300]}")
        model.save('models/builtin_material_debug.mph')
        print("\n调试模型已保存: models/builtin_material_debug.mph")

    client.disconnect()

except Exception as e:
    print(f"\n[错误] {e}")
    import traceback
    traceback.print_exc()
    try:
        client.disconnect()
    except:
        pass
    sys.exit(1)
