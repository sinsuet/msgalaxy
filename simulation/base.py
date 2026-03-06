"""
仿真基类

定义所有仿真驱动器的统一接口
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from core.protocol import SimulationRequest, SimulationResult, DesignState
from core.logger import get_logger
from simulation.contracts import build_simulation_constraint_rows

logger = get_logger(__name__)


class SimulationDriver(ABC):
    """仿真驱动器基类"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化仿真驱动器

        Args:
            config: 仿真配置字典
        """
        self.config = config
        self.connected = False

    @abstractmethod
    def connect(self) -> bool:
        """
        连接到仿真环境

        Returns:
            是否连接成功
        """
        pass

    @abstractmethod
    def disconnect(self):
        """断开连接"""
        pass

    @abstractmethod
    def run_simulation(self, request: SimulationRequest) -> SimulationResult:
        """
        运行仿真

        Args:
            request: 仿真请求

        Returns:
            仿真结果
        """
        pass

    def validate_design_state(self, design_state: DesignState) -> bool:
        """
        验证设计状态是否有效

        Args:
            design_state: 设计状态

        Returns:
            是否有效
        """
        if not design_state.components:
            logger.warning("设计状态中没有组件")
            return False

        return True

    def check_constraints(self, metrics: Dict[str, float]) -> list:
        """
        检查约束条件

        Args:
            metrics: 仿真指标

        Returns:
            违规列表
        """
        constraints = self.config.get("constraints", {})
        return build_simulation_constraint_rows(
            scalar_metrics=dict(metrics or {}),
            runtime_constraints=dict(constraints or {}),
        )

    def __enter__(self):
        """上下文管理器入口"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.disconnect()
