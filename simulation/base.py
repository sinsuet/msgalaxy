"""
仿真基类

定义所有仿真驱动器的统一接口
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from core.protocol import SimulationRequest, SimulationResult, DesignState
from core.logger import get_logger

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
        violations = []
        constraints = self.config.get('constraints', {})

        # 检查温度约束
        if 'max_temp_c' in constraints:
            max_temp = metrics.get('max_temp', 0)
            limit = constraints['max_temp_c']
            if max_temp > limit:
                # 限制severity在0-1范围内
                severity = min(1.0, (max_temp - limit) / limit)
                violations.append({
                    'id': f'TEMP_VIOLATION',
                    'type': 'THERMAL_OVERHEAT',
                    'description': f'Temperature {max_temp:.1f}°C > {limit}°C',
                    'involved_components': ['system'],
                    'severity': severity
                })

        # 检查间隙约束
        if 'min_clearance_mm' in constraints:
            min_clearance = metrics.get('min_clearance', float('inf'))
            limit = constraints['min_clearance_mm']
            if min_clearance < limit:
                # 限制severity在0-1范围内
                severity = min(1.0, (limit - min_clearance) / limit)
                violations.append({
                    'id': f'CLEARANCE_VIOLATION',
                    'type': 'GEOMETRY_CLASH',
                    'description': f'Clearance {min_clearance:.1f}mm < {limit}mm',
                    'involved_components': ['system'],
                    'severity': severity
                })

        # 检查质量约束
        if 'max_mass_kg' in constraints:
            total_mass = metrics.get('total_mass', 0)
            limit = constraints['max_mass_kg']
            if total_mass > limit:
                # 限制severity在0-1范围内
                severity = min(1.0, (total_mass - limit) / limit)
                violations.append({
                    'id': f'MASS_VIOLATION',
                    'type': 'MASS_LIMIT',
                    'description': f'Mass {total_mass:.1f}kg > {limit}kg',
                    'involved_components': ['system'],
                    'severity': severity
                })

        # 检查功率约束
        if 'max_power_w' in constraints:
            total_power = metrics.get('total_power', 0)
            limit = constraints['max_power_w']
            if total_power > limit:
                # 限制severity在0-1范围内
                severity = min(1.0, (total_power - limit) / limit)
                violations.append({
                    'id': f'POWER_VIOLATION',
                    'type': 'POWER_LIMIT',
                    'description': f'Power {total_power:.1f}W > {limit}W',
                    'involved_components': ['system'],
                    'severity': severity
                })

        return violations

    def __enter__(self):
        """上下文管理器入口"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.disconnect()
