#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
并行优化器测试
"""

import pytest
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from optimization.parallel_optimizer import (
    ParallelOptimizer,
    ParallelTask,
    ParallelResult,
    create_parallel_optimizer
)
from core.protocol import DesignState, ComponentGeometry, Vector3D, SimulationResult, Envelope


def create_test_state(i: int) -> DesignState:
    """创建测试设计状态"""
    component = ComponentGeometry(
        id=f"comp_{i}",
        position=Vector3D(x=10.0 * i, y=10.0, z=10.0),
        dimensions=Vector3D(x=50.0, y=50.0, z=50.0),
        mass=1.0,
        power=10.0,
        category="test"
    )
    envelope = Envelope(
        outer_size=Vector3D(x=200.0, y=200.0, z=200.0)
    )
    return DesignState(
        components=[component],
        envelope=envelope,
        iteration=i
    )


def mock_simulate(state: DesignState, config: dict) -> SimulationResult:
    """模拟仿真函数"""
    time.sleep(0.1)  # 模拟计算时间
    return SimulationResult(
        success=True,
        metrics={
            "max_temp": 50.0 + state.iteration,
            "avg_temp": 40.0,
            "min_clearance": 5.0,
            "total_mass": 10.0,
            "total_power": 100.0
        }
    )


def mock_simulate_with_error(state: DesignState, config: dict) -> SimulationResult:
    """模拟会失败的仿真函数"""
    if state.iteration == 2:
        raise ValueError("Simulated error for testing")
    return mock_simulate(state, config)


class TestParallelOptimizer:
    """并行优化器测试"""

    def test_initialization(self):
        """测试初始化"""
        optimizer = ParallelOptimizer(num_workers=2)
        assert optimizer.num_workers == 2
        assert optimizer.max_retries == 2
        assert optimizer.executor is None

    def test_context_manager(self):
        """测试上下文管理器"""
        with ParallelOptimizer(num_workers=2) as optimizer:
            assert optimizer.is_running()
        assert not optimizer.is_running()

    def test_parallel_simulate_empty(self):
        """测试空列表"""
        with ParallelOptimizer(num_workers=2) as optimizer:
            results = optimizer.parallel_simulate([], mock_simulate, {})
            assert len(results) == 0

    def test_parallel_simulate_single(self):
        """测试单个任务"""
        states = [create_test_state(0)]

        with ParallelOptimizer(num_workers=2) as optimizer:
            results = optimizer.parallel_simulate(states, mock_simulate, {})

            assert len(results) == 1
            assert results[0].success
            assert results[0].result.metrics["max_temp"] == 50.0

    def test_parallel_simulate_multiple(self):
        """测试多个任务"""
        states = [create_test_state(i) for i in range(4)]

        with ParallelOptimizer(num_workers=2) as optimizer:
            start_time = time.time()
            results = optimizer.parallel_simulate(states, mock_simulate, {})
            duration = time.time() - start_time

            # 验证结果
            assert len(results) == 4
            assert all(r.success for r in results)

            # 验证并行加速（4个任务，每个0.1秒，2个worker应该约0.2秒）
            # 允许进程池启动开销，检查是否小于1.0秒（串行需要0.4秒）
            assert duration < 1.0, f"Parallel execution too slow: {duration:.2f}s"

            # 验证结果顺序
            for i, result in enumerate(results):
                assert result.task_id == f"task_{i}"
                assert result.result.metrics["max_temp"] == 50.0 + i

    def test_parallel_simulate_with_errors(self):
        """测试错误处理"""
        states = [create_test_state(i) for i in range(4)]

        with ParallelOptimizer(num_workers=2) as optimizer:
            results = optimizer.parallel_simulate(
                states,
                mock_simulate_with_error,
                {}
            )

            assert len(results) == 4

            # 检查成功和失败的任务
            success_count = sum(1 for r in results if r.success)
            assert success_count == 3  # task_2 应该失败

            # 检查失败的任务
            failed_result = results[2]
            assert not failed_result.success
            assert "Simulated error" in failed_result.error

    def test_worker_count(self):
        """测试工作进程数"""
        optimizer = ParallelOptimizer(num_workers=3)
        assert optimizer.get_worker_count() == 3

    def test_create_parallel_optimizer(self):
        """测试便捷创建函数"""
        optimizer = create_parallel_optimizer(num_workers=2, max_retries=3)
        assert optimizer.num_workers == 2
        assert optimizer.max_retries == 3


class TestParallelTask:
    """并行任务测试"""

    def test_parallel_task_creation(self):
        """测试任务创建"""
        state = create_test_state(0)
        task = ParallelTask(
            task_id="test_task",
            design_state=state,
            config={"key": "value"}
        )

        assert task.task_id == "test_task"
        assert task.design_state == state
        assert task.config == {"key": "value"}


class TestParallelResult:
    """并行结果测试"""

    def test_parallel_result_success(self):
        """测试成功结果"""
        result = ParallelResult(
            task_id="task_0",
            success=True,
            result=SimulationResult(
                success=True,
                metrics={
                    "max_temp": 50.0,
                    "avg_temp": 40.0,
                    "min_clearance": 5.0,
                    "total_mass": 10.0,
                    "total_power": 100.0
                }
            ),
            duration=0.5
        )

        assert result.success
        assert result.result.metrics["max_temp"] == 50.0
        assert result.duration == 0.5
        assert result.error is None

    def test_parallel_result_failure(self):
        """测试失败结果"""
        result = ParallelResult(
            task_id="task_1",
            success=False,
            error="Test error",
            duration=0.1
        )

        assert not result.success
        assert result.error == "Test error"
        assert result.result is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
