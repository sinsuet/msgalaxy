#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WebSocket客户端示例

演示如何使用WebSocket接收实时任务更新
"""

import socketio
import time
from typing import Callable, Optional

from core.logger import get_logger

logger = get_logger("websocket_client")


class TaskWebSocketClient:
    """任务WebSocket客户端"""

    def __init__(self, server_url: str = "http://localhost:5000"):
        """
        初始化WebSocket客户端

        Args:
            server_url: 服务器URL
        """
        self.server_url = server_url
        self.sio = socketio.Client()
        self.connected = False

        # 注册事件处理器
        self.sio.on('connect', self._on_connect, namespace='/tasks')
        self.sio.on('disconnect', self._on_disconnect, namespace='/tasks')
        self.sio.on('connected', self._on_connected, namespace='/tasks')
        self.sio.on('task_update', self._on_task_update, namespace='/tasks')
        self.sio.on('error', self._on_error, namespace='/tasks')

        # 用户自定义回调
        self.on_status_change: Optional[Callable] = None
        self.on_progress: Optional[Callable] = None
        self.on_iteration_complete: Optional[Callable] = None
        self.on_error: Optional[Callable] = None

    def connect(self):
        """连接到服务器"""
        try:
            logger.info(f"Connecting to {self.server_url}")
            self.sio.connect(self.server_url, namespaces=['/tasks'])
            self.connected = True
            logger.info("Connected successfully")
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            raise

    def disconnect(self):
        """断开连接"""
        if self.connected:
            self.sio.disconnect()
            self.connected = False
            logger.info("Disconnected")

    def subscribe(self, task_id: str):
        """
        订阅任务更新

        Args:
            task_id: 任务ID
        """
        if not self.connected:
            raise RuntimeError("Not connected to server")

        logger.info(f"Subscribing to task {task_id}")
        self.sio.emit('subscribe', {'task_id': task_id}, namespace='/tasks')

    def wait_for_completion(self, timeout: Optional[float] = None):
        """
        等待任务完成

        Args:
            timeout: 超时时间(秒)
        """
        start_time = time.time()
        while self.connected:
            if timeout and (time.time() - start_time) > timeout:
                logger.warning("Wait timeout")
                break
            time.sleep(0.1)

    # ============ 内部事件处理器 ============

    def _on_connect(self):
        """连接成功"""
        logger.info("WebSocket connected")

    def _on_disconnect(self):
        """连接断开"""
        logger.info("WebSocket disconnected")
        self.connected = False

    def _on_connected(self, data):
        """收到连接确认"""
        logger.info(f"Server confirmed: {data.get('message')}")

    def _on_task_update(self, data):
        """收到任务更新"""
        task_id = data.get('task_id')
        event_type = data.get('event_type')
        event_data = data.get('data', {})
        timestamp = data.get('timestamp')

        logger.info(f"Task {task_id} update: {event_type}")

        # 根据事件类型调用相应的回调
        if event_type == 'status_change':
            if self.on_status_change:
                self.on_status_change(task_id, event_data)
            else:
                status = event_data.get('status')
                message = event_data.get('message')
                logger.info(f"Status changed to {status}: {message}")

        elif event_type == 'progress':
            if self.on_progress:
                self.on_progress(task_id, event_data)
            else:
                current = event_data.get('current_iteration', 0)
                max_iter = event_data.get('max_iterations', 0)
                percent = event_data.get('progress_percent', 0)
                message = event_data.get('message', '')
                if message:
                    logger.info(f"Progress: {message}")
                else:
                    logger.info(f"Progress: {current}/{max_iter} ({percent}%)")

        elif event_type == 'iteration_complete':
            if self.on_iteration_complete:
                self.on_iteration_complete(task_id, event_data)
            else:
                iteration = event_data.get('iteration')
                logger.info(f"Iteration {iteration} completed")

        elif event_type == 'error':
            if self.on_error:
                self.on_error(task_id, event_data)
            else:
                error = event_data.get('error')
                logger.error(f"Task error: {error}")

    def _on_error(self, data):
        """收到错误消息"""
        logger.error(f"WebSocket error: {data.get('message')}")


# ============ 使用示例 ============

def example_usage():
    """使用示例"""
    # 创建客户端
    client = TaskWebSocketClient("http://localhost:5000")

    # 定义回调函数
    def on_status_change(task_id, data):
        status = data.get('status')
        message = data.get('message')
        print(f"[STATUS] Task {task_id}: {status} - {message}")

    def on_progress(task_id, data):
        percent = data.get('progress_percent', 0)
        message = data.get('message', '')
        if message:
            print(f"[PROGRESS] {message}")
        else:
            print(f"[PROGRESS] {percent}%")

    def on_error(task_id, data):
        error = data.get('error')
        print(f"[ERROR] Task {task_id}: {error}")

    # 设置回调
    client.on_status_change = on_status_change
    client.on_progress = on_progress
    client.on_error = on_error

    try:
        # 连接到服务器
        client.connect()

        # 订阅任务(假设已经创建了任务)
        task_id = "your-task-id-here"
        client.subscribe(task_id)

        # 等待任务完成
        print("Waiting for task updates...")
        client.wait_for_completion(timeout=600)  # 10分钟超时

    except KeyboardInterrupt:
        print("\nInterrupted by user")
    finally:
        client.disconnect()


if __name__ == "__main__":
    example_usage()
