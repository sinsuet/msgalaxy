#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
动态COMSOL导入验证脚本

测试核心技术路线：
1. 从DesignState生成STEP文件
2. COMSOL动态导入STEP
3. 基于空间坐标的Box Selection识别组件
4. 赋予物理属性（热源、辐射面）
5. 求解并提取温度结果

这是架构升级的探路脚本，验证通过后再集成到主流程。
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import logging
from typing import Dict, Any, List, Tuple

from core.protocol import DesignState, ComponentGeometry, Vector3D, Envelope
from geometry.cad_export import export_design, CADExportOptions
from core.exceptions import SimulationError

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DynamicComsolTester:
    """动态COMSOL导入测试器"""

    def __init__(self, workspace_dir: str = "workspace/comsol_test"):
        """
        初始化测试器

        Args:
            workspace_dir: 工作目录
        """
        self.workspace = Path(workspace_dir)
        self.workspace.mkdir(parents=True, exist_ok=True)

        self.client = None
        self.model = None

    def create_test_design(self) -> DesignState:
        """
        创建测试用的设计状态

        包含2个简单组件：
        1. 电池模块（发热10W）
        2. 载荷模块（发热5W）
        """
        logger.info("创建测试设计状态...")

        components = [
            ComponentGeometry(
                id="battery_01",
                position=Vector3D(x=50.0, y=50.0, z=50.0),
                dimensions=Vector3D(x=100.0, y=80.0, z=50.0),
                mass=5.0,
                power=10.0,  # 发热功率10W
                category="power"
            ),
            ComponentGeometry(
                id="payload_01",
                position=Vector3D(x=200.0, y=50.0, z=50.0),
                dimensions=Vector3D(x=80.0, y=80.0, z=60.0),
                mass=3.0,
                power=5.0,  # 发热功率5W
                category="payload"
            )
        ]

        envelope = Envelope(
            outer_size=Vector3D(x=400.0, y=200.0, z=200.0)
        )

        design_state = DesignState(
            iteration=0,
            components=components,
            envelope=envelope
        )

        logger.info(f"✓ 创建了包含 {len(components)} 个组件的设计")
        return design_state

    def export_to_step(self, design_state: DesignState) -> Path:
        """
        导出设计为STEP文件

        Args:
            design_state: 设计状态

        Returns:
            STEP文件路径
        """
        logger.info("导出STEP文件...")

        step_file = self.workspace / "current_design.step"

        options = CADExportOptions(
            unit="mm",
            precision=3,
            author="DynamicComsolTest",
            description="Test design for dynamic COMSOL import"
        )

        export_design(design_state, str(step_file), format="step", options=options)

        logger.info(f"✓ STEP文件已导出: {step_file}")
        return step_file

    def connect_comsol(self):
        """连接到COMSOL"""
        logger.info("连接COMSOL...")

        try:
            import mph

            # 启动COMSOL客户端
            self.client = mph.start()
            logger.info("✓ COMSOL客户端启动成功")

        except ImportError:
            raise SimulationError(
                "无法导入mph模块。请安装:\n"
                "pip install mph\n"
                "并确保COMSOL已安装"
            )
        except Exception as e:
            raise SimulationError(f"COMSOL连接失败: {e}")

    def create_dynamic_model(
        self,
        step_file: Path,
        design_state: DesignState
    ) -> Dict[str, Any]:
        """
        创建动态COMSOL模型

        核心步骤：
        1. 创建空模型
        2. 导入STEP几何
        3. 使用Box Selection识别组件和边界
        4. 赋予物理属性
        5. 划分网格

        Args:
            step_file: STEP文件路径
            design_state: 设计状态（用于获取组件坐标）

        Returns:
            模型信息字典
        """
        logger.info("创建动态COMSOL模型...")

        try:
            # 1. 创建新模型
            logger.info("  [1/6] 创建空模型...")
            self.model = self.client.create("DynamicThermalModel")

            # 2. 导入STEP几何
            logger.info(f"  [2/6] 导入STEP文件: {step_file}")
            geom = self.model.java.geom().create("geom1", 3)
            import_node = geom.feature().create("imp1", "Import")
            import_node.set("filename", str(step_file.absolute()))
            import_node.set("type", "step")

            # 执行几何导入
            geom.run()
            logger.info("  ✓ STEP几何导入成功")

            # 3. 创建物理场（稳态热传导）
            logger.info("  [3/6] 创建热传导物理场...")
            ht = self.model.java.physics().create("ht", "HeatTransfer", "geom1")

            # 4. 使用Box Selection识别组件并赋予热源
            logger.info("  [4/6] 创建Box Selection并赋予热源...")
            self._assign_heat_sources(design_state, ht, geom)

            # 5. 识别外部边界并赋予辐射条件
            logger.info("  [5/6] 识别外部边界并赋予辐射条件...")
            self._assign_radiation_boundaries(design_state, ht, geom)

            # 6. 创建网格
            logger.info("  [6/6] 创建自动网格...")
            mesh = self.model.java.mesh().create("mesh1", "geom1")
            mesh.autoMeshSize(5)  # 中等网格密度
            mesh.run()
            logger.info("  ✓ 网格生成成功")

            logger.info("✓ 动态模型创建完成")

            return {
                "geometry": geom,
                "physics": ht,
                "mesh": mesh
            }

        except Exception as e:
            logger.error(f"模型创建失败: {e}")
            raise SimulationError(f"动态模型创建失败: {e}")

    def _assign_heat_sources(
        self,
        design_state: DesignState,
        ht: Any,
        geom: Any
    ):
        """
        使用Box Selection识别组件并赋予热源

        Args:
            design_state: 设计状态
            ht: 热传导物理场对象
            geom: 几何对象
        """
        for i, comp in enumerate(design_state.components):
            if comp.power <= 0:
                continue

            logger.info(f"    - 为组件 {comp.id} 创建热源 ({comp.power}W)")

            # 计算组件的包围盒
            pos = comp.position
            dim = comp.dimensions

            # Box Selection的边界（组件中心 ± 半尺寸）
            x_min = pos.x - dim.x / 2
            x_max = pos.x + dim.x / 2
            y_min = pos.y - dim.y / 2
            y_max = pos.y + dim.y / 2
            z_min = pos.z - dim.z / 2
            z_max = pos.z + dim.z / 2

            # 创建Box Selection（选择Domain）
            sel_name = f"boxsel_comp_{i}"
            box_sel = geom.selection().create(sel_name, "Box")
            box_sel.set("entitydim", 3)  # 3D实体（Domain）
            box_sel.set("xmin", x_min)
            box_sel.set("xmax", x_max)
            box_sel.set("ymin", y_min)
            box_sel.set("ymax", y_max)
            box_sel.set("zmin", z_min)
            box_sel.set("zmax", z_max)

            # 创建热源节点
            hs_name = f"hs_{i}"
            heat_source = ht.feature().create(hs_name, "HeatSource")
            heat_source.selection().named(sel_name)

            # 设置发热功率（W/m³需要转换）
            # 假设组件均匀发热，功率密度 = 总功率 / 体积
            volume = (dim.x * dim.y * dim.z) / 1e9  # mm³ -> m³
            power_density = comp.power / volume if volume > 0 else 0

            heat_source.set("Q0", power_density)

            logger.info(f"      ✓ 热源已设置: {comp.power}W, 功率密度: {power_density:.2e} W/m³")

    def _assign_radiation_boundaries(
        self,
        design_state: DesignState,
        ht: Any,
        geom: Any
    ):
        """
        识别外部边界并赋予辐射条件

        使用包围整个卫星的Box Selection选择所有外表面

        Args:
            design_state: 设计状态
            ht: 热传导物理场对象
            geom: 几何对象
        """
        logger.info("    - 创建外部辐射边界...")

        # 使用envelope尺寸创建外边界Box Selection
        env = design_state.envelope.outer_size

        # 稍微扩大包围盒以确保选中所有外表面
        margin = 10.0  # mm
        x_min = -margin
        x_max = env.x + margin
        y_min = -margin
        y_max = env.y + margin
        z_min = -margin
        z_max = env.z + margin

        # 创建Box Selection（选择Boundary）
        sel_name = "boxsel_outer_boundary"
        box_sel = geom.selection().create(sel_name, "Box")
        box_sel.set("entitydim", 2)  # 2D边界（Boundary）
        box_sel.set("xmin", x_min)
        box_sel.set("xmax", x_max)
        box_sel.set("ymin", y_min)
        box_sel.set("ymax", y_max)
        box_sel.set("zmin", z_min)
        box_sel.set("zmax", z_max)

        # 创建辐射边界条件
        # 注意：为了确保收敛，这里使用线性化的等效对流换热
        # 而不是T^4非线性辐射
        hf = ht.feature().create("hf1", "HeatFluxBoundary")
        hf.selection().named(sel_name)

        # 深空温度（约4K）
        T_space = 4.0  # K

        # 等效对流换热系数（简化辐射）
        # h_eff ≈ ε * σ * T³，假设T≈300K，ε=0.8
        epsilon = 0.8
        sigma = 5.67e-8  # Stefan-Boltzmann常数
        T_ref = 300.0  # K
        h_eff = epsilon * sigma * (T_ref ** 3)

        hf.set("HeatFluxType", "ConvectiveHeatFlux")
        hf.set("h", h_eff)
        hf.set("Text", T_space)

        logger.info(f"      ✓ 辐射边界已设置: h_eff={h_eff:.2e} W/(m²·K), T_space={T_space}K")

    def solve_model(self) -> Dict[str, float]:
        """
        求解模型并提取结果

        Returns:
            结果字典，包含max_temp等指标
        """
        logger.info("求解模型...")

        try:
            # 创建研究
            study = self.model.java.study().create("std1")
            study.feature().create("stat", "Stationary")

            # 运行求解
            logger.info("  正在求解（这可能需要几分钟）...")
            study.run()
            logger.info("  ✓ 求解完成")

            # 提取最高温度
            logger.info("  提取温度结果...")

            # 获取温度场数据
            # 注意：mph库的API可能因版本而异，这里提供通用方法
            try:
                # 方法1：使用evaluate
                max_temp = self.model.evaluate("maxop1(T)", "K")
                logger.info(f"  ✓ 最高温度: {max_temp:.2f} K ({max_temp - 273.15:.2f} °C)")

            except:
                # 方法2：使用Java API
                result = self.model.java.result()
                eval_node = result.numerical().create("eval1", "Eval")
                eval_node.set("expr", "T")
                eval_node.set("unit", "K")
                eval_node.set("descr", "Temperature")

                data = eval_node.getData()
                max_temp = float(max(data))
                logger.info(f"  ✓ 最高温度: {max_temp:.2f} K ({max_temp - 273.15:.2f} °C)")

            return {
                "max_temp": max_temp,
                "max_temp_celsius": max_temp - 273.15,
                "converged": True
            }

        except Exception as e:
            logger.error(f"求解失败: {e}")

            # 返回惩罚分
            return {
                "max_temp": 9999.0,
                "max_temp_celsius": 9999.0,
                "converged": False,
                "error": str(e)
            }

    def save_model(self, filename: str = "dynamic_test_model.mph"):
        """保存模型文件"""
        if self.model:
            save_path = self.workspace / filename
            logger.info(f"保存模型: {save_path}")
            self.model.save(str(save_path))
            logger.info("✓ 模型已保存")

    def disconnect(self):
        """断开COMSOL连接"""
        if self.client:
            try:
                self.client.disconnect()
                logger.info("COMSOL连接已关闭")
            except Exception as e:
                logger.warning(f"断开连接时出错: {e}")
            finally:
                self.client = None
                self.model = None

    def run_full_test(self) -> bool:
        """
        运行完整测试流程

        Returns:
            测试是否成功
        """
        logger.info("=" * 60)
        logger.info("开始动态COMSOL导入完整测试")
        logger.info("=" * 60)

        try:
            # 1. 创建测试设计
            design_state = self.create_test_design()

            # 2. 导出STEP
            step_file = self.export_to_step(design_state)

            # 3. 连接COMSOL
            self.connect_comsol()

            # 4. 创建动态模型
            model_info = self.create_dynamic_model(step_file, design_state)

            # 5. 求解
            results = self.solve_model()

            # 6. 保存模型
            self.save_model()

            # 7. 输出结果
            logger.info("=" * 60)
            logger.info("测试结果:")
            logger.info(f"  最高温度: {results['max_temp']:.2f} K ({results['max_temp_celsius']:.2f} °C)")
            logger.info(f"  是否收敛: {results['converged']}")

            if results['converged']:
                logger.info("=" * 60)
                logger.info("✓✓✓ 测试成功！动态COMSOL导入技术路线验证通过！")
                logger.info("=" * 60)
                return True
            else:
                logger.warning("求解未收敛，但流程已走通")
                return False

        except Exception as e:
            logger.error(f"测试失败: {e}", exc_info=True)
            return False

        finally:
            # 清理
            self.disconnect()


def main():
    """主函数"""
    tester = DynamicComsolTester()

    success = tester.run_full_test()

    if success:
        print("\n下一步：将此技术集成到 comsol_driver.py 和 orchestrator.py")
        sys.exit(0)
    else:
        print("\n测试失败，请检查错误信息")
        sys.exit(1)


if __name__ == "__main__":
    main()
