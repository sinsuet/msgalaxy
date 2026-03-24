#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scenario-driven REST API server.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_socketio import SocketIO, emit

from api.experiment_index import (
    iter_experiment_dirs,
    load_json_if_exists,
    load_latest_index,
    resolve_experiment_dir,
    resolve_experiments_root,
    serialize_experiment_dir,
)
from core.logger import get_logger
from domain.satellite.scenario import load_satellite_scenario_spec
from run.run_scenario import (
    REGISTRY_PATH,
    _load_executor,
    _load_registry,
    _resolve_abs_path,
    _resolve_registry_entry,
)
from workflow.scenario_runtime import load_runtime_config


logger = get_logger("api_server", persist_global=True)
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

tasks: Dict[str, Dict[str, Any]] = {}
tasks_lock = threading.Lock()


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _experiments_root() -> Path:
    configured = app.config.get("EXPERIMENTS_DIR", "experiments")
    return resolve_experiments_root(configured)


def _resolve_experiment_dir(path_value: str) -> Path:
    return resolve_experiment_dir(_experiments_root(), path_value)


def _serialize_experiment_dir(path_value: str | Path) -> str:
    return serialize_experiment_dir(_experiments_root(), path_value)


def _load_registry_entry(stack: str, scenario: str) -> Dict[str, Any]:
    registry = _load_registry(REGISTRY_PATH)
    return _resolve_registry_entry(registry, stack=stack, scenario=scenario)


def _serialize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(task or {})
    result = payload.get("result")
    if isinstance(result, dict) and result.get("experiment_dir"):
        serialized = dict(result)
        serialized["experiment_dir"] = _serialize_experiment_dir(str(serialized.get("experiment_dir", "") or ""))
        payload["result"] = serialized
    return payload


def emit_task_update(task_id: str, event_type: str, data: Dict[str, Any]):
    try:
        socketio.emit(
            "task_update",
            {
                "task_id": task_id,
                "event_type": event_type,
                "data": data,
                "timestamp": datetime.now().isoformat(),
            },
            namespace="/tasks",
        )
    except Exception as exc:
        logger.error("Failed to emit task update: %s", exc)


def run_optimization_task(task_id: str, config: Dict[str, Any]):
    try:
        with tasks_lock:
            task_ref = tasks.get(task_id)
            if task_ref is None:
                logger.warning("Task %s not found when marking RUNNING; abort worker", task_id)
                return
            task_ref["status"] = TaskStatus.RUNNING
            task_ref["started_at"] = datetime.now().isoformat()

        stack = str(config.get("stack", "mass") or "mass")
        scenario = str(config.get("scenario", "") or "").strip()
        entry = _load_registry_entry(stack, scenario)
        scenario_path = _resolve_abs_path(entry["scenario"])
        base_config_path = _resolve_abs_path(str(config.get("base_config", "") or entry["base_config"]))

        emit_task_update(task_id, "status_change", {"status": TaskStatus.RUNNING, "message": "Scenario execution started"})
        executor_cls = _load_executor(stack)
        executor = executor_cls(
            config=load_runtime_config(base_config_path),
            run_label=str(config.get("run_label", "") or ""),
        )
        result = executor.run_scenario(scenario_path=str(scenario_path))

        result_payload = {
            "experiment_dir": str(result.run_dir),
            "summary_path": str(result.run_dir / "summary.json"),
            "scenario_id": str(result.summary.get("scenario_id", "") or scenario),
            "stack": stack,
            "status": str(result.summary.get("status", "") or "UNKNOWN"),
        }
        with tasks_lock:
            task_ref = tasks.get(task_id)
            if task_ref is not None:
                task_ref["status"] = TaskStatus.COMPLETED
                task_ref["completed_at"] = datetime.now().isoformat()
                task_ref["result"] = dict(result_payload)

        emit_task_update(
            task_id,
            "status_change",
            {
                "status": TaskStatus.COMPLETED,
                "message": "Scenario execution completed",
                "result": dict(result_payload),
            },
        )
    except Exception as exc:
        logger.error("Scenario task failed: %s", task_id, exc_info=True)
        with tasks_lock:
            task_ref = tasks.get(task_id)
            if task_ref is not None:
                task_ref["status"] = TaskStatus.FAILED
                task_ref["error"] = str(exc)
                task_ref["completed_at"] = datetime.now().isoformat()
        emit_task_update(task_id, "error", {"status": TaskStatus.FAILED, "error": str(exc)})


@socketio.on("connect", namespace="/tasks")
def handle_connect():
    logger.info("Client connected: %s", request.sid)
    emit("connected", {"message": "Connected to task updates"})


@socketio.on("disconnect", namespace="/tasks")
def handle_disconnect():
    logger.info("Client disconnected: %s", request.sid)


@socketio.on("subscribe", namespace="/tasks")
def handle_subscribe(data):
    task_id = dict(data or {}).get("task_id")
    if task_id:
        emit("subscribed", {"task_id": task_id, "message": f"Subscribed to task {task_id}"})
    else:
        emit("error", {"message": "task_id is required"})


