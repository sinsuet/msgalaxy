#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创建最小可工作的辐射模型
严格按照COMSOL要求设置
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
print("创建最小可工作辐射模型")
print("=" * 80)

try:
    client = mph.start()
    model = client.create('MinimalRadiation')

    # 参数
    model.parameter('T_space', '3[K]')

    # 几何
    model.java.component().create('comp1', True)
    model.java.component('comp1').geom().create('geom1', 3)
    geom = model.java.component('comp1').geom('geom1')

    block = geom.create('blk1', 'Block')
    block.set('size', ['100', '100', '100'])
    geom.run()

    # 材料 - 关键：必须先定义材料，再应用到域
    comp = model.java.component('comp1')
    mat = comp.material().create('mat1', 'Common')
    mat.label('Aluminum')

    # 设置所有必需的热物性
    mat.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
    mat.propertyGroup('def').set('density', ['2700[kg/m^3]'])
    mat.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])

    # 关键：设置辐射发射率
    mat.propertyGroup('def').set('epsilon_rad', ['0.85'])

    # 关键：应用材料到所有域
    mat.selection().all()

    print("✓ 材料定义完成（包含epsilon_rad=0.85）")

    # 物理场
    ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

    # 热源
    hs = ht.create('hs1', 'HeatSource', 3)
    hs.selection().all()
    hs.set('Q0', 1, '1000')

    # 辐射边界 - 关键：不要显式设置epsilon_rad，让它从材料读取
    rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
    rad.selection().all()
    rad.set('Tamb', 'T_space')
    rad.label('Radiation to Deep Space')

    print("✓ 辐射边界创建完成（从材料读取epsilon_rad）")

    # 网格
    mesh = comp.mesh().create('mesh1')
    mesh.automatic(True)
    mesh.autoMeshSize(5)
    mesh.run()

    # 研究
    study = model.java.study().create('std1')
    study.create('stat', 'Stationary')

    print("\n尝试求解...")
    try:
        model.solve()
        max_temp = float(model.evaluate('max(T)', unit='K'))
        print(f"✓ 求解成功！")
        print(f"  最高温度: {max_temp:.2f} K ({max_temp-273.15:.2f} °C)")

        # 保存成功的模型
        output_path = 'models/satellite_thermal_v2.mph'
        model.save(output_path)
        print(f"\n✓ 模型已保存: {output_path}")

        client.disconnect()
        sys.exit(0)

    except Exception as e:
        print(f"✗ 求解失败: {e}")

        # 保存失败的模型用于调试
        model.save('models/radiation_debug.mph')
        print("调试模型已保存: models/radiation_debug.mph")

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
