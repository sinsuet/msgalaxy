#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
并行优化器 - 支持多进程并行仿真

提供多进程并行执行仿真任务的能力，显著提升优化效率。
"""

import logging
from typing import List, Dict, Any, Optional, Callable
from concurrent.futures import ProcessPoolExecutor, Future, as_completed
from multiprocessing import cpu_count
import time
from dataclasses import dataclass

from core.protocol import DesignState, SimulationResult
from core.exceptions import SimulationError

logger = logging.getLogger(__name__)


@dataclass
class ParallelTask:
    """并行任务"""
    task_id: str
    design_state: DesignState
    config: Dict[str, Any]


@dataclass
class ParallelResult:
    """并行任务结果"""
    task_id: str
    success: bool
    result: Optional[SimulationResult] = None
    error: Optional[str] = None
    duration: float = 0.0


class ParallelOptimizer:
    """
    并行优化器

    使用多进程池并行执行仿真任务，提升计算效率。

    特性:
    - 多进程并行仿真
    - 任务队列管理
    - 结果聚合
    - 错误处理和重试
    - 负载均衡
    """

    def __init__(
        self,
        num_workers: Optional[int] = None,
        max_retries: int = 2,
        timeout: Optional[float] = None
    ):
        """
        初始化并行优化器

        Args:
            num_workers: 工作进程数，默认为CPU核心数
            max_retries: 任务失败最大重试次数
            timeout: 单个任务超时时间(秒)
        """
        self.num_workers = num_workers or max(1, cpu_count() - 1)
        self.max_retries = max_retries
        self.timeout = timeout
        self.executor: Optional[ProcessPoolExecutor] = None

        logger.info(f"ParallelOptimizer initialized with {self.num_workers} workers")

    def __enter__(self):
        """上下文管理器入口"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出"""
        self.shutdown()

    def start(self):
        """启动进程池"""
        if self.executor is None:
            self.executor = ProcessPoolExecutor(max_workers=self.num_workers)
            logger.info(f"Process pool started with {self.num_workers} workers")

    def shutdown(self, wait: bool = True):
        """
        关闭进程池

        Args:
            wait: 是否等待所有任务完成
        """
        if self.executor is not None:
            self.executor.shutdown(wait=wait)
            self.executor = None
            logger.info("Process pool shut down")

    def parallel_simulate(
        self,
        design_states: List[DesignState],
        simulate_func: Callable[[DesignState, Dict[str, Any]], SimulationResult],
        config: Dict[str, Any]
    ) -> List[ParallelResult]:
        """
        并行仿真多个设计状态

        Args:
            design_states: 设计状态列表
            simulate_func: 仿真函数
            config: 仿真配置

        Returns:
            并行结果列表
        """
        if not design_states:
            return []

        if self.executor is None:
            self.start()

        # 创建任务
        tasks = [
            ParallelTask(
                task_id=f"task_{i}",
                design_state=state,
                config=config
            )
            for i, state in enumerate(design_states)
        ]

        logger.info(f"Submitting {len(tasks)} tasks to process pool")

        # 提交任务
        futures: Dict[Future, ParallelTask] = {}
        for task in tasks:
            future = self.executor.submit(
                self._execute_task,
                task,
                simulate_func
            )
            futures[future] = task

        # 收集结果
        results: List[ParallelResult] = []
        completed = 0

        for future in as_completed(futures, timeout=self.timeout):
            task = futures[future]
            completed += 1

            try:
                result = future.result()
                results.append(result)

                if result.success:
                    logger.info(
                        f"Task {task.task_id} completed successfully "
                        f"({completed}/{len(tasks)}, {result.duration:.2f}s)"
                    )
                else:
                    logger.warning(
                        f"Task {task.task_id} failed: {result.error} "
                        f"({completed}/{len(tasks)})"
                    )

            except Exception as e:
                logger.error(f"Task {task.task_id} raised exception: {e}")
                results.append(ParallelResult(
                    task_id=task.task_id,
                    success=False,
                    error=str(e)
                ))

        # 按task_id排序
        results.sort(key=lambda r: r.task_id)

        success_count = sum(1 for r in results if r.success)
        logger.info(
            f"Parallel simulation completed: "
            f"{success_count}/{len(results)} successful"
        )

        return results

    @staticmethod
    def _execute_task(
        task: ParallelTask,
        simulate_func: Callable[[DesignState, Dict[str, Any]], SimulationResult]
    ) -> ParallelResult:
        """
        执行单个仿真任务（在子进程中运行）

        Args:
            task: 并行任务
            simulate_func: 仿真函数

        Returns:
            并行结果
        """
        start_time = time.time()

        try:
            # 执行仿真
            result = simulate_func(task.design_state, task.config)

            duration = time.time() - start_time

            return ParallelResult(
                task_id=task.task_id,
                success=True,
                result=result,
                duration=duration
            )

        except Exception as e:
            duration = time.time() - start_time

            return ParallelResult(
                task_id=task.task_id,
                success=False,
                error=str(e),
                duration=duration
            )

    def get_worker_count(self) -> int:
        """获取工作进程数"""
        return self.num_workers

    def is_running(self) -> bool:
        """检查进程池是否运行中"""
        return self.executor is not None


def create_parallel_optimizer(
    num_workers: Optional[int] = None,
    **kwargs
) -> ParallelOptimizer:
    """
    创建并行优化器的便捷函数

    Args:
        num_workers: 工作进程数
        **kwargs: 其他参数传递给ParallelOptimizer

    Returns:
        并行优化器实例
    """
    return ParallelOptimizer(num_workers=num_workers, **kwargs)


# 示例使用
if __name__ == "__main__":
    from core.protocol import ComponentGeometry, Vector3D

    # 配置日志
    logging.basicConfig(level=logging.INFO)

    # 创建测试设计状态
    def create_test_state(i: int) -> DesignState:
        component = ComponentGeometry(
            id=f"comp_{i}",
            position=Vector3D(x=10.0 * i, y=10.0, z=10.0),
            dimensions=Vector3D(x=50.0, y=50.0, z=50.0),
            mass=1.0,
            power=10.0,
            category="test"
        )
        return DesignState(
            components=[component],
            iteration=i,
            timestamp=time.time()
        )

    # 模拟仿真函数
    def mock_simulate(state: DesignState, config: Dict[str, Any]) -> SimulationResult:
        time.sleep(0.5)  # 模拟计算时间
        return SimulationResult(
            max_temp=50.0 + state.iteration,
            avg_temp=40.0,
            min_clearance=5.0,
            total_mass=10.0,
            total_power=100.0,
            success=True
        )

    # 测试并行优化
    states = [create_test_state(i) for i in range(8)]

    with ParallelOptimizer(num_workers=4) as optimizer:
        results = optimizer.parallel_simulate(
            design_states=states,
            simulate_func=mock_simulate,
            config={}
        )

        print(f"\nCompleted {len(results)} simulations")
        for result in results:
            if result.success:
                print(f"  {result.task_id}: max_temp={result.result.max_temp:.1f}°C, "
                      f"duration={result.duration:.2f}s")
