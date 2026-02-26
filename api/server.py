#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
REST API服务器

提供HTTP接口用于：
- 提交优化任务
- 查询任务状态
- 获取结果和可视化
- 管理实验
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
import threading
import uuid
from datetime import datetime

from workflow.orchestrator import WorkflowOrchestrator
from core.logger import get_logger
from core.exceptions import SatelliteDesignError

logger = get_logger("api_server")

# 创建Flask应用
app = Flask(__name__)
CORS(app)  # 启用CORS支持

# 创建SocketIO实例
socketio = SocketIO(app, cors_allowed_origins="*")

# 任务存储
tasks: Dict[str, Dict[str, Any]] = {}
tasks_lock = threading.Lock()


class TaskStatus:
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def emit_task_update(task_id: str, event_type: str, data: Dict[str, Any]):
    """
    通过WebSocket发送任务更新

    Args:
        task_id: 任务ID
        event_type: 事件类型 (status_change, progress, iteration_complete, error)
        data: 事件数据
    """
    try:
        socketio.emit('task_update', {
            'task_id': task_id,
            'event_type': event_type,
            'data': data,
            'timestamp': datetime.now().isoformat()
        }, namespace='/tasks')
        logger.debug(f"Emitted {event_type} for task {task_id}")
    except Exception as e:
        logger.error(f"Failed to emit task update: {e}")


def run_optimization_task(task_id: str, config: Dict[str, Any]):
    """
    在后台运行优化任务

    Args:
        task_id: 任务ID
        config: 配置参数
    """
    try:
        # 更新任务状态
        with tasks_lock:
            tasks[task_id]["status"] = TaskStatus.RUNNING
            tasks[task_id]["started_at"] = datetime.now().isoformat()

        logger.info(f"Starting optimization task: {task_id}")

        # 发送状态更新
        emit_task_update(task_id, 'status_change', {
            'status': TaskStatus.RUNNING,
            'message': 'Optimization started'
        })

        # 创建编排器
        orchestrator = WorkflowOrchestrator(
            config_path=config.get("config_path", "config/system.yaml")
        )

        # 运行优化
        max_iterations = config.get("max_iterations", 20)

        # 发送进度更新
        emit_task_update(task_id, 'progress', {
            'current_iteration': 0,
            'max_iterations': max_iterations,
            'progress_percent': 0
        })

        final_state = orchestrator.run_optimization(
            bom_file=config.get("bom_file"),
            max_iterations=max_iterations,
            convergence_threshold=config.get("convergence_threshold", 0.01)
        )

        # 生成可视化
        emit_task_update(task_id, 'progress', {
            'message': 'Generating visualizations...',
            'progress_percent': 90
        })

        from core.visualization import generate_visualizations
        generate_visualizations(orchestrator.logger.run_dir)

        # 更新任务状态
        with tasks_lock:
            tasks[task_id]["status"] = TaskStatus.COMPLETED
            tasks[task_id]["completed_at"] = datetime.now().isoformat()
            tasks[task_id]["result"] = {
                "experiment_dir": orchestrator.logger.run_dir,
                "final_iteration": final_state.iteration,
                "num_components": len(final_state.components)
            }

        logger.info(f"Optimization task completed: {task_id}")

        # 发送完成通知
        emit_task_update(task_id, 'status_change', {
            'status': TaskStatus.COMPLETED,
            'message': 'Optimization completed successfully',
            'result': tasks[task_id]["result"]
        })

    except Exception as e:
        logger.error(f"Optimization task failed: {task_id}", exc_info=True)

        with tasks_lock:
            tasks[task_id]["status"] = TaskStatus.FAILED
            tasks[task_id]["error"] = str(e)
            tasks[task_id]["completed_at"] = datetime.now().isoformat()

        # 发送错误通知
        emit_task_update(task_id, 'error', {
            'status': TaskStatus.FAILED,
            'error': str(e)
        })


# ============ WebSocket事件处理 ============

@socketio.on('connect', namespace='/tasks')
def handle_connect():
    """客户端连接"""
    logger.info(f"Client connected: {request.sid}")
    emit('connected', {'message': 'Connected to task updates'})


@socketio.on('disconnect', namespace='/tasks')
def handle_disconnect():
    """客户端断开连接"""
    logger.info(f"Client disconnected: {request.sid}")


@socketio.on('subscribe', namespace='/tasks')
def handle_subscribe(data):
    """订阅特定任务的更新"""
    task_id = data.get('task_id')
    if task_id:
        logger.info(f"Client {request.sid} subscribed to task {task_id}")
        emit('subscribed', {'task_id': task_id, 'message': f'Subscribed to task {task_id}'})
    else:
        emit('error', {'message': 'task_id is required'})


# ============ API端点 ============

@app.route("/api/health", methods=["GET"])
def health_check():
    """健康检查"""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat()
    })


