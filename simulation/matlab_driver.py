"""
MATLAB仿真驱动器

通过MATLAB Engine API调用MATLAB函数进行仿真
"""

import os
import tempfile
from typing import Dict, Any, Optional
import numpy as np

from simulation.base import SimulationDriver
from core.protocol import SimulationRequest, SimulationResult, ViolationItem
from core.exceptions import MatlabConnectionError, SimulationError
from core.logger import get_logger

logger = get_logger(__name__)


class MatlabDriver(SimulationDriver):
    """MATLAB仿真驱动器"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化MATLAB驱动器

        Args:
            config: 配置字典，包含：
                - matlab_workspace: MATLAB工作目录
                - matlab_function: 要调用的MATLAB函数名
        """
        super().__init__(config)
        self.workspace = config.get('matlab_workspace', '.')
        self.function_name = config.get('matlab_function', 'run_simulation')
        self.engine: Optional[Any] = None

    def connect(self) -> bool:
        """
        启动MATLAB引擎

        Returns:
            是否连接成功
        """
        if self.connected:
            logger.info("MATLAB引擎已连接")
            return True

        try:
            logger.info("正在启动MATLAB引擎...")
            import matlab.engine
            self.engine = matlab.engine.start_matlab()

            # 切换到工作目录
            if os.path.exists(self.workspace):
                self.engine.cd(self.workspace, nargout=0)
                logger.info(f"MATLAB工作目录: {self.workspace}")
            else:
                logger.warning(f"MATLAB工作目录不存在: {self.workspace}")

            self.connected = True
            logger.info("✓ MATLAB引擎启动成功")
            return True

        except ImportError:
            raise MatlabConnectionError(
                "无法导入matlab.engine模块。请安装MATLAB Engine for Python:\n"
                "1. 找到MATLAB安装目录\n"
                "2. cd \"D:\\Program Files\\MATLAB\\R20XXx\\extern\\engines\\python\"\n"
                "3. python setup.py install"
            )
        except Exception as e:
            raise MatlabConnectionError(f"MATLAB引擎启动失败: {e}")

    def disconnect(self):
        """关闭MATLAB引擎"""
        if self.engine:
            try:
                self.engine.quit()
                logger.info("MATLAB引擎已关闭")
            except Exception as e:
                logger.warning(f"关闭MATLAB引擎时出错: {e}")
            finally:
                self.engine = None
                self.connected = False

    def run_simulation(self, request: SimulationRequest) -> SimulationResult:
        """
        运行MATLAB仿真

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
            logger.info(f"运行MATLAB仿真: {self.function_name}")

            # 1. 准备输入数据
            input_file = self._prepare_input(request)
            logger.info(f"  输入文件: {input_file}")

            # 2. 调用MATLAB函数
            # 支持两种调用方式：
            # 方式1: 直接调用函数（如果函数在路径中）
            # 方式2: 使用eval执行
            try:
                # 尝试直接调用
                result = getattr(self.engine, self.function_name)(input_file, nargout=1)
            except AttributeError:
                # 如果函数不在路径中，使用eval
                result = self.engine.eval(f"{self.function_name}('{input_file}')", nargout=1)

            # 3. 解析结果
            metrics = self._parse_result(result)
            logger.info(f"  仿真完成: {metrics}")

            # 4. 检查约束
            violations = self.check_constraints(metrics)

            # 5. 清理临时文件
            if os.path.exists(input_file):
                os.remove(input_file)

            return SimulationResult(
                success=True,
                metrics=metrics,
                violations=[ViolationItem(**v) for v in violations],
                raw_data={'matlab_result': str(result)}
            )

        except Exception as e:
            logger.error(f"MATLAB仿真失败: {e}")
            return SimulationResult(
                success=False,
                metrics={},
                violations=[],
                error_message=str(e)
            )

    def _prepare_input(self, request: SimulationRequest) -> str:
        """
        准备MATLAB输入文件（制表符分隔格式）

        Args:
            request: 仿真请求

        Returns:
            输入文件路径
        """
        # 创建临时文件
        fd, input_file = tempfile.mkstemp(suffix='.dat', prefix='matlab_input_')
        os.close(fd)

        # 提取设计参数
        design_state = request.design_state
        params = []

        # 添加组件位置和尺寸
        for comp in design_state.components:
            params.extend([
                comp.position.x,
                comp.position.y,
                comp.position.z,
                comp.dimensions.x,
                comp.dimensions.y,
                comp.dimensions.z,
                comp.mass,
                comp.power
            ])

        # 写入文件（制表符分隔）
        with open(input_file, 'w') as f:
            f.write('\t'.join(map(str, params)))

        return input_file

    def _parse_result(self, result: Any) -> Dict[str, float]:
        """
        解析MATLAB返回结果

        Args:
            result: MATLAB返回值

        Returns:
            指标字典
        """
        metrics = {}

        # 如果返回的是结构体或字典
        if isinstance(result, dict):
            for key, value in result.items():
                try:
                    metrics[key] = float(value)
                except (ValueError, TypeError):
                    logger.warning(f"无法转换指标 {key}: {value}")

        # 如果返回的是数组
        elif isinstance(result, (list, tuple, np.ndarray)):
            # 假设返回格式: [max_temp, min_clearance, total_mass, total_power]
            if len(result) >= 1:
                metrics['max_temp'] = float(result[0])
            if len(result) >= 2:
                metrics['min_clearance'] = float(result[1])
            if len(result) >= 3:
                metrics['total_mass'] = float(result[2])
            if len(result) >= 4:
                metrics['total_power'] = float(result[3])

        # 如果返回的是单个数值
        elif isinstance(result, (int, float)):
            metrics['result'] = float(result)

        else:
            logger.warning(f"未知的MATLAB返回类型: {type(result)}")

        return metrics

    def call_function(self, function_name: str, *args, **kwargs) -> Any:
        """
        调用任意MATLAB函数

        Args:
            function_name: 函数名
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            MATLAB返回值
        """
        if not self.connected:
            self.connect()

        try:
            func = getattr(self.engine, function_name)
            return func(*args, **kwargs)
        except Exception as e:
            raise SimulationError(f"调用MATLAB函数 {function_name} 失败: {e}")
