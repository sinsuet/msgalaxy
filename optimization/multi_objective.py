#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
多目标优化模块 - Pareto前沿计算

支持多个优化目标的权衡分析，计算Pareto最优解集。
"""

import logging
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
import numpy as np

from core.protocol import DesignState, SimulationResult

logger = logging.getLogger(__name__)


@dataclass
class ObjectiveDefinition:
    """目标定义"""
    name: str  # 目标名称，如 "max_temp", "total_mass"
    direction: str  # "minimize" 或 "maximize"
    weight: float = 1.0  # 权重
    constraint: Optional[float] = None  # 约束值（如最大温度限制）


@dataclass
class ParetoSolution:
    """Pareto解"""
    design_state: DesignState
    simulation_result: SimulationResult
    objective_values: Dict[str, float]  # 目标值
    rank: int = 0  # Pareto等级（0=前沿）
    crowding_distance: float = 0.0  # 拥挤距离


class MultiObjectiveOptimizer:
    """
    多目标优化器

    实现基于Pareto支配关系的多目标优化，计算Pareto前沿。

    特性:
    - 多目标权衡分析
    - Pareto前沿计算
    - 非支配排序
    - 拥挤距离计算
    - 折衷方案生成
    """

    def __init__(self, objectives: List[ObjectiveDefinition]):
        """
        初始化多目标优化器

        Args:
            objectives: 目标定义列表
        """
        self.objectives = objectives
        self._validate_objectives()

        logger.info(
            f"MultiObjectiveOptimizer initialized with {len(objectives)} objectives"
        )

    def _validate_objectives(self):
        """验证目标定义"""
        if not self.objectives:
            raise ValueError("At least one objective is required")

        for obj in self.objectives:
            if obj.direction not in ["minimize", "maximize"]:
                raise ValueError(
                    f"Invalid direction '{obj.direction}' for objective '{obj.name}'. "
                    "Use 'minimize' or 'maximize'."
                )

    def compute_pareto_front(
        self,
        solutions: List[ParetoSolution]
    ) -> List[ParetoSolution]:
        """
        计算Pareto前沿

        Args:
            solutions: 候选解列表

        Returns:
            Pareto前沿解列表（rank=0的解）
        """
        if not solutions:
            return []

        # 非支配排序
        ranked_solutions = self.non_dominated_sort(solutions)

        # 返回第一层（Pareto前沿）
        pareto_front = [sol for sol in ranked_solutions if sol.rank == 0]

        # 计算拥挤距离
        self._compute_crowding_distance(pareto_front)

        logger.info(
            f"Pareto front computed: {len(pareto_front)}/{len(solutions)} solutions"
        )

        return pareto_front

    def non_dominated_sort(
        self,
        solutions: List[ParetoSolution]
    ) -> List[ParetoSolution]:
        """
        非支配排序

        将解按Pareto支配关系分层。

        Args:
            solutions: 候选解列表

        Returns:
            排序后的解列表（带rank）
        """
        n = len(solutions)

        # 初始化
        domination_count = [0] * n  # 被支配次数
        dominated_solutions = [[] for _ in range(n)]  # 支配的解
        fronts = [[]]  # 各层前沿

        # 计算支配关系
        for i in range(n):
            for j in range(i + 1, n):
                if self.dominates(solutions[i], solutions[j]):
                    dominated_solutions[i].append(j)
                    domination_count[j] += 1
                elif self.dominates(solutions[j], solutions[i]):
                    dominated_solutions[j].append(i)
                    domination_count[i] += 1

        # 第一层：未被支配的解
        for i in range(n):
            if domination_count[i] == 0:
                solutions[i].rank = 0
                fronts[0].append(i)

        # 后续层
        current_front = 0
        while current_front < len(fronts) and fronts[current_front]:
            next_front = []

            for i in fronts[current_front]:
                for j in dominated_solutions[i]:
                    domination_count[j] -= 1
                    if domination_count[j] == 0:
                        solutions[j].rank = current_front + 1
                        next_front.append(j)

            current_front += 1
            if next_front:
                fronts.append(next_front)

        return solutions

    def dominates(self, sol1: ParetoSolution, sol2: ParetoSolution) -> bool:
        """
        判断sol1是否支配sol2

        支配定义：
        - sol1在所有目标上不差于sol2
        - sol1至少在一个目标上优于sol2

        Args:
            sol1: 解1
            sol2: 解2

        Returns:
            sol1是否支配sol2
        """
        better_in_all = True
        better_in_one = False

        for obj in self.objectives:
            val1 = sol1.objective_values.get(obj.name, 0.0)
            val2 = sol2.objective_values.get(obj.name, 0.0)

            if obj.direction == "minimize":
                if val1 > val2:
                    better_in_all = False
                if val1 < val2:
                    better_in_one = True
            else:  # maximize
                if val1 < val2:
                    better_in_all = False
                if val1 > val2:
                    better_in_one = True

        return better_in_all and better_in_one

    def _compute_crowding_distance(self, solutions: List[ParetoSolution]):
        """
        计算拥挤距离

        拥挤距离用于保持解的多样性，距离越大表示该解越独特。

        Args:
            solutions: 解列表（会原地修改crowding_distance）
        """
        n = len(solutions)
        if n <= 2:
            for sol in solutions:
                sol.crowding_distance = float('inf')
            return

        # 初始化
        for sol in solutions:
            sol.crowding_distance = 0.0

        # 对每个目标计算拥挤距离
        for obj in self.objectives:
            # 按该目标排序
            solutions.sort(key=lambda s: s.objective_values.get(obj.name, 0.0))

            # 边界解设为无穷大
            solutions[0].crowding_distance = float('inf')
            solutions[-1].crowding_distance = float('inf')

            # 目标值范围
            obj_min = solutions[0].objective_values.get(obj.name, 0.0)
            obj_max = solutions[-1].objective_values.get(obj.name, 0.0)
            obj_range = obj_max - obj_min

            if obj_range == 0:
                continue

            # 计算中间解的拥挤距离
            for i in range(1, n - 1):
                if solutions[i].crowding_distance != float('inf'):
                    val_prev = solutions[i - 1].objective_values.get(obj.name, 0.0)
                    val_next = solutions[i + 1].objective_values.get(obj.name, 0.0)
                    solutions[i].crowding_distance += (val_next - val_prev) / obj_range

    def select_compromise_solution(
        self,
        pareto_front: List[ParetoSolution],
        method: str = "weighted_sum"
    ) -> Optional[ParetoSolution]:
        """
        从Pareto前沿选择折衷解

        Args:
            pareto_front: Pareto前沿解列表
            method: 选择方法
                - "weighted_sum": 加权和
                - "min_distance": 最小距离（到理想点）
                - "max_crowding": 最大拥挤距离

        Returns:
            折衷解
        """
        if not pareto_front:
            return None

        if method == "weighted_sum":
            return self._select_by_weighted_sum(pareto_front)
        elif method == "min_distance":
            return self._select_by_min_distance(pareto_front)
        elif method == "max_crowding":
            return max(pareto_front, key=lambda s: s.crowding_distance)
        else:
            raise ValueError(f"Unknown selection method: {method}")

    def _select_by_weighted_sum(
        self,
        solutions: List[ParetoSolution]
    ) -> ParetoSolution:
        """使用加权和选择解"""
        best_solution = None
        best_score = float('inf')

        for sol in solutions:
            score = 0.0

            for obj in self.objectives:
                val = sol.objective_values.get(obj.name, 0.0)

                if obj.direction == "minimize":
                    score += obj.weight * val
                else:  # maximize
                    score -= obj.weight * val

            if score < best_score:
                best_score = score
                best_solution = sol

        return best_solution

    def _select_by_min_distance(
        self,
        solutions: List[ParetoSolution]
    ) -> ParetoSolution:
        """使用到理想点的最小距离选择解"""
        # 计算理想点（每个目标的最优值）
        ideal_point = {}

        for obj in self.objectives:
            values = [s.objective_values.get(obj.name, 0.0) for s in solutions]

            if obj.direction == "minimize":
                ideal_point[obj.name] = min(values)
            else:
                ideal_point[obj.name] = max(values)

        # 计算到理想点的距离
        best_solution = None
        min_distance = float('inf')

        for sol in solutions:
            distance = 0.0

            for obj in self.objectives:
                val = sol.objective_values.get(obj.name, 0.0)
                ideal_val = ideal_point[obj.name]

                # 归一化距离
                distance += ((val - ideal_val) * obj.weight) ** 2

            distance = np.sqrt(distance)

            if distance < min_distance:
                min_distance = distance
                best_solution = sol

        return best_solution

    def get_objective_statistics(
        self,
        solutions: List[ParetoSolution]
    ) -> Dict[str, Dict[str, float]]:
        """
        获取目标统计信息

        Args:
            solutions: 解列表

        Returns:
            每个目标的统计信息（min, max, mean, std）
        """
        stats = {}

        for obj in self.objectives:
            values = [s.objective_values.get(obj.name, 0.0) for s in solutions]

            stats[obj.name] = {
                "min": float(np.min(values)),
                "max": float(np.max(values)),
                "mean": float(np.mean(values)),
                "std": float(np.std(values))
            }

        return stats


def create_pareto_solution(
    design_state: DesignState,
    simulation_result: SimulationResult,
    objectives: List[ObjectiveDefinition]
) -> ParetoSolution:
    """
    创建Pareto解的便捷函数

    Args:
        design_state: 设计状态
        simulation_result: 仿真结果
        objectives: 目标定义列表

    Returns:
        Pareto解
    """
    objective_values = {}

    for obj in objectives:
        # 从仿真结果的metrics中提取目标值
        if obj.name in simulation_result.metrics:
            objective_values[obj.name] = simulation_result.metrics[obj.name]
        else:
            logger.warning(f"Objective '{obj.name}' not found in simulation metrics")
            objective_values[obj.name] = 0.0

    return ParetoSolution(
        design_state=design_state,
        simulation_result=simulation_result,
        objective_values=objective_values
    )


# 示例使用
if __name__ == "__main__":
    import time

    # 配置日志
    logging.basicConfig(level=logging.INFO)

    # 定义目标
    objectives = [
        ObjectiveDefinition(name="max_temp", direction="minimize", weight=1.0),
        ObjectiveDefinition(name="total_mass", direction="minimize", weight=0.5),
        ObjectiveDefinition(name="volume_utilization", direction="maximize", weight=0.3)
    ]

    # 创建优化器
    optimizer = MultiObjectiveOptimizer(objectives)

    # 创建测试解（模拟）
    from core.protocol import ComponentGeometry, Vector3D, Envelope

    solutions = []
    for i in range(10):
        comp = ComponentGeometry(
            id=f"comp_{i}",
            position=Vector3D(x=10.0, y=10.0, z=10.0),
            dimensions=Vector3D(x=50.0, y=50.0, z=50.0),
            mass=1.0 + i * 0.1,
            power=10.0,
            category="test"
        )

        envelope = Envelope(outer_size=Vector3D(x=200.0, y=200.0, z=200.0))

        design = DesignState(iteration=i, components=[comp], envelope=envelope)

        sim_result = SimulationResult(
            success=True,
            metrics={
                "max_temp": 50.0 + i * 2,
                "total_mass": 10.0 + i * 0.5,
                "volume_utilization": 0.3 + i * 0.02
            }
        )

        sol = create_pareto_solution(design, sim_result, objectives)
        solutions.append(sol)

    # 计算Pareto前沿
    pareto_front = optimizer.compute_pareto_front(solutions)

    print(f"\nPareto front: {len(pareto_front)} solutions")
    for sol in pareto_front:
        print(f"  Iteration {sol.design_state.iteration}: "
              f"temp={sol.objective_values['max_temp']:.1f}, "
              f"mass={sol.objective_values['total_mass']:.1f}, "
              f"util={sol.objective_values['volume_utilization']:.2f}")

    # 选择折衷解
    compromise = optimizer.select_compromise_solution(pareto_front, method="weighted_sum")
    print(f"\nCompromise solution: Iteration {compromise.design_state.iteration}")

    # 统计信息
    stats = optimizer.get_objective_statistics(solutions)
    print("\nObjective statistics:")
    for obj_name, obj_stats in stats.items():
        print(f"  {obj_name}: min={obj_stats['min']:.2f}, max={obj_stats['max']:.2f}, "
              f"mean={obj_stats['mean']:.2f}")