@app.route("/api/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


@app.route("/api/tasks", methods=["POST"])
def create_task():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "Request body is required"}), 400

        stack = str(data.get("stack", "mass") or "mass")
        scenario = str(data.get("scenario", "") or "").strip()
        if stack != "mass":
            return jsonify({"error": "stack must be: mass"}), 400
        if not scenario:
            return jsonify({"error": "scenario is required"}), 400

        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "status": TaskStatus.PENDING,
            "config": dict(data),
            "created_at": datetime.now().isoformat(),
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None,
        }
        with tasks_lock:
            tasks[task_id] = task

        thread = threading.Thread(target=run_optimization_task, args=(task_id, dict(data)))
        thread.daemon = True
        thread.start()

        return jsonify({"task_id": task_id, "status": task["status"], "created_at": task["created_at"]}), 201
    except Exception as exc:
        logger.error("Failed to create task", exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/tasks/<task_id>", methods=["GET"])
def get_task(task_id: str):
    with tasks_lock:
        task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(_serialize_task(task))


@app.route("/api/tasks", methods=["GET"])
def list_tasks():
    with tasks_lock:
        task_list = [_serialize_task(item) for item in tasks.values()]
    task_list.sort(key=lambda item: item["created_at"], reverse=True)
    return jsonify({"tasks": task_list, "total": len(task_list)})


@app.route("/api/experiments/latest", methods=["GET"])
def get_latest_experiment():
    latest_payload = load_latest_index(_experiments_root())
    if not latest_payload:
        return jsonify({"error": "Latest experiment not found"}), 404
    return jsonify(latest_payload)


@app.route("/api/tasks/<task_id>/result", methods=["GET"])
def get_task_result(task_id: str):
    with tasks_lock:
        task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    if task["status"] != TaskStatus.COMPLETED:
        return jsonify({"error": "Task not completed"}), 400

    experiment_dir = str(task["result"]["experiment_dir"] or "")
    experiment_path = _resolve_experiment_dir(experiment_dir)
    summary = load_json_if_exists(experiment_path / "summary.json")
    result_index = load_json_if_exists(experiment_path / "result_index.json")

    return jsonify(
        {
            "task_id": task_id,
            "summary": summary,
            "result_index": result_index,
            "experiment_dir": _serialize_experiment_dir(experiment_path),
        }
    )


@app.route("/api/tasks/<task_id>/artifacts/<path:relative_path>", methods=["GET"])
def get_artifact(task_id: str, relative_path: str):
    with tasks_lock:
        task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    if task["status"] != TaskStatus.COMPLETED:
        return jsonify({"error": "Task not completed"}), 400

    experiment_dir = str(task["result"]["experiment_dir"] or "")
    experiment_path = _resolve_experiment_dir(experiment_dir)
    candidate = (experiment_path / relative_path).resolve()
    if not str(candidate).startswith(str(experiment_path.resolve())):
        return jsonify({"error": "Invalid artifact path"}), 400
    if not candidate.exists() or not candidate.is_file():
        return jsonify({"error": "Artifact not found"}), 404
    return send_file(candidate)


@app.route("/api/experiments", methods=["GET"])
def list_experiments():
    experiments_dir = _experiments_root()
    if not experiments_dir.exists():
        return jsonify({"experiments": [], "total": 0})

    experiments = []
    for exp_dir in iter_experiment_dirs(experiments_dir):
        summary = load_json_if_exists(exp_dir / "summary.json")
        experiments.append(
            {
                "name": exp_dir.name,
                "path": _serialize_experiment_dir(exp_dir),
                "summary": summary,
            }
        )

    experiments.sort(
        key=lambda item: (
            str(dict(item.get("summary", {}) or {}).get("run_started_at", "") or ""),
            str(item.get("path", "") or ""),
        ),
        reverse=True,
    )
    return jsonify({"experiments": experiments, "total": len(experiments)})


@app.route("/api/scenarios/validate", methods=["POST"])
def validate_scenario():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "Request body is required"}), 400
        stack = str(data.get("stack", "mass") or "mass")
        scenario = str(data.get("scenario", "") or "").strip()
        if stack != "mass":
            return jsonify({"error": "stack must be: mass"}), 400
        if not scenario:
            return jsonify({"error": "scenario is required"}), 400

        entry = _load_registry_entry(stack, scenario)
        scenario_path = _resolve_abs_path(entry["scenario"])
        spec = load_satellite_scenario_spec(scenario_path)
        return jsonify(
            {
                "valid": True,
                "stack": stack,
                "scenario": scenario,
                "scenario_path": str(scenario_path),
                "archetype_id": spec.archetype_id,
                "shell_variant": spec.shell_variant,
                "component_count": len(list(spec.catalog_component_instances or [])),
                "field_exports": list(spec.field_exports or []),
            }
        )
    except Exception as exc:
        logger.error("Scenario validation failed", exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500


def run_server(host: str = "0.0.0.0", port: int = 5000, debug: bool = False):
    logger.info("Starting scenario API server on %s:%s", host, port)
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    run_server(debug=True)
