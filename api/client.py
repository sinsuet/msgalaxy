#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
API客户端

提供Python接口用于调用REST API
"""

import requests
from typing import Dict, Any, List, Optional
from pathlib import Path
import time


class APIClient:
    """API客户端"""

    def __init__(self, base_url: str = "http://localhost:5000"):
        """
        初始化客户端

        Args:
            base_url: API服务器地址
        """
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        response = self.session.get(f"{self.base_url}/api/health")
        response.raise_for_status()
        return response.json()

    def create_task(
        self,
        bom_file: str,
        max_iterations: int = 20,
        convergence_threshold: float = 0.01,
        config_path: str = "config/system.yaml"
    ) -> Dict[str, Any]:
        """
        创建优化任务

        Args:
            bom_file: BOM文件路径
            max_iterations: 最大迭代次数
            convergence_threshold: 收敛阈值
            config_path: 配置文件路径

        Returns:
            任务信息
        """
        data = {
            "bom_file": bom_file,
            "max_iterations": max_iterations,
            "convergence_threshold": convergence_threshold,
            "config_path": config_path
        }

        response = self.session.post(
            f"{self.base_url}/api/tasks",
            json=data
        )
        response.raise_for_status()
        return response.json()

    def get_task(self, task_id: str) -> Dict[str, Any]:
        """
        获取任务状态

        Args:
            task_id: 任务ID

        Returns:
            任务信息
        """
        response = self.session.get(f"{self.base_url}/api/tasks/{task_id}")
        response.raise_for_status()
        return response.json()

    def list_tasks(self) -> Dict[str, Any]:
        """
        列出所有任务

        Returns:
            任务列表
        """
        response = self.session.get(f"{self.base_url}/api/tasks")
        response.raise_for_status()
        return response.json()

    def get_task_result(self, task_id: str) -> Dict[str, Any]:
        """
        获取任务结果

        Args:
            task_id: 任务ID

        Returns:
            任务结果
        """
        response = self.session.get(
            f"{self.base_url}/api/tasks/{task_id}/result"
        )
        response.raise_for_status()
        return response.json()

    def download_visualization(
        self,
        task_id: str,
        filename: str,
        output_path: str
    ):
        """
        下载可视化图片

        Args:
            task_id: 任务ID
            filename: 文件名
            output_path: 输出路径
        """
        response = self.session.get(
            f"{self.base_url}/api/tasks/{task_id}/visualizations/{filename}"
        )
        response.raise_for_status()

        with open(output_path, 'wb') as f:
            f.write(response.content)

    def list_experiments(self) -> Dict[str, Any]:
        """
        列出所有实验

        Returns:
            实验列表
        """
        response = self.session.get(f"{self.base_url}/api/experiments")
        response.raise_for_status()
        return response.json()

    def validate_bom(self, bom_file: str) -> Dict[str, Any]:
        """
        验证BOM文件

        Args:
            bom_file: BOM文件路径

        Returns:
            验证结果
        """
        data = {"bom_file": bom_file}
        response = self.session.post(
            f"{self.base_url}/api/bom/validate",
            json=data
        )
        response.raise_for_status()
        return response.json()

    def wait_for_task(
        self,
        task_id: str,
        poll_interval: float = 2.0,
        timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        等待任务完成

        Args:
            task_id: 任务ID
            poll_interval: 轮询间隔（秒）
            timeout: 超时时间（秒）

        Returns:
            任务信息

        Raises:
            TimeoutError: 超时
        """
        start_time = time.time()

        while True:
            task = self.get_task(task_id)
            status = task["status"]

            if status in ["completed", "failed"]:
                return task

            if timeout and (time.time() - start_time) > timeout:
                raise TimeoutError(f"Task {task_id} timed out")

            time.sleep(poll_interval)

    def run_optimization(
        self,
        bom_file: str,
        max_iterations: int = 20,
        convergence_threshold: float = 0.01,
        config_path: str = "config/system.yaml",
        wait: bool = True,
        poll_interval: float = 2.0,
        timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        运行优化（便捷方法）

        Args:
            bom_file: BOM文件路径
            max_iterations: 最大迭代次数
            convergence_threshold: 收敛阈值
            config_path: 配置文件路径
            wait: 是否等待完成
            poll_interval: 轮询间隔（秒）
            timeout: 超时时间（秒）

        Returns:
            任务信息
        """
        # 创建任务
        task = self.create_task(
            bom_file=bom_file,
            max_iterations=max_iterations,
            convergence_threshold=convergence_threshold,
            config_path=config_path
        )

        task_id = task["task_id"]
        print(f"Task created: {task_id}")

        if wait:
            print("Waiting for task to complete...")
            task = self.wait_for_task(
                task_id,
                poll_interval=poll_interval,
                timeout=timeout
            )

            if task["status"] == "completed":
                print(f"Task completed successfully")
            else:
                print(f"Task failed: {task.get('error')}")

        return task


# ============ 命令行接口 ============

def main():
    """命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="API客户端")
    parser.add_argument(
        "--base-url",
        default="http://localhost:5000",
        help="API服务器地址"
    )

    subparsers = parser.add_subparsers(dest="command", help="命令")

    # health命令
    subparsers.add_parser("health", help="健康检查")

    # run命令
    run_parser = subparsers.add_parser("run", help="运行优化")
    run_parser.add_argument("bom_file", help="BOM文件路径")
    run_parser.add_argument(
        "--max-iterations",
        type=int,
        default=20,
        help="最大迭代次数"
    )
    run_parser.add_argument(
        "--no-wait",
        action="store_true",
        help="不等待完成"
    )

    # list命令
    subparsers.add_parser("list", help="列出所有任务")

    # status命令
    status_parser = subparsers.add_parser("status", help="查看任务状态")
    status_parser.add_argument("task_id", help="任务ID")

    # result命令
    result_parser = subparsers.add_parser("result", help="获取任务结果")
    result_parser.add_argument("task_id", help="任务ID")

    # validate命令
    validate_parser = subparsers.add_parser("validate", help="验证BOM文件")
    validate_parser.add_argument("bom_file", help="BOM文件路径")

    args = parser.parse_args()

    # 创建客户端
    client = APIClient(base_url=args.base_url)

    try:
        if args.command == "health":
            result = client.health_check()
            print(f"Status: {result['status']}")
            print(f"Timestamp: {result['timestamp']}")

        elif args.command == "run":
            task = client.run_optimization(
                bom_file=args.bom_file,
                max_iterations=args.max_iterations,
                wait=not args.no_wait
            )
            print(f"\nTask ID: {task['id']}")
            print(f"Status: {task['status']}")

        elif args.command == "list":
            result = client.list_tasks()
            print(f"Total tasks: {result['total']}\n")
            for task in result['tasks']:
                print(f"ID: {task['id']}")
                print(f"Status: {task['status']}")
                print(f"Created: {task['created_at']}")
                print()

        elif args.command == "status":
            task = client.get_task(args.task_id)
            print(f"Task ID: {task['id']}")
            print(f"Status: {task['status']}")
            print(f"Created: {task['created_at']}")
            if task['started_at']:
                print(f"Started: {task['started_at']}")
            if task['completed_at']:
                print(f"Completed: {task['completed_at']}")
            if task['error']:
                print(f"Error: {task['error']}")

        elif args.command == "result":
            result = client.get_task_result(args.task_id)
            print(f"Task ID: {result['task_id']}")
            print(f"Experiment: {result['experiment_dir']}")
            print(f"\nSummary:")
            for key, value in result['summary'].items():
                print(f"  {key}: {value}")

        elif args.command == "validate":
            result = client.validate_bom(args.bom_file)
            print(f"Valid: {result['valid']}")
            print(f"Components: {result['num_components']}")
            if result['errors']:
                print("\nErrors:")
                for error in result['errors']:
                    print(f"  - {error}")

        else:
            parser.print_help()

    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")
        exit(1)


if __name__ == "__main__":
    main()
