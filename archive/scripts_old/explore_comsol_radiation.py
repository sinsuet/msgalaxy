#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
探索COMSOL辐射属性的正确设置方法
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
print("COMSOL辐射属性探索")
print("=" * 80)

try:
    # 连接COMSOL
    print("\n[1] 连接COMSOL...")
    client = mph.start()
    print("  ✓ 连接成功")

    # 创建测试模型
    print("\n[2] 创建测试模型...")
    model = client.create('RadiationTest')

    # 创建组件
    model.java.component().create('comp1', True)
    model.java.component('comp1').geom().create('geom1', 3)
    geom = model.java.component('comp1').geom('geom1')

    # 创建简单几何
    block = geom.create('blk1', 'Block')
    block.set('size', ['100', '100', '100'])
    geom.run()

    # 创建材料
    print("\n[3] 测试材料属性...")
    comp = model.java.component('comp1')
    mat = comp.material().create('mat1', 'Common')
    mat.label('TestMaterial')

    # 尝试不同的属性名称
    property_names = [
        'epsilon_rad',
        'emissivity',
        'surfaceEmissivity',
        'epsilon',
        'rho_rad',
        'reflectivity'
    ]

    print("\n测试属性名称:")
    for prop_name in property_names:
        try:
            mat.propertyGroup('def').set(prop_name, ['0.85'])
            print(f"  ✓ {prop_name} - 成功")
        except Exception as e:
            print(f"  ✗ {prop_name} - 失败: {str(e)[:50]}")

    # 创建热传导物理场
    print("\n[4] 创建热传导物理场...")
    ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

    # 尝试创建表面对表面辐射
    print("\n[5] 测试表面对表面辐射...")
    try:
        rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
        rad.selection().all()
        print("  ✓ 表面对表面辐射创建成功")

        # 检查可用的设置
        print("\n[6] 检查辐射边界的可用设置...")
        try:
            # 尝试获取特征信息
            feature = model.java.component('comp1').physics('ht').feature('rad1')
            print(f"  特征类型: {feature.getType()}")

            # 尝试不同的设置方法
            settings_to_try = [
                ('epsilon_rad', 1, '0.85'),
                ('epsilon_rad', 1, 'emissivity'),
                ('epsilon', 1, '0.85'),
                ('Tamb', 'T_space'),
                ('Tamb', '3[K]'),
            ]

            print("\n测试辐射设置:")
            for setting in settings_to_try:
                try:
                    if len(setting) == 3:
                        rad.set(setting[0], setting[1], setting[2])
                        print(f"  ✓ set('{setting[0]}', {setting[1]}, '{setting[2]}') - 成功")
                    else:
                        rad.set(setting[0], setting[1])
                        print(f"  ✓ set('{setting[0]}', '{setting[1]}') - 成功")
                except Exception as e:
                    print(f"  ✗ set{setting} - 失败: {str(e)[:60]}")

        except Exception as e:
            print(f"  检查设置失败: {e}")

    except Exception as e:
        print(f"  ✗ 表面对表面辐射创建失败: {e}")

    # 清理
    client.disconnect()

    print("\n" + "=" * 80)
    print("探索完成")
    print("=" * 80)

except Exception as e:
    print(f"\n[错误] {e}")
    import traceback
    traceback.print_exc()
    try:
        client.disconnect()
    except:
        pass
