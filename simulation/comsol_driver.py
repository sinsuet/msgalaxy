"""
COMSOL仿真驱动器

通过MPh库连接COMSOL Multiphysics进行多物理场仿真
支持动态模型生成，自动修复边界条件问题
"""

import os
from typing import Dict, Any, Optional, List

from simulation.base import SimulationDriver
from core.protocol import SimulationRequest, SimulationResult, ViolationItem
from core.exceptions import ComsolConnectionError, SimulationError
from core.logger import get_logger

logger = get_logger(__name__)


class ComsolDriver(SimulationDriver):
    """COMSOL仿真驱动器"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化COMSOL驱动器

        Args:
            config: 配置字典，包含：
                - comsol_model: COMSOL模型文件路径（.mph）
                - comsol_parameters: 要更新的参数列表
                - auto_generate_model: 是否自动生成模型（默认False）
                - environment: 环境类型 ("orbit"或"ground"，默认"orbit")
        """
        super().__init__(config)
        self.model_file = config.get('comsol_model', 'model.mph')
        self.parameters = config.get('comsol_parameters', [])
        self.auto_generate = config.get('auto_generate_model', False)
        self.environment = config.get('environment', 'orbit')
        self.client: Optional[Any] = None
        self.model: Optional[Any] = None

    def connect(self) -> bool:
        """
        连接到COMSOL服务器并加载模型

        Returns:
            是否连接成功
        """
        if self.connected:
            logger.info("COMSOL已连接")
            return True

        try:
            logger.info("正在连接COMSOL...")
            import mph

            # 启动COMSOL客户端
            self.client = mph.start()
            logger.info("✓ COMSOL客户端启动成功")

            # 加载模型
            if not os.path.exists(self.model_file):
                raise ComsolConnectionError(f"COMSOL模型文件不存在: {self.model_file}")

            logger.info(f"正在加载模型: {self.model_file}")
            self.model = self.client.load(self.model_file)
            logger.info("✓ COMSOL模型加载成功")

            self.connected = True
            return True

        except ImportError:
            raise ComsolConnectionError(
                "无法导入mph模块。请安装MPh库:\n"
                "pip install mph\n"
                "注意：需要COMSOL安装在 D:\\Program Files\\COMSOL63"
            )
        except Exception as e:
            raise ComsolConnectionError(f"COMSOL连接失败: {e}")

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
                self.connected = False

    def run_simulation(self, request: SimulationRequest) -> SimulationResult:
        """
        运行COMSOL仿真

        Args:
            request: 仿真请求

        Returns:
            仿真结果
        """
        if not self.connected:
            self.connect()

        if not self.validate_design_state(request.design_state):
            return SimulationResult(
                success=False,
                metrics={},
                violations=[],
                error_message="设计状态无效"
            )

        try:
            # 检查是否需要重新生成模型
            if self.auto_generate:
                self._regenerate_model_if_needed(request.design_state)

            logger.info("运行COMSOL仿真...")

            # 1. 更新几何参数
            self._update_geometry(request.design_state)

            # 2. 重建几何和网格
            logger.info("  重建几何...")
            self.model.build()

            logger.info("  生成网格...")
            self.model.mesh()

            # 3. 求解
            logger.info("  求解物理场...")
            self.model.solve()

            # 4. 提取结果
            metrics = self._extract_results()
            logger.info(f"  仿真完成: {metrics}")

            # 5. 检查约束
            violations = self.check_constraints(metrics)

            return SimulationResult(
                success=True,
                metrics=metrics,
                violations=[ViolationItem(**v) for v in violations]
            )

        except Exception as e:
            logger.error(f"COMSOL仿真失败: {e}")
            return SimulationResult(
                success=False,
                metrics={},
                violations=[],
                error_message=str(e)
            )

    def _update_geometry(self, design_state):
        """
        更新COMSOL模型的几何参数

        Args:
            design_state: 设计状态
        """
        logger.info("  更新几何参数...")

        for comp in design_state.components:
            # 更新位置参数
            param_prefix = comp.id

            # 位置参数
            if f'{param_prefix}_x' in self.parameters:
                self.model.parameter(f'{param_prefix}_x', f'{comp.position.x}[mm]')
            if f'{param_prefix}_y' in self.parameters:
                self.model.parameter(f'{param_prefix}_y', f'{comp.position.y}[mm]')
            if f'{param_prefix}_z' in self.parameters:
                self.model.parameter(f'{param_prefix}_z', f'{comp.position.z}[mm]')

            # 尺寸参数
            if f'{param_prefix}_dx' in self.parameters:
                self.model.parameter(f'{param_prefix}_dx', f'{comp.dimensions.x}[mm]')
            if f'{param_prefix}_dy' in self.parameters:
                self.model.parameter(f'{param_prefix}_dy', f'{comp.dimensions.y}[mm]')
            if f'{param_prefix}_dz' in self.parameters:
                self.model.parameter(f'{param_prefix}_dz', f'{comp.dimensions.z}[mm]')

            # 功率参数（用于热源）
            if f'{param_prefix}_power' in self.parameters:
                self.model.parameter(f'{param_prefix}_power', f'{comp.power}[W]')

        logger.info(f"  已更新 {len(design_state.components)} 个组件的参数")

    def _regenerate_model_if_needed(self, design_state):
        """
        根据需要重新生成COMSOL模型

        Args:
            design_state: 设计状态
        """
        # 检查是否需要重新生成
        # 条件：组件数量变化、首次运行、或模型文件不存在
        need_regenerate = False

        if not os.path.exists(self.model_file):
            logger.info("模型文件不存在，需要生成新模型")
            need_regenerate = True
        elif hasattr(self, '_last_component_count'):
            if len(design_state.components) != self._last_component_count:
                logger.info(f"组件数量变化 ({self._last_component_count} -> {len(design_state.components)})，需要重新生成模型")
                need_regenerate = True

        if need_regenerate:
            logger.info("开始动态生成COMSOL模型...")
            from simulation.comsol_model_generator import COMSOLModelGenerator

            generator = COMSOLModelGenerator()
            success = generator.generate_model(
                design_state,
                self.model_file,
                environment=self.environment
            )

            if not success:
                raise SimulationError("COMSOL模型生成失败")

            # 重新加载模型
            if self.connected:
                self.disconnect()
            self.connect()

            logger.info("✓ 动态模型生成并加载成功")

        self._last_component_count = len(design_state.components)

    def _extract_results(self) -> Dict[str, float]:
        """
        从COMSOL模型中提取仿真结果

        Returns:
            指标字典
        """
        metrics = {}

        try:
            # 提取最大温度（使用算子）
            max_temp = float(self.model.evaluate('maxop1(T)', unit='degC'))
            metrics['max_temp'] = max_temp

            # 提取平均温度（使用算子）
            avg_temp = float(self.model.evaluate('aveop1(T)', unit='degC'))
            metrics['avg_temp'] = avg_temp

            # 提取最大应力（如果有结构分析）
            try:
                max_stress = float(self.model.evaluate('maxop1(solid.mises)', unit='MPa'))
                metrics['max_stress'] = max_stress
            except:
                pass

            # 提取总热流（如果有热分析）
            try:
                total_heat_flux = float(self.model.evaluate('intop1(ht.tfluxMag)', unit='W'))
                metrics['total_heat_flux'] = total_heat_flux
            except:
                pass

        except Exception as e:
            logger.warning(f"提取结果时出错: {e}")

        return metrics

    def evaluate_expression(self, expression: str, unit: str = None) -> float:
        """
        计算COMSOL表达式

        Args:
            expression: COMSOL表达式
            unit: 单位

        Returns:
            计算结果
        """
        if not self.connected:
            self.connect()

        try:
            if unit:
                return float(self.model.evaluate(expression, unit=unit))
            else:
                return float(self.model.evaluate(expression))
        except Exception as e:
            raise SimulationError(f"计算表达式失败: {e}")

    def export_results(self, output_file: str, dataset: str = None):
        """
        导出仿真结果

        Args:
            output_file: 输出文件路径
            dataset: 数据集名称
        """
        if not self.connected:
            raise SimulationError("COMSOL未连接")

        try:
            if dataset:
                self.model.export(output_file, dataset)
            else:
                self.model.export(output_file)
            logger.info(f"结果已导出到: {output_file}")
        except Exception as e:
            raise SimulationError(f"导出结果失败: {e}")
