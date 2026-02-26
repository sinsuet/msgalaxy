#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
API服务器单元测试
"""

import pytest
import json
import time
from pathlib import Path

# 导入Flask应用
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.server import app, tasks, TaskStatus


@pytest.fixture
def client():
    """创建测试客户端"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def clear_tasks():
    """每个测试前清空任务列表"""
    tasks.clear()
    yield
    tasks.clear()


class TestHealthCheck:
    """健康检查测试"""

    def test_health_check(self, client):
        """测试健康检查端点"""
        response = client.get('/api/health')
        assert response.status_code == 200

        data = response.get_json()
        assert data['status'] == 'ok'
        assert 'timestamp' in data


class TestTaskManagement:
    """任务管理测试"""

    def test_create_task(self, client):
        """测试创建任务"""
        payload = {
            "bom_file": "config/bom_example.json",
            "max_iterations": 10
        }

        response = client.post(
            '/api/tasks',
            data=json.dumps(payload),
            content_type='application/json'
        )

        assert response.status_code == 201

        data = response.get_json()
        assert 'task_id' in data
        assert data['status'] == TaskStatus.PENDING
        assert 'created_at' in data

    def test_create_task_without_body(self, client):
        """测试创建任务时缺少请求体"""
        response = client.post('/api/tasks')
        assert response.status_code == 400

        data = response.get_json()
        assert 'error' in data

    def test_get_task(self, client):
        """测试获取任务状态"""
        # 先创建任务
        payload = {"bom_file": "config/bom_example.json"}
        response = client.post(
            '/api/tasks',
            data=json.dumps(payload),
            content_type='application/json'
        )
        task_id = response.get_json()['task_id']

        # 获取任务状态
        response = client.get(f'/api/tasks/{task_id}')
        assert response.status_code == 200

        data = response.get_json()
        assert data['id'] == task_id
        assert 'status' in data
        assert 'config' in data

    def test_get_nonexistent_task(self, client):
        """测试获取不存在的任务"""
        response = client.get('/api/tasks/nonexistent-id')
        assert response.status_code == 404

        data = response.get_json()
        assert 'error' in data

    def test_list_tasks(self, client):
        """测试列出所有任务"""
        # 创建几个任务
        for i in range(3):
            payload = {"bom_file": f"config/bom_{i}.json"}
            client.post(
                '/api/tasks',
                data=json.dumps(payload),
                content_type='application/json'
            )

        # 列出任务
        response = client.get('/api/tasks')
        assert response.status_code == 200

        data = response.get_json()
        assert data['total'] == 3
        assert len(data['tasks']) == 3

    def test_list_tasks_empty(self, client):
        """测试列出空任务列表"""
        response = client.get('/api/tasks')
        assert response.status_code == 200

        data = response.get_json()
        assert data['total'] == 0
        assert len(data['tasks']) == 0


class TestTaskResults:
    """任务结果测试"""

    def test_get_result_not_completed(self, client):
        """测试获取未完成任务的结果"""
        # 创建任务
        payload = {"bom_file": "config/bom_example.json"}
        response = client.post(
            '/api/tasks',
            data=json.dumps(payload),
            content_type='application/json'
        )
        task_id = response.get_json()['task_id']

        # 尝试获取结果
        response = client.get(f'/api/tasks/{task_id}/result')
        assert response.status_code == 400

        data = response.get_json()
        assert 'error' in data

    def test_get_visualization_not_completed(self, client):
        """测试获取未完成任务的可视化"""
        # 创建任务
        payload = {"bom_file": "config/bom_example.json"}
        response = client.post(
            '/api/tasks',
            data=json.dumps(payload),
            content_type='application/json'
        )
        task_id = response.get_json()['task_id']

        # 尝试获取可视化
        response = client.get(
            f'/api/tasks/{task_id}/visualizations/evolution_trace.png'
        )
        assert response.status_code == 400


class TestBOMValidation:
    """BOM验证测试"""

    def test_validate_bom_missing_file(self, client):
        """测试验证不存在的BOM文件"""
        payload = {"bom_file": "nonexistent.json"}
        response = client.post(
            '/api/bom/validate',
            data=json.dumps(payload),
            content_type='application/json'
        )

        assert response.status_code == 500
        data = response.get_json()
        assert 'error' in data

    def test_validate_bom_missing_parameter(self, client):
        """测试验证BOM时缺少参数"""
        payload = {}
        response = client.post(
            '/api/bom/validate',
            data=json.dumps(payload),
            content_type='application/json'
        )

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data


class TestExperiments:
    """实验管理测试"""

    def test_list_experiments(self, client):
        """测试列出实验"""
        response = client.get('/api/experiments')
        assert response.status_code == 200

        data = response.get_json()
        assert 'experiments' in data
        assert 'total' in data
        assert isinstance(data['experiments'], list)


class TestErrorHandling:
    """错误处理测试"""

    def test_404_error(self, client):
        """测试404错误"""
        response = client.get('/api/nonexistent')
        assert response.status_code == 404

        data = response.get_json()
        assert 'error' in data


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