@app.route("/api/tasks", methods=["POST"])
def create_task():
    """
    创建优化任务

    请求体:
    {
        "bom_file": "config/bom_example.json",
        "max_iterations": 20,
        "convergence_threshold": 0.01,
        "config_path": "config/system.yaml"
    }
    """
    try:
        data = request.get_json(force=True, silent=True)

        # 验证必需参数
        if not data:
            return jsonify({"error": "Request body is required"}), 400

        # 生成任务ID
        task_id = str(uuid.uuid4())

        # 创建任务记录
        task = {
            "id": task_id,
            "status": TaskStatus.PENDING,
            "config": data,
            "created_at": datetime.now().isoformat(),
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None
        }

        with tasks_lock:
            tasks[task_id] = task

        # 在后台线程中运行优化
        thread = threading.Thread(
            target=run_optimization_task,
            args=(task_id, data)
        )
        thread.daemon = True
        thread.start()

        logger.info(f"Created optimization task: {task_id}")

        return jsonify({
            "task_id": task_id,
            "status": task["status"],
            "created_at": task["created_at"]
        }), 201

    except Exception as e:
        logger.error("Failed to create task", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/tasks/<task_id>", methods=["GET"])
def get_task(task_id: str):
    """获取任务状态"""
    with tasks_lock:
        task = tasks.get(task_id)

    if not task:
        return jsonify({"error": "Task not found"}), 404

    return jsonify(task)


@app.route("/api/tasks", methods=["GET"])
def list_tasks():
    """列出所有任务"""
    with tasks_lock:
        task_list = list(tasks.values())

    # 按创建时间倒序排序
    task_list.sort(key=lambda x: x["created_at"], reverse=True)

    return jsonify({
        "tasks": task_list,
        "total": len(task_list)
    })


@app.route("/api/tasks/<task_id>/result", methods=["GET"])
def get_task_result(task_id: str):
    """获取任务结果详情"""
    with tasks_lock:
        task = tasks.get(task_id)

    if not task:
        return jsonify({"error": "Task not found"}), 404

    if task["status"] != TaskStatus.COMPLETED:
        return jsonify({"error": "Task not completed"}), 400

    # 读取实验结果
    experiment_dir = task["result"]["experiment_dir"]

    # 读取summary
    summary_path = os.path.join(experiment_dir, "summary.json")
    if os.path.exists(summary_path):
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)
    else:
        summary = {}

    # 读取evolution trace
    csv_path = os.path.join(experiment_dir, "evolution_trace.csv")
    evolution_data = []
    if os.path.exists(csv_path):
        import csv
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            evolution_data = list(reader)

    return jsonify({
        "task_id": task_id,
        "summary": summary,
        "evolution": evolution_data,
        "experiment_dir": experiment_dir
    })


@app.route("/api/tasks/<task_id>/visualizations/<filename>", methods=["GET"])
def get_visualization(task_id: str, filename: str):
    """获取可视化图片"""
    with tasks_lock:
        task = tasks.get(task_id)

    if not task:
        return jsonify({"error": "Task not found"}), 404

    if task["status"] != TaskStatus.COMPLETED:
        return jsonify({"error": "Task not completed"}), 400

    # 构建文件路径
    experiment_dir = task["result"]["experiment_dir"]
    viz_path = os.path.join(experiment_dir, "visualizations", filename)

    if not os.path.exists(viz_path):
        return jsonify({"error": "Visualization not found"}), 404

    return send_file(viz_path, mimetype='image/png')


@app.route("/api/experiments", methods=["GET"])
def list_experiments():
    """列出所有实验"""
    experiments_dir = Path("experiments")

    if not experiments_dir.exists():
        return jsonify({"experiments": [], "total": 0})

    experiments = []
    for exp_dir in experiments_dir.iterdir():
        if exp_dir.is_dir() and exp_dir.name.startswith("run_"):
            # 读取summary
            summary_path = exp_dir / "summary.json"
            if summary_path.exists():
                with open(summary_path, 'r', encoding='utf-8') as f:
                    summary = json.load(f)
            else:
                summary = {}

            experiments.append({
                "name": exp_dir.name,
                "path": str(exp_dir),
                "summary": summary
            })

    # 按时间倒序排序
    experiments.sort(key=lambda x: x["name"], reverse=True)

    return jsonify({
        "experiments": experiments,
        "total": len(experiments)
    })


@app.route("/api/bom/validate", methods=["POST"])
def validate_bom():
    """
    验证BOM文件

    请求体:
    {
        "bom_file": "config/bom_example.json"
    }
    """
    try:
        data = request.get_json()
        bom_file = data.get("bom_file")

        if not bom_file:
            return jsonify({"error": "bom_file is required"}), 400

        from core.bom_parser import BOMParser

        # 解析BOM
        components = BOMParser.parse(bom_file)

        # 验证
        errors = BOMParser.validate(components)

        return jsonify({
            "valid": len(errors) == 0,
            "num_components": len(components),
            "errors": errors,
            "components": [
                {
                    "id": c.id,
                    "name": c.name,
                    "mass": c.mass,
                    "power": c.power,
                    "category": c.category
                }
                for c in components
            ]
        })

    except Exception as e:
        logger.error("BOM validation failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ============ 错误处理 ============

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500


# ============ 主函数 ============

def run_server(host: str = "0.0.0.0", port: int = 5000, debug: bool = False):
    """
    运行API服务器

    Args:
        host: 主机地址
        port: 端口号
        debug: 调试模式
    """
    logger.info(f"Starting API server with WebSocket support on {host}:{port}")
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    run_server(debug=True)
