#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
深入探索COMSOL材料属性组
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
print("探索COMSOL材料属性组")
print("=" * 80)

try:
    client = mph.start()
    model = client.create('MaterialTest')

    model.java.component().create('comp1', True)
    comp = model.java.component('comp1')

    # 创建材料
    mat = comp.material().create('mat1', 'Common')
    mat.label('TestMaterial')

    print("\n[1] 测试不同的属性组...")

    # 获取可用的属性组
    try:
        prop_groups = mat.propertyGroup().tags()
        print(f"  可用属性组: {list(prop_groups)}")
    except:
        print("  无法获取属性组列表")

    # 测试不同的属性组
    property_groups_to_try = [
        'def',  # 默认
        'SurfaceEmissivity',  # 表面发射率
        'Emissivity',
        'RadiativeProperties',
        'ThermalExpansion'
    ]

    print("\n[2] 尝试创建/使用不同的属性组...")
    for pg_name in property_groups_to_try:
        try:
            # 尝试创建属性组
            try:
                pg = mat.propertyGroup().create(pg_name)
                print(f"  ✓ 创建属性组 '{pg_name}' 成功")
            except:
                # 如果已存在，直接使用
                pg = mat.propertyGroup(pg_name)
                print(f"  ✓ 使用现有属性组 '{pg_name}'")

            # 尝试设置epsilon_rad
            try:
                pg.set('epsilon_rad', ['0.85'])
                print(f"    ✓ 在 '{pg_name}' 中设置 epsilon_rad 成功")
            except Exception as e:
                print(f"    ✗ 在 '{pg_name}' 中设置 epsilon_rad 失败: {str(e)[:50]}")

        except Exception as e:
            print(f"  ✗ 属性组 '{pg_name}' 失败: {str(e)[:50]}")

    # 测试使用内置材料
    print("\n[3] 测试使用COMSOL内置材料...")
    try:
        mat2 = comp.material().create('mat2', 'Common')
        mat2.label('BuiltInMaterial')

        # 尝试从内置材料库加载
        try:
            # 加载铝合金
            mat2.propertyGroup('def').func().create('eta', 'Piecewise')
            mat2.propertyGroup('def').func().create('Cp', 'Piecewise')
            mat2.propertyGroup('def').func().create('rho', 'Analytic')
            mat2.propertyGroup('def').func().create('k', 'Piecewise')
            mat2.propertyGroup('def').func().create('cs', 'Interpolation')
            mat2.propertyGroup('def').func().create('alpha', 'Analytic')
            mat2.propertyGroup('def').set('thermalconductivity', ['k(T[1/K])[W/(m*K)]'])
            mat2.propertyGroup('def').set('density', ['rho(T[1/K])[kg/m^3]'])
            mat2.propertyGroup('def').set('heatcapacity', ['Cp(T[1/K])[J/(kg*K)]'])

            print("  ✓ 内置材料创建成功")
        except Exception as e:
            print(f"  ✗ 内置材料创建失败: {e}")

    except Exception as e:
        print(f"  ✗ 测试内置材料失败: {e}")

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
