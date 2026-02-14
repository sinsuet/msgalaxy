"""
简化物理引擎

提供快速的物理场估算，用于快速迭代和测试
基于mssim的简化物理模型
"""

import numpy as np
from typing import Dict, Any, List, Tuple

from simulation.base import SimulationDriver
from core.protocol import SimulationRequest, SimulationResult, ViolationItem
from core.logger import get_logger

logger = get_logger(__name__)


class SimplifiedPhysicsEngine(SimulationDriver):
    """简化物理引擎"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化简化物理引擎

        Args:
            config: 配置字典
        """
        super().__init__(config)

        # 物理参数
        self.ambient_temp = 20.0  # 环境温度（°C）
        self.heat_source_power = 100.0  # 热源功率（W）
        self.heat_source_pos = np.array([0.0, 0.0, 0.0])  # 热源位置
        self.safe_distance = 3.0  # 安全距离（mm）

    def connect(self) -> bool:
        """简化引擎不需要连接"""
        self.connected = True
        logger.info("✓ 简化物理引擎已就绪")
        return True

    def disconnect(self):
        """简化引擎不需要断开"""
        self.connected = False

    def run_simulation(self, request: SimulationRequest) -> SimulationResult:
        """
        运行简化物理仿真

        Args:
            request: 仿真请求

        Returns:
            仿真结果
        """
        if not self.validate_design_state(request.design_state):
            return SimulationResult(
                success=False,
                metrics={},
                violations=[],
                error_message="设计状态无效"
            )

        try:
            logger.info("运行简化物理仿真...")

            design_state = request.design_state

            # 1. 计算热场
            max_temp, temp_violations = self._compute_thermal(design_state)

            # 2. 计算几何干涉
            min_clearance, geo_violations = self._compute_geometry(design_state)

            # 3. 计算总质量和功率
            total_mass = sum(comp.mass for comp in design_state.components)
            total_power = sum(comp.power for comp in design_state.components)

            # 4. 汇总指标
            metrics = {
                'max_temp': max_temp,
                'min_clearance': min_clearance,
                'total_mass': total_mass,
                'total_power': total_power
            }

            # 5. 汇总违规
            all_violations = temp_violations + geo_violations

            # 6. 检查约束
            constraint_violations = self.check_constraints(metrics)
            all_violations.extend(constraint_violations)

            logger.info(f"  仿真完成: {metrics}")
            logger.info(f"  违规数: {len(all_violations)}")

            return SimulationResult(
                success=True,
                metrics=metrics,
                violations=[ViolationItem(**v) for v in all_violations]
            )

        except Exception as e:
            logger.error(f"简化物理仿真失败: {e}")
            return SimulationResult(
                success=False,
                metrics={},
                violations=[],
                error_message=str(e)
            )

    def _compute_thermal(self, design_state) -> Tuple[float, List[Dict]]:
        """
        计算热场（简化模型）

        使用反平方律估算温度分布：
        T = T_ambient + Q / (r^2 + offset)

        Args:
            design_state: 设计状态

        Returns:
            (最大温度, 违规列表)
        """
        violations = []
        max_temp = self.ambient_temp

        # 假设热源在原点
        heat_source_pos = self.heat_source_pos

        for comp in design_state.components:
            # 计算组件中心到热源的距离
            comp_center = np.array([
                comp.position.x + comp.dimensions.x / 2,
                comp.position.y + comp.dimensions.y / 2,
                comp.position.z + comp.dimensions.z / 2
            ])

            distance_sq = np.sum((comp_center - heat_source_pos) ** 2)

            # 温度计算（反平方律 + 自身功率）
            # T = T_ambient + (heat_source_power + self_power) / (distance^2 + offset)
            offset = 1000.0  # 偏移量，避免距离为0时温度无穷大
            temp = self.ambient_temp + (self.heat_source_power + comp.power) / (distance_sq + offset)

            max_temp = max(max_temp, temp)

            # 检查温度限制
            temp_limit = self.config.get('constraints', {}).get('max_temp_c', 50.0)
            if temp > temp_limit:
                # severity限制在0-1范围内
                severity = min(1.0, (temp - temp_limit) / temp_limit)
                violations.append({
                    'id': f'TEMP_{comp.id}',
                    'type': 'THERMAL_OVERHEAT',
                    'description': f'{comp.id} temperature {temp:.1f}°C > {temp_limit}°C',
                    'involved_components': [comp.id],
                    'severity': severity
                })

        return max_temp, violations

    def _compute_geometry(self, design_state) -> Tuple[float, List[Dict]]:
        """
        计算几何干涉

        检查组件之间的最小间隙

        Args:
            design_state: 设计状态

        Returns:
            (最小间隙, 违规列表)
        """
        violations = []
        min_clearance = float('inf')

        components = design_state.components
        n = len(components)

        # 两两检查组件间隙
        for i in range(n):
            comp_a = components[i]
            min_a = np.array([comp_a.position.x, comp_a.position.y, comp_a.position.z])
            max_a = min_a + np.array([comp_a.dimensions.x, comp_a.dimensions.y, comp_a.dimensions.z])

            for j in range(i + 1, n):
                comp_b = components[j]
                min_b = np.array([comp_b.position.x, comp_b.position.y, comp_b.position.z])
                max_b = min_b + np.array([comp_b.dimensions.x, comp_b.dimensions.y, comp_b.dimensions.z])

                # 计算AABB间隙
                clearance = self._compute_aabb_clearance(min_a, max_a, min_b, max_b)
                min_clearance = min(min_clearance, clearance)

                # 检查是否碰撞
                if clearance < 0:
                    violations.append({
                        'id': f'CLASH_{comp_a.id}_{comp_b.id}',
                        'type': 'GEOMETRY_CLASH',
                        'description': f'{comp_a.id} and {comp_b.id} overlap by {-clearance:.1f}mm',
                        'involved_components': [comp_a.id, comp_b.id],
                        'severity': 1.0
                    })
                elif clearance < self.safe_distance:
                    # severity限制在0-1范围内
                    severity = min(1.0, (self.safe_distance - clearance) / self.safe_distance)
                    violations.append({
                        'id': f'CLEARANCE_{comp_a.id}_{comp_b.id}',
                        'type': 'GEOMETRY_CLASH',
                        'description': f'{comp_a.id} and {comp_b.id} clearance {clearance:.1f}mm < {self.safe_distance}mm',
                        'involved_components': [comp_a.id, comp_b.id],
                        'severity': severity
                    })

        return min_clearance if min_clearance != float('inf') else 0.0, violations

    def _compute_aabb_clearance(self, min_a: np.ndarray, max_a: np.ndarray,
                                 min_b: np.ndarray, max_b: np.ndarray) -> float:
        """
        计算两个AABB之间的最小间隙

        Args:
            min_a, max_a: AABB A的最小和最大点
            min_b, max_b: AABB B的最小和最大点

        Returns:
            最小间隙（负值表示重叠）
        """
        # 检查是否重叠
        overlap_x = (min_a[0] < max_b[0]) and (min_b[0] < max_a[0])
        overlap_y = (min_a[1] < max_b[1]) and (min_b[1] < max_a[1])
        overlap_z = (min_a[2] < max_b[2]) and (min_b[2] < max_a[2])

        if overlap_x and overlap_y and overlap_z:
            # 重叠：计算重叠深度
            overlap_depth_x = min(max_a[0] - min_b[0], max_b[0] - min_a[0])
            overlap_depth_y = min(max_a[1] - min_b[1], max_b[1] - min_a[1])
            overlap_depth_z = min(max_a[2] - min_b[2], max_b[2] - min_a[2])
            return -min(overlap_depth_x, overlap_depth_y, overlap_depth_z)

        # 不重叠：计算最小间隙
        gap_x = max(0, max(min_a[0] - max_b[0], min_b[0] - max_a[0]))
        gap_y = max(0, max(min_a[1] - max_b[1], min_b[1] - max_a[1]))
        gap_z = max(0, max(min_a[2] - max_b[2], min_b[2] - max_a[2]))

        # 返回最小的非零间隙
        gaps = [g for g in [gap_x, gap_y, gap_z] if g > 0]
        return min(gaps) if gaps else 0.0

    def set_heat_source(self, position: np.ndarray, power: float):
        """
        设置热源

        Args:
            position: 热源位置 [x, y, z]
            power: 热源功率（W）
        """
        self.heat_source_pos = np.array(position)
        self.heat_source_power = power
        logger.info(f"热源设置: 位置={position}, 功率={power}W")
