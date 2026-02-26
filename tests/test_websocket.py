#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WebSocket功能测试
"""

import pytest
import time
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.websocket_client import TaskWebSocketClient


class TestWebSocketClient:
    """WebSocket客户端测试"""

    def test_client_creation(self):
        """测试客户端创建"""
        client = TaskWebSocketClient("http://localhost:5000")
        assert client.server_url == "http://localhost:5000"
        assert client.connected == False

    def test_callback_assignment(self):
        """测试回调函数赋值"""
        client = TaskWebSocketClient("http://localhost:5000")

        def test_callback(task_id, data):
            pass

        client.on_status_change = test_callback
        client.on_progress = test_callback
        client.on_error = test_callback

        assert client.on_status_change == test_callback
        assert client.on_progress == test_callback
        assert client.on_error == test_callback


class TestWebSocketIntegration:
    """WebSocket集成测试（需要服务器运行）"""

    @pytest.mark.skip(reason="需要服务器运行")
    def test_connection(self):
        """测试连接到服务器"""
        client = TaskWebSocketClient("http://localhost:5000")

        try:
            client.connect()
            assert client.connected == True
            time.sleep(1)  # 等待连接确认
        finally:
            client.disconnect()

    @pytest.mark.skip(reason="需要服务器运行")
    def test_subscribe(self):
        """测试订阅任务"""
        client = TaskWebSocketClient("http://localhost:5000")

        try:
            client.connect()
            time.sleep(1)

            # 订阅一个测试任务ID
            client.subscribe("test-task-id")
            time.sleep(1)
        finally:
            client.disconnect()

    @pytest.mark.skip(reason="需要服务器运行")
    def test_receive_updates(self):
        """测试接收任务更新"""
        client = TaskWebSocketClient("http://localhost:5000")

        received_updates = []

        def on_status_change(task_id, data):
            received_updates.append(('status', task_id, data))

        def on_progress(task_id, data):
            received_updates.append(('progress', task_id, data))

        client.on_status_change = on_status_change
        client.on_progress = on_progress

        try:
            client.connect()
            time.sleep(1)

            # 这里需要实际创建一个任务来测试
            # 由于需要完整的系统运行，这里只是框架

            time.sleep(5)  # 等待接收更新

            # 验证收到了更新
            # assert len(received_updates) > 0
        finally:
            client.disconnect()


class TestWebSocketEventHandling:
    """WebSocket事件处理测试"""

    def test_status_change_event(self):
        """测试状态变更事件处理"""
        client = TaskWebSocketClient("http://localhost:5000")

        received_data = []

        def on_status_change(task_id, data):
            received_data.append((task_id, data))

        client.on_status_change = on_status_change

        # 模拟接收事件
        test_data = {
            'task_id': 'test-123',
            'event_type': 'status_change',
            'data': {
                'status': 'running',
                'message': 'Test message'
            }
        }

        client._on_task_update(test_data)

        assert len(received_data) == 1
        assert received_data[0][0] == 'test-123'
        assert received_data[0][1]['status'] == 'running'

    def test_progress_event(self):
        """测试进度事件处理"""
        client = TaskWebSocketClient("http://localhost:5000")

        received_data = []

        def on_progress(task_id, data):
            received_data.append((task_id, data))

        client.on_progress = on_progress

        # 模拟接收事件
        test_data = {
            'task_id': 'test-123',
            'event_type': 'progress',
            'data': {
                'current_iteration': 5,
                'max_iterations': 20,
                'progress_percent': 25
            }
        }

        client._on_task_update(test_data)

        assert len(received_data) == 1
        assert received_data[0][1]['progress_percent'] == 25

    def test_error_event(self):
        """测试错误事件处理"""
        client = TaskWebSocketClient("http://localhost:5000")

        received_data = []

        def on_error(task_id, data):
            received_data.append((task_id, data))

        client.on_error = on_error

        # 模拟接收事件
        test_data = {
            'task_id': 'test-123',
            'event_type': 'error',
            'data': {
                'error': 'Test error message'
            }
        }

        client._on_task_update(test_data)

        assert len(received_data) == 1
        assert 'error' in received_data[0][1]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
