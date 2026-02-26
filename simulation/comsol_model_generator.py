#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
COMSOL模型动态生成器

根据设计状态动态生成正确的COMSOL热分析模型
解决固定模型的边界条件错误问题
"""

import os
from typing import List, Dict, Any, Optional
from core.protocol import DesignState, ComponentGeometry
from core.logger import get_logger

logger = get_logger(__name__)


class COMSOLModelGenerator:
    """COMSOL模型动态生成器"""

    def __init__(self):
        self.logger = logger

    def generate_model(
        self,
        design_state: DesignState,
        output_path: str,
        environment: str = "orbit"  # "orbit" or "ground"
    ) -> bool:
        """
        根据设计状态动态生成COMSOL模型

        Args:
            design_state: 当前设计状态
            output_path: 输出模型路径
            environment: 环境类型 ("orbit"=轨道真空, "ground"=地面测试)

        Returns:
            bool: 是否成功
        """
        try:
            import mph
        except ImportError:
            self.logger.error("MPh库未安装，无法生成COMSOL模型")
            return False

        self.logger.info(f"开始生成COMSOL模型: {output_path}")
        self.logger.info(f"  环境: {environment}")
        self.logger.info(f"  组件数量: {len(design_state.components)}")

        try:
            # 1. 连接COMSOL
            self.logger.info("[1/10] 连接COMSOL...")
            client = mph.start()

            # 2. 创建模型
            self.logger.info("[2/10] 创建模型...")
            model = client.create('SatelliteThermalDynamic')

            # 3. 定义全局参数
            self.logger.info("[3/10] 定义全局参数...")
            self._define_parameters(model, design_state, environment)

            # 4. 创建几何
            self.logger.info("[4/10] 创建参数化几何...")
            self._create_geometry(model, design_state)

            # 5. 定义材料
            self.logger.info("[5/10] 定义材料属性...")
            self._define_materials(model)

            # 6. 创建选择集
            self.logger.info("[6/10] 创建选择集...")
            self._create_selections(model, design_state)

            # 7. 设置物理场
            self.logger.info("[7/10] 设置热传导物理场...")
            self._setup_physics(model, design_state, environment)

            # 8. 创建网格
            self.logger.info("[8/10] 创建网格...")
            self._create_mesh(model)

            # 9. 添加算子
            self.logger.info("[9/10] 添加算子定义...")
            self._create_operators(model)

            # 10. 创建研究
            self.logger.info("[10/10] 创建研究...")
            self._create_study(model)

            # 保存模型
            output_dir = os.path.dirname(output_path)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir)

            model.save(output_path)
            client.disconnect()

            self.logger.info(f"✓ COMSOL模型生成成功: {output_path}")
            return True

        except Exception as e:
            self.logger.error(f"COMSOL模型生成失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            try:
                client.disconnect()
            except:
                pass
            return False

    def _define_parameters(
        self,
        model,
        design_state: DesignState,
        environment: str
    ):
        """定义全局参数"""
        # 为每个组件定义参数
        for comp in design_state.components:
            prefix = comp.id.replace('-', '_').replace('.', '_')

            # 位置参数
            model.parameter(f'{prefix}_x', f'{comp.position.x}[mm]')
            model.parameter(f'{prefix}_y', f'{comp.position.y}[mm]')
            model.parameter(f'{prefix}_z', f'{comp.position.z}[mm]')

            # 尺寸参数
            model.parameter(f'{prefix}_dx', f'{comp.dimensions.x}[mm]')
            model.parameter(f'{prefix}_dy', f'{comp.dimensions.y}[mm]')
            model.parameter(f'{prefix}_dz', f'{comp.dimensions.z}[mm]')

            # 功率参数
            model.parameter(f'{prefix}_power', f'{comp.power}[W]')

        # 外壳参数
        envelope = design_state.envelope
        model.parameter('envelope_x', f'{envelope.outer_size.x}[mm]')
        model.parameter('envelope_y', f'{envelope.outer_size.y}[mm]')
        model.parameter('envelope_z', f'{envelope.outer_size.z}[mm]')
        model.parameter('wall_thickness', f'{envelope.thickness}[mm]')

        # 环境参数
        if environment == "orbit":
            # 轨道环境
            model.parameter('T_space', '3[K]')  # 深空温度
            model.parameter('T_sun', '5778[K]')  # 太阳温度
            model.parameter('solar_flux', '1367[W/m^2]')  # 太阳常数
            model.parameter('emissivity', '0.85')  # 表面发射率
            model.parameter('absorptivity', '0.25')  # 太阳吸收率
        else:
            # 地面测试环境
            model.parameter('T_ambient', '293.15[K]')  # 20°C
            model.parameter('h_conv', '10[W/(m^2*K)]')  # 对流系数

        self.logger.info(f"  ✓ 定义了 {len(design_state.components) * 7 + 4} 个参数")

    def _create_geometry(self, model, design_state: DesignState):
        """创建参数化几何"""
        model.java.component().create('comp1', True)
        model.java.component('comp1').geom().create('geom1', 3)
        geom = model.java.component('comp1').geom('geom1')

        # 创建每个组件的几何
        for i, comp in enumerate(design_state.components):
            prefix = comp.id.replace('-', '_').replace('.', '_')

            block = geom.create(f'comp_{i}', 'Block')
            block.set('size', [f'{prefix}_dx', f'{prefix}_dy', f'{prefix}_dz'])
            block.set('pos', [
                f'{prefix}_x-{prefix}_dx/2',
                f'{prefix}_y-{prefix}_dy/2',
                f'{prefix}_z-{prefix}_dz/2'
            ])
            block.label(comp.id)

        # 创建外壳
        envelope_block = geom.create('envelope', 'Block')
        envelope_block.set('size', ['envelope_x', 'envelope_y', 'envelope_z'])
        envelope_block.set('pos', [
            '-envelope_x/2',
            '-envelope_y/2',
            '-envelope_z/2'
        ])
        envelope_block.label('Envelope')

        geom.run()
        self.logger.info(f"  ✓ 创建了 {len(design_state.components) + 1} 个几何体")

    def _define_materials(self, model):
        """定义材料属性"""
        comp = model.java.component('comp1')

        # 铝合金（应用于所有域）
        mat_al = comp.material().create('mat_aluminum', 'Common')
        mat_al.label('Aluminum')

        # 热物性
        mat_al.propertyGroup('def').set('thermalconductivity', ['237[W/(m*K)]'])
        mat_al.propertyGroup('def').set('density', ['2700[kg/m^3]'])
        mat_al.propertyGroup('def').set('heatcapacity', ['900[J/(kg*K)]'])

        # 辐射属性（关键！使用COMSOL的正确属性名）
        mat_al.propertyGroup('def').set('epsilon_rad', ['0.85'])  # 辐射发射率
        mat_al.propertyGroup('def').set('rho_rad', ['0.15'])  # 辐射反射率

        mat_al.selection().all()

        self.logger.info("  ✓ 材料定义完成（包含辐射属性）")
        self.logger.info("    - 热导率: 237 W/(m·K)")
        self.logger.info("    - 辐射发射率: 0.85")

    def _create_selections(self, model, design_state: DesignState):
        """创建选择集"""
        comp = model.java.component('comp1')

        # 为每个组件创建选择集
        for i, component in enumerate(design_state.components):
            sel = comp.selection().create(f'sel_comp_{i}', 'Explicit')
            sel.geom('geom1', 3)
            sel.set([i + 1])  # 域索引从1开始
            sel.label(f'{component.id} Domain')

        # 外表面选择集
        sel_surface = comp.selection().create('sel_outer_surface', 'Explicit')
        sel_surface.geom('geom1', 2)
        # 这里需要根据实际几何确定外表面边界
        sel_surface.label('Outer Surface')

        self.logger.info(f"  ✓ 创建了 {len(design_state.components) + 1} 个选择集")

    def _setup_physics(
        self,
        model,
        design_state: DesignState,
        environment: str
    ):
        """设置热传导物理场"""
        comp = model.java.component('comp1')
        ht = comp.physics().create('ht', 'HeatTransfer', 'geom1')
        ht.label('Heat Transfer in Solids')

        # 为每个组件添加热源
        for i, component in enumerate(design_state.components):
            if component.power > 0:
                prefix = component.id.replace('-', '_').replace('.', '_')

                hs = ht.create(f'hs_{i}', 'HeatSource', 3)
                hs.selection().named(f'sel_comp_{i}')
                # 体积热源：功率 / 体积
                hs.set('Q0', 1, f'{prefix}_power/({prefix}_dx*{prefix}_dy*{prefix}_dz*1e-9)')
                hs.label(f'{component.id} Heat Source')

        # 设置边界条件
        if environment == "orbit":
            # 轨道环境：辐射边界条件
            self._setup_radiation_boundary(ht)
        else:
            # 地面环境：对流边界条件
            self._setup_convection_boundary(ht)

        self.logger.info("  ✓ 物理场设置完成")

    def _setup_radiation_boundary(self, ht):
        """设置辐射边界条件（轨道环境）"""
        # 表面对表面辐射
        rad = ht.create('rad1', 'SurfaceToSurfaceRadiation', 2)
        rad.selection().all()  # 应用于所有外表面

        # 关键步骤：
        # 1. 切换数据源为"用户定义"（这会让COMSOL停止从材料查找epsilon_rad）
        rad.set('epsilon_rad_mat', 'userdef')

        # 2. 设置发射率值
        rad.set('epsilon_rad', '0.85')

        # 3. 设置深空温度
        rad.set('Tamb', 'T_space')
        rad.label('Radiation to Deep Space')

        self.logger.info("  ✓ 辐射边界条件已设置")
        self.logger.info("    - 数据源: 用户定义")
        self.logger.info("    - 发射率: 0.85")
        self.logger.info("    - 深空温度: 3K")

    def _setup_convection_boundary(self, ht):
        """设置对流边界条件（地面环境）"""
        hf = ht.create('hf1', 'HeatFluxBoundary', 2)
        hf.selection().all()
        hf.set('q0', 'h_conv*(T-T_ambient)')
        hf.label('Convection Boundary')

        self.logger.info("  ✓ 对流边界条件已设置")
        self.logger.info("    - 对流系数: 10 W/(m²·K)")
        self.logger.info("    - 环境温度: 20°C")

    def _create_mesh(self, model):
        """创建网格"""
        comp = model.java.component('comp1')
        mesh = comp.mesh().create('mesh1')
        mesh.automatic(True)
        mesh.autoMeshSize(5)  # Normal
        mesh.run()

        self.logger.info("  ✓ 网格创建完成")

    def _create_operators(self, model):
        """创建算子"""
        comp = model.java.component('comp1')

        # 最大值算子
        maxop = comp.cpl().create('maxop1', 'Maximum')
        maxop.selection().geom('geom1', 3)
        maxop.selection().all()
        maxop.label('Maximum Operator')

        # 平均值算子
        aveop = comp.cpl().create('aveop1', 'Average')
        aveop.selection().geom('geom1', 3)
        aveop.selection().all()
        aveop.label('Average Operator')

        # 积分算子
        intop = comp.cpl().create('intop1', 'Integration')
        intop.selection().geom('geom1', 3)
        intop.selection().all()
        intop.label('Integration Operator')

        self.logger.info("  ✓ 算子定义完成")

    def _create_study(self, model):
        """创建研究"""
        study = model.java.study().create('std1')
        study.create('stat', 'Stationary')
        study.label('Steady-State Thermal Analysis')

        self.logger.info("  ✓ 研究创建完成")


if __name__ == "__main__":
    # 测试代码
    from core.protocol import Vector3D, Envelope

    # 创建测试设计状态
    comp1 = ComponentGeometry(
        id='battery_01',
        position=Vector3D(x=0, y=0, z=0),
        dimensions=Vector3D(x=200, y=150, z=100),
        mass=5.0,
        power=50.0,
        category='power'
    )

    comp2 = ComponentGeometry(
        id='payload_01',
        position=Vector3D(x=0, y=0, z=150),
        dimensions=Vector3D(x=180, y=180, z=120),
        mass=3.5,
        power=30.0,
        category='payload'
    )

    state = DesignState(
        iteration=1,
        components=[comp1, comp2],
        envelope=Envelope(
            outer_size=Vector3D(x=400, y=400, z=400),
            thickness=5.0
        )
    )

    # 生成模型
    generator = COMSOLModelGenerator()
    success = generator.generate_model(
        state,
        'models/satellite_thermal_dynamic.mph',
        environment='orbit'
    )

    print(f"\n模型生成{'成功' if success else '失败'}")
