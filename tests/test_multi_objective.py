#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
多目标优化模块测试
"""

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from optimization.multi_objective import (
    MultiObjectiveOptimizer,
    ObjectiveDefinition,
    ParetoSolution,
    create_pareto_solution
)
from core.protocol import DesignState, ComponentGeometry, Vector3D, Envelope, SimulationResult


def create_test_solution(
    iteration: int,
    max_temp: float,
    total_mass: float,
    volume_util: float
) -> ParetoSolution:
    """创建测试解"""
    comp = ComponentGeometry(
        id=f"comp_{iteration}",
        position=Vector3D(x=10.0, y=10.0, z=10.0),
        dimensions=Vector3D(x=50.0, y=50.0, z=50.0),
        mass=total_mass,
        power=10.0,
        category="test"
    )

    envelope = Envelope(outer_size=Vector3D(x=200.0, y=200.0, z=200.0))

    design = DesignState(iteration=iteration, components=[comp], envelope=envelope)

    sim_result = SimulationResult(
        success=True,
        metrics={
            "max_temp": max_temp,
            "total_mass": total_mass,
            "volume_utilization": volume_util
        }
    )

    objectives = [
        ObjectiveDefinition(name="max_temp", direction="minimize"),
        ObjectiveDefinition(name="total_mass", direction="minimize"),
        ObjectiveDefinition(name="volume_utilization", direction="maximize")
    ]

    return create_pareto_solution(design, sim_result, objectives)


class TestObjectiveDefinition:
    """目标定义测试"""

    def test_objective_creation(self):
        """测试目标创建"""
        obj = ObjectiveDefinition(
            name="max_temp",
            direction="minimize",
            weight=1.0
        )

        assert obj.name == "max_temp"
        assert obj.direction == "minimize"
        assert obj.weight == 1.0
        assert obj.constraint is None

    def test_objective_with_constraint(self):
        """测试带约束的目标"""
        obj = ObjectiveDefinition(
            name="max_temp",
            direction="minimize",
            weight=1.0,
            constraint=70.0
        )

        assert obj.constraint == 70.0


class TestMultiObjectiveOptimizer:
    """多目标优化器测试"""

    def test_initialization(self):
        """测试初始化"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize"),
            ObjectiveDefinition(name="total_mass", direction="minimize")
        ]

        optimizer = MultiObjectiveOptimizer(objectives)
        assert len(optimizer.objectives) == 2

    def test_initialization_empty_objectives(self):
        """测试空目标列表"""
        with pytest.raises(ValueError, match="At least one objective"):
            MultiObjectiveOptimizer([])

    def test_initialization_invalid_direction(self):
        """测试无效的优化方向"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="invalid")
        ]

        with pytest.raises(ValueError, match="Invalid direction"):
            MultiObjectiveOptimizer(objectives)

    def test_dominates_minimize(self):
        """测试支配关系（最小化）"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize"),
            ObjectiveDefinition(name="total_mass", direction="minimize")
        ]

        optimizer = MultiObjectiveOptimizer(objectives)

        # sol1支配sol2（两个目标都更小）
        sol1 = create_test_solution(0, max_temp=50.0, total_mass=10.0, volume_util=0.3)
        sol2 = create_test_solution(1, max_temp=60.0, total_mass=12.0, volume_util=0.3)

        assert optimizer.dominates(sol1, sol2)
        assert not optimizer.dominates(sol2, sol1)

    def test_dominates_maximize(self):
        """测试支配关系（最大化）"""
        objectives = [
            ObjectiveDefinition(name="volume_utilization", direction="maximize")
        ]

        optimizer = MultiObjectiveOptimizer(objectives)

        sol1 = create_test_solution(0, max_temp=50.0, total_mass=10.0, volume_util=0.5)
        sol2 = create_test_solution(1, max_temp=50.0, total_mass=10.0, volume_util=0.3)

        assert optimizer.dominates(sol1, sol2)
        assert not optimizer.dominates(sol2, sol1)

    def test_dominates_mixed(self):
        """测试支配关系（混合）"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize"),
            ObjectiveDefinition(name="volume_utilization", direction="maximize")
        ]

        optimizer = MultiObjectiveOptimizer(objectives)

        # sol1支配sol2（温度更低，利用率更高）
        sol1 = create_test_solution(0, max_temp=50.0, total_mass=10.0, volume_util=0.5)
        sol2 = create_test_solution(1, max_temp=60.0, total_mass=10.0, volume_util=0.3)

        assert optimizer.dominates(sol1, sol2)

    def test_non_dominated_sort(self):
        """测试非支配排序"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize"),
            ObjectiveDefinition(name="total_mass", direction="minimize")
        ]

        optimizer = MultiObjectiveOptimizer(objectives)

        # 创建测试解
        solutions = [
            create_test_solution(0, max_temp=50.0, total_mass=10.0, volume_util=0.3),  # Pareto前沿
            create_test_solution(1, max_temp=60.0, total_mass=8.0, volume_util=0.3),   # Pareto前沿
            create_test_solution(2, max_temp=70.0, total_mass=12.0, volume_util=0.3),  # 被支配
            create_test_solution(3, max_temp=55.0, total_mass=15.0, volume_util=0.3),  # 被支配
        ]

        ranked = optimizer.non_dominated_sort(solutions)

        # 检查rank
        assert ranked[0].rank == 0  # Pareto前沿
        assert ranked[1].rank == 0  # Pareto前沿
        assert ranked[2].rank > 0   # 被支配
        assert ranked[3].rank > 0   # 被支配

    def test_compute_pareto_front(self):
        """测试Pareto前沿计算"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize"),
            ObjectiveDefinition(name="total_mass", direction="minimize")
        ]

        optimizer = MultiObjectiveOptimizer(objectives)

        solutions = [
            create_test_solution(0, max_temp=50.0, total_mass=10.0, volume_util=0.3),
            create_test_solution(1, max_temp=60.0, total_mass=8.0, volume_util=0.3),
            create_test_solution(2, max_temp=70.0, total_mass=12.0, volume_util=0.3),
        ]

        pareto_front = optimizer.compute_pareto_front(solutions)

        # 前两个解应该在Pareto前沿上
        assert len(pareto_front) == 2
        assert all(sol.rank == 0 for sol in pareto_front)

    def test_compute_pareto_front_empty(self):
        """测试空解列表"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize")
        ]

        optimizer = MultiObjectiveOptimizer(objectives)
        pareto_front = optimizer.compute_pareto_front([])

        assert len(pareto_front) == 0

    def test_select_compromise_weighted_sum(self):
        """测试加权和折衷解选择"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize", weight=1.0),
            ObjectiveDefinition(name="total_mass", direction="minimize", weight=0.5)
        ]

        optimizer = MultiObjectiveOptimizer(objectives)

        solutions = [
            create_test_solution(0, max_temp=50.0, total_mass=10.0, volume_util=0.3),
            create_test_solution(1, max_temp=60.0, total_mass=8.0, volume_util=0.3),
        ]

        pareto_front = optimizer.compute_pareto_front(solutions)
        compromise = optimizer.select_compromise_solution(pareto_front, method="weighted_sum")

        assert compromise is not None
        # 第一个解应该更好（50*1.0 + 10*0.5 = 55 < 60*1.0 + 8*0.5 = 64）
        assert compromise.design_state.iteration == 0

    def test_select_compromise_min_distance(self):
        """测试最小距离折衷解选择"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize"),
            ObjectiveDefinition(name="total_mass", direction="minimize")
        ]

        optimizer = MultiObjectiveOptimizer(objectives)

        solutions = [
            create_test_solution(0, max_temp=50.0, total_mass=10.0, volume_util=0.3),
            create_test_solution(1, max_temp=60.0, total_mass=8.0, volume_util=0.3),
        ]

        pareto_front = optimizer.compute_pareto_front(solutions)
        compromise = optimizer.select_compromise_solution(pareto_front, method="min_distance")

        assert compromise is not None

    def test_select_compromise_max_crowding(self):
        """测试最大拥挤距离折衷解选择"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize"),
            ObjectiveDefinition(name="total_mass", direction="minimize")
        ]

        optimizer = MultiObjectiveOptimizer(objectives)

        solutions = [
            create_test_solution(0, max_temp=50.0, total_mass=10.0, volume_util=0.3),
            create_test_solution(1, max_temp=60.0, total_mass=8.0, volume_util=0.3),
        ]

        pareto_front = optimizer.compute_pareto_front(solutions)
        compromise = optimizer.select_compromise_solution(pareto_front, method="max_crowding")

        assert compromise is not None

    def test_select_compromise_invalid_method(self):
        """测试无效的选择方法"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize")
        ]

        optimizer = MultiObjectiveOptimizer(objectives)

        solutions = [
            create_test_solution(0, max_temp=50.0, total_mass=10.0, volume_util=0.3)
        ]

        pareto_front = optimizer.compute_pareto_front(solutions)

        with pytest.raises(ValueError, match="Unknown selection method"):
            optimizer.select_compromise_solution(pareto_front, method="invalid")

    def test_get_objective_statistics(self):
        """测试目标统计信息"""
        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize"),
            ObjectiveDefinition(name="total_mass", direction="minimize")
        ]

        optimizer = MultiObjectiveOptimizer(objectives)

        solutions = [
            create_test_solution(0, max_temp=50.0, total_mass=10.0, volume_util=0.3),
            create_test_solution(1, max_temp=60.0, total_mass=12.0, volume_util=0.3),
            create_test_solution(2, max_temp=70.0, total_mass=14.0, volume_util=0.3),
        ]

        stats = optimizer.get_objective_statistics(solutions)

        assert "max_temp" in stats
        assert "total_mass" in stats

        assert stats["max_temp"]["min"] == 50.0
        assert stats["max_temp"]["max"] == 70.0
        assert stats["max_temp"]["mean"] == 60.0

        assert stats["total_mass"]["min"] == 10.0
        assert stats["total_mass"]["max"] == 14.0


class TestCreateParetoSolution:
    """创建Pareto解测试"""

    def test_create_pareto_solution(self):
        """测试创建Pareto解"""
        comp = ComponentGeometry(
            id="test_comp",
            position=Vector3D(x=10.0, y=10.0, z=10.0),
            dimensions=Vector3D(x=50.0, y=50.0, z=50.0),
            mass=5.0,
            power=10.0,
            category="test"
        )

        envelope = Envelope(outer_size=Vector3D(x=200.0, y=200.0, z=200.0))

        design = DesignState(iteration=0, components=[comp], envelope=envelope)

        sim_result = SimulationResult(
            success=True,
            metrics={
                "max_temp": 50.0,
                "total_mass": 10.0
            }
        )

        objectives = [
            ObjectiveDefinition(name="max_temp", direction="minimize"),
            ObjectiveDefinition(name="total_mass", direction="minimize")
        ]

        solution = create_pareto_solution(design, sim_result, objectives)

        assert solution.design_state == design
        assert solution.simulation_result == sim_result
        assert solution.objective_values["max_temp"] == 50.0
        assert solution.objective_values["total_mass"] == 10.0
        assert solution.rank == 0
        assert solution.crowding_distance == 0.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
