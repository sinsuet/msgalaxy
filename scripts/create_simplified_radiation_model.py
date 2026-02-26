#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创建简化的COMSOL模型 - 使用辐射热流边界条件

避免复杂的表面对表面辐射，使用简化的Stefan-Boltzmann辐射边界
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.protocol import DesignState, ComponentGeometry, Vector3D, Envelope
from core.logger import get_logger

logger = get_logger(__name__)


def create_simplified_radiation_model(output_path: str):
    """创建简化的辐射模型"""
    try:
        import mph
    except ImportError:
        print("MPh库未安装")
        return False

    print("=" * 80)
    print("创建简化辐射COMSOL模型")
    print("=" * 80)

    try:
        # 1. 连接COMSOL
        print("\n[1/9] 连接COMSOL...")
        client = mph.start()
        print("  ✓ COMSOL连接成功")

        # 2. 创建模型
        print("\n[2/9] 创建模型...")
        model = client.create('SatelliteThermalSimplified')

        # 3. 定义参数
        print("\n[3/9] 定义参数...")
        model.parameter('battery_x', '0[mm]')
        model.parameter('battery_y', '0[mm]')
        model.parameter('battery_z', '0[mm]')
        model.parameter('battery_dx', '200[mm]')
        model.parameter('battery_dy', '150[mm]')
        model.parameter('battery_dz', '100[mm]')
        model.parameter('battery_power', '50[W]')

        model.parameter('payload_x', '0[mm]')
        model.parameter('payload_y', '0[mm]')
        model.parameter('payload_z', '150[mm]')
        model.parameter('payload_dx', '180[mm]')
        model.parameter('payload_dy', '180[mm]')
        model.parameter('payload_dz', '120[mm]')
        model.parameter('payload_power', '30[W]')

        model.parameter('envelope_x', '400[mm]')
        model.parameter('envelope_y', '400[mm]')
        model.parameter('envelope_z', '400[mm]')

        # 辐射参数
        model.parameter('T_space', '3[K]')  # 深空温度
        model.parameter('emissivity', '0.85')  # 发射率
        model.parameter('sigma', '5.67e-8[W/(m^2*K^4)]')  # Stefan-Boltzmann常数

        print("  ✓ 参数定义完成")

        # 4. 创建几何
        print("\n[4/9] 创建几何...")
        model.java.component().create('comp1', True)
        model.java.component('comp1').geom().create('geom1', 3)
        geom = model.java.component('comp1').geom('geom1')

        battery = geom.create('battery', 'Block')
        battery.set('size', ['battery_dx', 'battery_dy', 'battery_dz'])
        battery.set('pos', ['battery_x-battery_dx/2', 'battery_y-battery_dy/2', 'battery_z-battery_dz/2'])

        payload = geom.create('payload', 'Block')
        payload.set('size', ['payload_dx', 'payload_dy', 'payload_dz'])
        payload.set('pos', ['payload_x-payload_dx/2', 'payload_y-payload_dy/2', 'payload_z-payload_dz/2'])

        envelope = geom.create('envelope', 'Block')
        envelope.set('size', ['envelope_x', 'envelope_y', 'envelope_z'])
        envelope.set('pos', ['-envelope_x/2', '-envelope_y/2', '-envelope_z/2'])

        geom.run()
        print("  ✓ 几何创建完成")

        # 5. 定义材料
        print("\n[5/9] 定义材料...")
        comp = model.java.component('comp1')
        mat_al = comp.material().create('mat_aluminum', 'Common')
        mat_al.label('Aluminum')
        mat_al.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
        mat_al.propertyGroup('def').set('density', ['2700[kg/m^3]'])
        mat_al.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])
        mat_al.selection().all()
        print("  ✓ 材料定义完成")

        # 6. 创建选择集
        print("\n[6/9] 创建选择集...")
        sel_battery = comp.selection().create('sel_battery', 'Explicit')
        sel_battery.geom('geom1', 3)
        sel_battery.set([1])

        sel_payload = comp.selection().create('sel_payload', 'Explicit')
        sel_payload.geom('geom1', 3)
        sel_payload.set([2])
        print("  ✓ 选择集创建完成")

        # 7. 设置物理场
        print("\n[7/9] 设置物理场...")
        ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')

        # 热源
        hs_battery = ht.create('hs_battery', 'HeatSource', 3)
        hs_battery.selection().named('sel_battery')
        hs_battery.set('Q0', 1, 'battery_power/(battery_dx*battery_dy*battery_dz*1e-9)')

        hs_payload = ht.create('hs_payload', 'HeatSource', 3)
        hs_payload.selection().named('sel_payload')
        hs_payload.set('Q0', 1, 'payload_power/(payload_dx*payload_dy*payload_dz*1e-9)')

        # 辐射边界条件（简化版 - 使用热流边界）
        hf = ht.create('hf1', 'HeatFluxBoundary', 2)
        hf.selection().all()
        # Stefan-Boltzmann辐射: q = ε·σ·(T⁴ - T_space⁴)
        hf.set('q0', 'emissivity*sigma*(T^4-T_space^4)')
        hf.label('Radiation to Deep Space')

        print("  ✓ 物理场设置完成")
        print("    - 使用Stefan-Boltzmann辐射边界")
        print("    - 发射率: 0.85")
        print("    - 深空温度: 3K")

        # 8. 创建网格
        print("\n[8/9] 创建网格...")
        mesh = comp.mesh().create('mesh1')
        mesh.automatic(True)
        mesh.autoMeshSize(5)
        mesh.run()
        print("  ✓ 网格创建完成")

        # 9. 添加算子和研究
        print("\n[9/9] 添加算子和研究...")
        maxop = comp.cpl().create('maxop1', 'Maximum')
        maxop.selection().geom('geom1', 3)
        maxop.selection().all()

        aveop = comp.cpl().create('aveop1', 'Average')
        aveop.selection().geom('geom1', 3)
        aveop.selection().all()

        intop = comp.cpl().create('intop1', 'Integration')
        intop.selection().geom('geom1', 3)
        intop.selection().all()

        study = model.java.study().create('std1')
        study.create('stat', 'Stationary')

        print("  ✓ 算子和研究创建完成")

        # 保存
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        model.save(output_path)
        client.disconnect()

        print("\n" + "=" * 80)
        print("✓ 简化辐射模型创建成功！")
        print("=" * 80)
        print(f"\n文件: {output_path}")
        print(f"大小: {os.path.getsize(output_path) / 1024:.1f} KB")
        print("\n关键特性:")
        print("  - 使用简化的Stefan-Boltzmann辐射边界")
        print("  - 避免复杂的表面对表面辐射")
        print("  - 物理上正确的辐射散热")
        print()

        return True

    except Exception as e:
        print(f"\n[错误] {e}")
        import traceback
        traceback.print_exc()
        try:
            client.disconnect()
        except:
            pass
        return False


if __name__ == '__main__':
    output_path = 'models/satellite_thermal_v2.mph'
    success = create_simplified_radiation_model(output_path)
    sys.exit(0 if success else 1)
