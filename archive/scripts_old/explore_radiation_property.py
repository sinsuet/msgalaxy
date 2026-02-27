#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
探索COMSOL材料属性和辐射边界的关系
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
print("探索COMSOL辐射属性设置")
print("=" * 80)

try:
    client = mph.start()
    model = client.create('RadiationPropertyTest')

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

    # 测试1: 检查材料属性组
    print("\n[测试1] 检查材料可用的属性组...")
    mat = comp.material().create('mat1', 'Common')
    mat.label('TestMaterial')

    # 获取所有属性组
    try:
        prop_groups = mat.propertyGroup().tags()
        print(f"  可用属性组: {list(prop_groups)}")
    except:
        print("  无法获取属性组列表")

    # 测试2: 尝试不同的属性组设置epsilon_rad
    print("\n[测试2] 在不同属性组中设置epsilon_rad...")

    # 在def组中设置
    try:
        mat.propertyGroup('def').set('epsilon_rad', ['0.85'])
        print("  ✓ 在'def'组中设置epsilon_rad成功")
    except Exception as e:
        print(f"  ✗ 在'def'组中设置失败: {e}")

    # 设置基本热物性
    mat.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
    mat.propertyGroup('def').set('density', ['2700[kg/m^3]'])
    mat.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])
    mat.selection().all()

    # 测试3: 检查材料属性是否真的被设置
    print("\n[测试3] 验证材料属性...")
    try:
        # 尝试读取epsilon_rad
        epsilon_value = mat.propertyGroup('def').get('epsilon_rad')
        print(f"  ✓ epsilon_rad值: {epsilon_value}")
    except Exception as e:
        print(f"  ✗ 无法读取epsilon_rad: {e}")

    # 测试4: 创建物理场和辐射边界
    print("\n[测试4] 创建辐射边界...")
    ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

    # 热源
    hs = ht.create('hs1', 'HeatSource', 3)
    hs.selection().all()
    hs.set('Q0', 1, '1000')

    # 辐射边界
    rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
    rad.selection().all()
    rad.set('Tamb', 'T_space')
    rad.label('Radiation Test')

    print("  ✓ 辐射边界创建完成")

    # 测试5: 检查辐射边界的属性
    print("\n[测试5] 检查辐射边界属性...")
    try:
        # 尝试获取辐射边界的epsilon设置
        epsilon_mode = rad.getString('epsilon_rad', 0)
        print(f"  epsilon_rad模式: {epsilon_mode}")
    except Exception as e:
        print(f"  无法获取epsilon_rad模式: {e}")

    # 测试6: 尝试显式设置辐射边界的epsilon_rad为"from material"
    print("\n[测试6] 尝试不同的epsilon_rad设置方式...")

    # 方式A: 不设置（默认）
    print("  方式A: 不设置epsilon_rad（当前状态）")

    # 方式B: 设置为0（从材料读取）
    try:
        rad.set('epsilon_rad', 0, '0.85')
        print("  方式B: rad.set('epsilon_rad', 0, '0.85') - 成功")
    except Exception as e:
        print(f"  方式B失败: {e}")

    # 方式C: 设置为字符串
    try:
        rad.set('epsilon_rad', '0.85')
        print("  方式C: rad.set('epsilon_rad', '0.85') - 成功")
    except Exception as e:
        print(f"  方式C失败: {e}")

    # 测试7: 尝试求解
    print("\n[测试7] 尝试求解...")
    mesh = comp.mesh().create('mesh1')
    mesh.automatic(True)
    mesh.autoMeshSize(5)
    mesh.run()

    study = model.java.study().create('std1')
    study.create('stat', 'Stationary')

    try:
        model.solve()
        print("  ✓ 求解成功！")
        max_temp = float(model.evaluate('max(T)', unit='K'))
        print(f"  最高温度: {max_temp:.2f} K ({max_temp-273.15:.2f} °C)")
    except Exception as e:
        print(f"  ✗ 求解失败: {str(e)[:200]}")

    # 保存模型
    model.save('models/radiation_property_test.mph')
    print("\n模型已保存: models/radiation_property_test.mph")

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
