"""
实验日志系统

提供完整的可追溯性支持，记录每次迭代的输入输出、指标变化和LLM交互。
"""

import os
import json
import csv
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

from core.event_logger import EventLogger


def _safe_float(value: Any, digits: int = 6) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return ""


class ExperimentLogger:
    """实验日志管理器"""

    def __init__(self, base_dir: str = "experiments"):
        """
        初始化日志管理器

        Args:
            base_dir: 实验输出根目录
        """
        self.base_dir = base_dir

        # 创建带时间戳的实验文件夹
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = os.path.join(base_dir, f"run_{timestamp}")
        self.exp_dir = self.run_dir  # 添加exp_dir别名
        self.run_id = Path(self.run_dir).name
        os.makedirs(self.run_dir, exist_ok=True)
        self.event_logger = EventLogger(self.run_dir)

        # 创建子文件夹
        self.llm_log_dir = os.path.join(self.run_dir, "llm_interactions")
        os.makedirs(self.llm_log_dir, exist_ok=True)

        self.viz_dir = os.path.join(self.run_dir, "visualizations")
        os.makedirs(self.viz_dir, exist_ok=True)

        # 初始化CSV统计文件
        self.csv_path = os.path.join(self.run_dir, "evolution_trace.csv")
        self._init_csv()
        self.pymoo_maas_csv_path = os.path.join(self.run_dir, "pymoo_maas_trace.csv")
        self._init_pymoo_maas_csv()

        # 历史记录
        self.history: List[str] = []

        # 创建Python logger
        self.logger = get_logger(f"experiment_{timestamp}")

        # 添加文件处理器，将日志输出到实验目录的 run_log.txt
        self._add_run_log_handler(timestamp)

        print(f"Experiment logs: {self.run_dir}")

    def _add_run_log_handler(self, timestamp: str):
        """
        添加文件处理器，将日志输出到实验目录的 run_log.txt

        Args:
            timestamp: 时间戳字符串
        """
        # 创建 run_log.txt 文件路径
        run_log_path = os.path.join(self.run_dir, "run_log.txt")
        run_debug_path = os.path.join(self.run_dir, "run_log_debug.txt")

        class _RunLogCompactFilter(logging.Filter):
            """
            精简 run_log.txt 的高重复低信息密度日志。

            设计原则：
            - WARNING/ERROR 一律保留；
            - 高频重复的结构指标明细转移到 debug 日志；
            - 关键流程碑（COMSOL 调用、预算耗尽、审计结论）保留。
            """

            _structural_prefixes = (
                "质心:",
                "几何中心:",
                "质心偏移量:",
                "转动惯量:",
            )

            def filter(self, record: logging.LogRecord) -> bool:
                if record.levelno >= logging.WARNING:
                    return True

                if record.name == "simulation.structural_physics":
                    message = str(record.getMessage() or "")
                    if message.startswith(self._structural_prefixes):
                        return False

                return True

        # 设置格式器
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        root_logger = logging.getLogger()

        # 避免同进程多次初始化时重复挂载本系统 handler 导致日志倍增
        stale_handlers = [
            h for h in list(root_logger.handlers)
            if bool(getattr(h, "_msgalaxy_run_handler", False))
        ]
        for handler in stale_handlers:
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        # run_log.txt: 精简版，便于快速诊断阅读
        compact_handler = logging.FileHandler(run_log_path, encoding='utf-8')
        compact_handler.setLevel(logging.INFO)
        compact_handler.setFormatter(formatter)
        compact_handler.addFilter(_RunLogCompactFilter())
        compact_handler._msgalaxy_run_handler = True  # type: ignore[attr-defined]
        root_logger.addHandler(compact_handler)

        # run_log_debug.txt: 完整版，保留全部 INFO 细节用于深挖
        debug_handler = logging.FileHandler(run_debug_path, encoding='utf-8')
        debug_handler.setLevel(logging.INFO)
        debug_handler.setFormatter(formatter)
        debug_handler._msgalaxy_run_handler = True  # type: ignore[attr-defined]
        root_logger.addHandler(debug_handler)

        # 确保根 logger 的级别不会过滤掉 INFO 级别日志
        if root_logger.level > logging.INFO:
            root_logger.setLevel(logging.INFO)

        self.logger.info(
            "Run log initialized: %s (compact) | %s (full)",
            run_log_path,
            run_debug_path,
        )

    def _init_csv(self):
        """初始化CSV文件头"""
        headers = [
            "iteration",
            "timestamp",
            "max_temp",
            "min_clearance",
            "total_mass",
            "total_power",
            "num_violations",
            "is_safe",
            "solver_cost",
            "llm_tokens",
            "penalty_score",  # Phase 4: 惩罚分
            "state_id",       # Phase 4: 状态ID
            # 高信息密度字段（用于分析迭代有效性）
            "avg_temp",
            "min_temp",
            "temp_gradient",
            "cg_offset",
            "num_collisions",
            "penalty_violation",
            "penalty_temp",
            "penalty_clearance",
            "penalty_cg",
            "penalty_collision",
            "delta_penalty",
            "delta_cg_offset",
            "delta_max_temp",
            "delta_min_clearance",
            "effectiveness_score",
        ]
        with open(self.csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)

    def _init_pymoo_maas_csv(self):
        """初始化 pymoo_maas 运行轨迹 CSV 文件头。"""
        headers = [
            "iteration",
            "attempt",
            "timestamp",
            "branch_action",
            "branch_source",
            "operator_program_id",
            "operator_actions",
            "operator_bias_strategy",
            "intent_id",
            "thermal_evaluator_mode",
            "diagnosis_status",
            "diagnosis_reason",
            "solver_message",
            "solver_cost",
            "score",
            "best_cv",
            "aocc_cv",
            "aocc_objective",
            "has_candidate_state",
            "relaxation_applied_count",
            "physics_audit_selected_reason",
            "mcts_enabled",
            "is_best_attempt",
            "dominant_violation",
            "dominant_violation_value",
            "best_candidate_cg_offset",
            "best_candidate_max_temp",
            "best_candidate_min_clearance",
        ]
        with open(self.pymoo_maas_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)

    def log_llm_interaction(self, iteration: int, role: str = None, request: Dict[str, Any] = None,
                           response: Dict[str, Any] = None, context_dict: Dict[str, Any] = None,
                           response_dict: Dict[str, Any] = None):
        """
        记录LLM交互

        支持两种调用方式：
        1. 新方式: log_llm_interaction(iteration, role, request, response)
        2. 旧方式: log_llm_interaction(iteration, context_dict, response_dict)

        Args:
            iteration: 迭代次数
            role: 角色名称（meta_reasoner, thermal_agent等）
            request: 请求数据
            response: 响应数据
            context_dict: 输入上下文（旧方式）
            response_dict: LLM响应（旧方式）
        """
        # 兼容旧方式
        if context_dict is not None:
            request = context_dict
        if response_dict is not None:
            response = response_dict

        # 如果没有数据，跳过
        if request is None and response is None:
            return

        # 确定文件名前缀
        prefix = f"iter_{iteration:02d}"
        if role:
            prefix = f"iter_{iteration:02d}_{role}"

        # 保存请求
        if request is not None:
            req_path = os.path.join(self.llm_log_dir, f"{prefix}_req.json")
            with open(req_path, 'w', encoding='utf-8') as f:
                json.dump(request, f, indent=2, ensure_ascii=False)

        # 保存响应
        if response is not None:
            resp_path = os.path.join(self.llm_log_dir, f"{prefix}_resp.json")
            with open(resp_path, 'w', encoding='utf-8') as f:
                json.dump(response, f, indent=2, ensure_ascii=False)

        if request is not None or response is not None:
            print(f"  💾 LLM interaction saved: {prefix}")

    def log_metrics(self, data: Dict[str, Any]):
        """
        记录迭代指标

        Args:
            data: 指标数据字典
        """
        def _fmt_float(value: Any, digits: int = 2) -> str:
            try:
                return f"{float(value):.{digits}f}"
            except (TypeError, ValueError):
                return ""

        row = [
            data.get("iteration", 0),
            data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            _fmt_float(data.get('max_temp', 0), 2),
            _fmt_float(data.get('min_clearance', 0), 2),
            _fmt_float(data.get('total_mass', 0), 2),
            _fmt_float(data.get('total_power', 0), 2),
            data.get("num_violations", 0),
            data.get("is_safe", False),
            _fmt_float(data.get('solver_cost', 0), 4),
            data.get("llm_tokens", 0),
            _fmt_float(data.get('penalty_score', 0), 2),  # Phase 4
            data.get("state_id", ""),                    # Phase 4
            _fmt_float(data.get('avg_temp', 0), 2),
            _fmt_float(data.get('min_temp', 0), 2),
            _fmt_float(data.get('temp_gradient', 0), 2),
            _fmt_float(data.get('cg_offset', 0), 2),
            int(data.get('num_collisions', 0)),
            _fmt_float(data.get('penalty_violation', 0), 2),
            _fmt_float(data.get('penalty_temp', 0), 2),
            _fmt_float(data.get('penalty_clearance', 0), 2),
            _fmt_float(data.get('penalty_cg', 0), 2),
            _fmt_float(data.get('penalty_collision', 0), 2),
            _fmt_float(data.get('delta_penalty', 0), 2),
            _fmt_float(data.get('delta_cg_offset', 0), 2),
            _fmt_float(data.get('delta_max_temp', 0), 2),
            _fmt_float(data.get('delta_min_clearance', 0), 2),
            _fmt_float(data.get('effectiveness_score', 0), 2),
        ]

        with open(self.csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(row)

    def add_history(self, message: str):
        """
        添加历史记录

        Args:
            message: 历史消息
        """
        self.history.append(message)

    def get_recent_history(self, n: int = 3) -> List[str]:
        """
        获取最近的历史记录

        Args:
            n: 返回最近n条记录

        Returns:
            历史记录列表
        """
        return self.history[-n:] if len(self.history) >= n else self.history

    def save_design_state(self, iteration: int, design_state: Dict[str, Any]):
        """
        保存设计状态

        Args:
            iteration: 迭代次数
            design_state: 设计状态字典
        """
        state_path = os.path.join(self.run_dir, f"design_state_iter_{iteration:02d}.json")
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump(design_state, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _component_diff_lists(
        previous_state: Optional[Dict[str, Any]],
        current_state: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        """计算组件级变化摘要，用于布局时间轴可视化标注。"""
        prev_components = {
            str(item.get("id", "")): item
            for item in list((previous_state or {}).get("components", []) or [])
            if isinstance(item, dict) and str(item.get("id", ""))
        }
        curr_components = {
            str(item.get("id", "")): item
            for item in list((current_state or {}).get("components", []) or [])
            if isinstance(item, dict) and str(item.get("id", ""))
        }

        moved_components: List[str] = []
        added_heatsinks: List[str] = []
        added_brackets: List[str] = []
        changed_contacts: List[str] = []
        changed_coatings: List[str] = []

        for comp_id, curr in curr_components.items():
            prev = prev_components.get(comp_id, {})
            curr_pos = dict(curr.get("position", {}) or {})
            prev_pos = dict(prev.get("position", {}) or {})
            try:
                dx = float(curr_pos.get("x", 0.0)) - float(prev_pos.get("x", 0.0))
                dy = float(curr_pos.get("y", 0.0)) - float(prev_pos.get("y", 0.0))
                dz = float(curr_pos.get("z", 0.0)) - float(prev_pos.get("z", 0.0))
                dist = (dx * dx + dy * dy + dz * dz) ** 0.5
                if dist > 1e-6:
                    moved_components.append(comp_id)
            except Exception:
                pass

            prev_heatsink = prev.get("heatsink")
            curr_heatsink = curr.get("heatsink")
            if curr_heatsink and curr_heatsink != prev_heatsink:
                added_heatsinks.append(comp_id)

            prev_bracket = prev.get("bracket")
            curr_bracket = curr.get("bracket")
            if curr_bracket and curr_bracket != prev_bracket:
                added_brackets.append(comp_id)

            prev_contacts = dict(prev.get("thermal_contacts", {}) or {})
            curr_contacts = dict(curr.get("thermal_contacts", {}) or {})
            if curr_contacts != prev_contacts:
                changed_contacts.append(comp_id)

            prev_coating = (
                prev.get("coating_type", "default"),
                float(prev.get("emissivity", 0.8) or 0.8),
                float(prev.get("absorptivity", 0.3) or 0.3),
            )
            curr_coating = (
                curr.get("coating_type", "default"),
                float(curr.get("emissivity", 0.8) or 0.8),
                float(curr.get("absorptivity", 0.3) or 0.3),
            )
            if curr_coating != prev_coating:
                changed_coatings.append(comp_id)

        return {
            "moved_components": sorted(moved_components),
            "added_heatsinks": sorted(added_heatsinks),
            "added_brackets": sorted(added_brackets),
            "changed_contacts": sorted(changed_contacts),
            "changed_coatings": sorted(changed_coatings),
        }

    def save_layout_snapshot(
        self,
        *,
        iteration: int,
        attempt: int,
        sequence: int,
        stage: str,
        design_state: Any,
        thermal_source: str = "",
        metrics: Optional[Dict[str, Any]] = None,
        branch_action: str = "",
        branch_source: str = "",
        diagnosis_status: str = "",
        diagnosis_reason: str = "",
        operator_program_id: str = "",
        operator_actions: Optional[List[str]] = None,
        previous_design_state: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        保存布局快照并写入 layout 事件。

        每个快照包含完整设计状态 + 差分摘要，供时间轴可视化使用。
        """
        snapshots_dir = os.path.join(self.run_dir, "snapshots")
        os.makedirs(snapshots_dir, exist_ok=True)

        stage_token = "".join(
            ch if ch.isalnum() or ch in {"_", "-"} else "_"
            for ch in str(stage or "snapshot")
        ).strip("_") or "snapshot"
        snapshot_name = (
            f"seq_{int(sequence):04d}_iter_{int(iteration):02d}_"
            f"attempt_{int(attempt):02d}_{stage_token}.json"
        )
        snapshot_path = os.path.join(snapshots_dir, snapshot_name)

        if hasattr(design_state, "model_dump"):
            state_payload = design_state.model_dump()
        elif isinstance(design_state, dict):
            state_payload = dict(design_state)
        else:
            state_payload = {}

        prev_payload: Optional[Dict[str, Any]] = None
        if previous_design_state is not None:
            if hasattr(previous_design_state, "model_dump"):
                prev_payload = previous_design_state.model_dump()
            elif isinstance(previous_design_state, dict):
                prev_payload = dict(previous_design_state)
            else:
                prev_payload = None

        delta = self._component_diff_lists(prev_payload, state_payload)
        payload = {
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "timestamp": datetime.now().isoformat(),
            "sequence": int(sequence),
            "iteration": int(iteration),
            "attempt": int(attempt),
            "stage": str(stage or ""),
            "thermal_source": str(thermal_source or ""),
            "branch_action": str(branch_action or ""),
            "branch_source": str(branch_source or ""),
            "diagnosis_status": str(diagnosis_status or ""),
            "diagnosis_reason": str(diagnosis_reason or ""),
            "operator_program_id": str(operator_program_id or ""),
            "operator_actions": list(operator_actions or []),
            "metrics": dict(metrics or {}),
            "delta": dict(delta),
            "metadata": dict(metadata or {}),
            "design_state": state_payload,
        }

        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        event_payload = {
            "iteration": int(iteration),
            "attempt": int(attempt),
            "sequence": int(sequence),
            "stage": str(stage or ""),
            "snapshot_path": snapshot_path,
            "thermal_source": str(thermal_source or ""),
            "diagnosis_status": str(diagnosis_status or ""),
            "diagnosis_reason": str(diagnosis_reason or ""),
            "branch_action": str(branch_action or ""),
            "branch_source": str(branch_source or ""),
            "operator_program_id": str(operator_program_id or ""),
            "operator_actions": list(operator_actions or []),
            "moved_components": list(delta.get("moved_components", [])),
            "added_heatsinks": list(delta.get("added_heatsinks", [])),
            "added_brackets": list(delta.get("added_brackets", [])),
            "changed_contacts": list(delta.get("changed_contacts", [])),
            "changed_coatings": list(delta.get("changed_coatings", [])),
            "metrics": dict(metrics or {}),
            "metadata": dict(metadata or {}),
        }
        self.event_logger.append_layout_event(event_payload)
        return {"snapshot_path": snapshot_path, "event": event_payload, "delta": delta}

    def save_visualization(self, iteration: int, fig_name: str, fig):
        """
        保存可视化图表

        Args:
            iteration: 迭代次数
            fig_name: 图表名称
            fig: matplotlib figure对象
        """
        viz_path = os.path.join(self.viz_dir, f"iter_{iteration:02d}_{fig_name}.png")
        fig.savefig(viz_path, dpi=150, bbox_inches='tight')
        print(f"  📊 Visualization saved: {fig_name}")

    def save_summary(
        self,
        status: str,
        final_iteration: int,
        notes: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ):
        """
        保存总结报告

        Args:
            status: 状态（SUCCESS, TIMEOUT, ERROR）
            final_iteration: 最终迭代次数
            notes: 备注信息
            extra: 额外写入 summary.json 的键值
        """
        summary = {
            "status": status,
            "final_iteration": final_iteration,
            "timestamp": datetime.now().isoformat(),
            "run_dir": self.run_dir,
            "notes": notes
        }
        if extra:
            summary.update(extra)

        summary_path = os.path.join(self.run_dir, "summary.json")
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        # 生成Markdown报告
        self._generate_markdown_report(summary)

        # 同步更新事件层 run manifest
        self.save_run_manifest(
            {
                "status": str(status),
                "final_iteration": int(final_iteration),
                "extra": dict(extra or {}),
            }
        )

    def save_run_manifest(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """更新事件层 run manifest。"""
        data = dict(payload or {})
        data.setdefault("run_id", self.run_id)
        data.setdefault("run_dir", self.run_dir)
        return self.event_logger.write_run_manifest(data)

    def log_maas_phase_event(self, data: Dict[str, Any]) -> None:
        """写入 A/B/C/D 阶段事件。"""
        try:
            self.event_logger.append_phase_event(dict(data or {}))
        except Exception as exc:
            self.logger.debug("maas phase event write failed: %s", exc)

    def log_maas_policy_event(self, data: Dict[str, Any]) -> None:
        """写入策略调参事件。"""
        try:
            self.event_logger.append_policy_event(dict(data or {}))
        except Exception as exc:
            self.logger.debug("maas policy event write failed: %s", exc)

    def log_maas_generation_events(self, data: Dict[str, Any]) -> None:
        """写入代际收敛事件列表。"""
        payload = dict(data or {})
        records = list(payload.get("records", []) or [])
        if not records:
            return

        iteration = int(payload.get("iteration", 0) or 0)
        attempt = int(payload.get("attempt", 0) or 0)
        branch_action = str(payload.get("branch_action", ""))
        branch_source = str(payload.get("branch_source", ""))
        search_space_mode = str(payload.get("search_space_mode", ""))
        pymoo_algorithm = str(payload.get("pymoo_algorithm", ""))

        for item in records:
            try:
                record = dict(item or {})
                self.event_logger.append_generation_event(
                    {
                        "iteration": iteration,
                        "attempt": attempt,
                        "generation": int(record.get("generation", 0) or 0),
                        "pymoo_algorithm": pymoo_algorithm,
                        "branch_action": branch_action,
                        "branch_source": branch_source,
                        "search_space_mode": search_space_mode,
                        "population_size": int(record.get("population_size", 0) or 0),
                        "feasible_count": int(record.get("feasible_count", 0) or 0),
                        "feasible_ratio": record.get("feasible_ratio"),
                        "best_cv": record.get("best_cv"),
                        "mean_cv": record.get("mean_cv"),
                        "best_feasible_sum_f": record.get("best_feasible_sum_f"),
                    }
                )
            except Exception as exc:
                self.logger.debug("maas generation event write failed: %s", exc)

    def log_maas_physics_event(self, data: Dict[str, Any]) -> None:
        """写入物理调度/审计事件。"""
        try:
            self.event_logger.append_physics_event(dict(data or {}))
        except Exception as exc:
            self.logger.debug("maas physics event write failed: %s", exc)

    def log_pymoo_maas_trace(self, data: Dict[str, Any]):
        """记录 pymoo_maas 尝试级别轨迹。"""
        diagnosis = data.get("diagnosis") or {}
        dominant_violation = str(data.get("dominant_violation", "") or "")
        violation_breakdown = dict(data.get("constraint_violation_breakdown") or {})
        best_candidate_metrics = dict(data.get("best_candidate_metrics") or {})
        operator_actions = list(data.get("operator_actions", []) or [])
        operator_bias = dict(data.get("operator_bias", {}) or {})
        row = [
            int(data.get("iteration", 0)),
            int(data.get("attempt", 0)),
            data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            str(data.get("branch_action", "")),
            str(data.get("branch_source", "")),
            str(data.get("operator_program_id", "")),
            ",".join(str(item) for item in operator_actions),
            str(operator_bias.get("strategy", "")),
            str(data.get("intent_id", "")),
            str(data.get("thermal_evaluator_mode", "")),
            str(diagnosis.get("status", data.get("diagnosis_status", ""))),
            str(diagnosis.get("reason", data.get("diagnosis_reason", ""))),
            str(data.get("solver_message", "")),
            _safe_float(data.get("solver_cost"), digits=6),
            _safe_float(data.get("score"), digits=6),
            _safe_float(data.get("best_cv"), digits=6),
            _safe_float(data.get("aocc_cv"), digits=6),
            _safe_float(data.get("aocc_objective"), digits=6),
            bool(data.get("has_candidate_state", False)),
            int(data.get("relaxation_applied_count", 0)),
            str(data.get("physics_audit_selected_reason", "")),
            bool(data.get("mcts_enabled", False)),
            bool(data.get("is_best_attempt", False)),
            dominant_violation,
            _safe_float(violation_breakdown.get(dominant_violation), digits=6),
            _safe_float(best_candidate_metrics.get("cg_offset"), digits=6),
            _safe_float(best_candidate_metrics.get("max_temp"), digits=6),
            _safe_float(best_candidate_metrics.get("min_clearance"), digits=6),
        ]
        with open(self.pymoo_maas_csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)

        # Phase-1 双写：CSV 继续保留，同时写入结构化事件层。
        iteration = int(data.get("iteration", 0) or 0)
        attempt = int(data.get("attempt", 0) or 0)
        is_best_attempt = bool(data.get("is_best_attempt", False))
        attempt_event_payload = {
            "iteration": iteration,
            "attempt": attempt,
            "branch_action": str(data.get("branch_action", "")),
            "branch_source": str(data.get("branch_source", "")),
            "search_space_mode": str(data.get("search_space_mode", "")),
            "pymoo_algorithm": str(data.get("pymoo_algorithm", "")),
            "thermal_evaluator_mode": str(data.get("thermal_evaluator_mode", "")),
            "diagnosis_status": str(diagnosis.get("status", data.get("diagnosis_status", ""))),
            "diagnosis_reason": str(diagnosis.get("reason", data.get("diagnosis_reason", ""))),
            "solver_message": str(data.get("solver_message", "")),
            "solver_cost": _safe_float(data.get("solver_cost"), digits=6) or None,
            "score": _safe_float(data.get("score"), digits=6) or None,
            "best_cv": _safe_float(data.get("best_cv"), digits=6) or None,
            "aocc_cv": _safe_float(data.get("aocc_cv"), digits=6) or None,
            "aocc_objective": _safe_float(data.get("aocc_objective"), digits=6) or None,
            "dominant_violation": dominant_violation,
            "constraint_violation_breakdown": violation_breakdown,
            "best_candidate_metrics": best_candidate_metrics,
            "operator_program_id": str(data.get("operator_program_id", "")),
            "operator_actions": operator_actions,
            "operator_bias_strategy": str(operator_bias.get("strategy", "")),
            "mcts_enabled": bool(data.get("mcts_enabled", False)),
            "has_candidate_state": bool(data.get("has_candidate_state", False)),
            "is_best_attempt": is_best_attempt,
        }

        # Convert numeric strings produced by _safe_float back to float for typed events.
        for field in ("solver_cost", "score", "best_cv", "aocc_cv", "aocc_objective"):
            value = attempt_event_payload.get(field)
            if isinstance(value, str) and value.strip() != "":
                try:
                    attempt_event_payload[field] = float(value)
                except Exception:
                    attempt_event_payload[field] = None

        try:
            if not is_best_attempt:
                self.event_logger.append_attempt_event(attempt_event_payload)
            else:
                self.event_logger.append_candidate_event(
                    {
                        "iteration": iteration,
                        "attempt": attempt,
                        "source": "best_attempt_marker",
                        "diagnosis_status": attempt_event_payload.get("diagnosis_status", ""),
                        "diagnosis_reason": attempt_event_payload.get("diagnosis_reason", ""),
                        "best_cv": attempt_event_payload.get("best_cv", None),
                        "dominant_violation": dominant_violation,
                        "best_candidate_metrics": best_candidate_metrics,
                        "physics_audit_selected_reason": str(
                            data.get("physics_audit_selected_reason", "")
                        ),
                        "is_selected": True,
                    }
                )
        except Exception as exc:
            self.logger.debug("maas attempt/candidate event write failed: %s", exc)

    def _generate_markdown_report(self, summary: Dict[str, Any]):
        """生成Markdown格式的报告"""
        report_path = os.path.join(self.run_dir, "report.md")

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"# Satellite Design Optimization Report\n\n")
            f.write(f"**Status**: {summary['status']}\n\n")
            f.write(f"**Final Iteration**: {summary['final_iteration']}\n\n")
            f.write(f"**Timestamp**: {summary['timestamp']}\n\n")

            if summary.get('notes'):
                f.write(f"## Notes\n\n{summary['notes']}\n\n")

            f.write(f"## Files\n\n")
            f.write(f"- Evolution trace: `evolution_trace.csv`\n")
            f.write(f"- pymoo_maas trace: `pymoo_maas_trace.csv`\n")
            f.write(f"- Events: `events/`\n")
            f.write(f"  - generation events: `events/generation_events.jsonl`\n")
            f.write(f"- Materialized tables: `tables/`\n")
            f.write(f"- LLM interactions: `llm_interactions/`\n")
            f.write(f"- Visualizations: `visualizations/`\n")

        print(f"  📝 Report generated: report.md")

    # ============ Phase 4: Trace 审计日志 ============

    def save_trace_data(
        self,
        iteration: int,
        context_pack: Optional[Dict[str, Any]] = None,
        strategic_plan: Optional[Dict[str, Any]] = None,
        eval_result: Optional[Dict[str, Any]] = None
    ):
        """
        保存完整的 Trace 审计数据（Phase 4）

        Args:
            iteration: 迭代次数
            context_pack: 输入给 LLM 的上下文包
            strategic_plan: LLM 的战略计划输出
            eval_result: 物理仿真的评估结果
        """
        # 创建 trace 子目录
        trace_dir = os.path.join(self.run_dir, "trace")
        os.makedirs(trace_dir, exist_ok=True)

        prefix = f"iter_{iteration:02d}"

        # 保存 ContextPack
        if context_pack is not None:
            context_path = os.path.join(trace_dir, f"{prefix}_context.json")
            with open(context_path, 'w', encoding='utf-8') as f:
                json.dump(context_pack, f, indent=2, ensure_ascii=False)

        # 保存 StrategicPlan
        if strategic_plan is not None:
            plan_path = os.path.join(trace_dir, f"{prefix}_plan.json")
            with open(plan_path, 'w', encoding='utf-8') as f:
                json.dump(strategic_plan, f, indent=2, ensure_ascii=False)

        # 保存 EvalResult
        if eval_result is not None:
            eval_path = os.path.join(trace_dir, f"{prefix}_eval.json")
            with open(eval_path, 'w', encoding='utf-8') as f:
                json.dump(eval_result, f, indent=2, ensure_ascii=False)

        self.logger.info(f"  💾 Trace data saved: {prefix}")

    def save_maas_diagnostic_event(
        self,
        iteration: int,
        attempt: int,
        payload: Dict[str, Any],
    ) -> None:
        """
        记录 MaaS 闭环每次求解尝试的诊断事件（JSONL）。

        Args:
            iteration: 外层优化迭代编号
            attempt: MaaS 内部第几次建模/求解尝试（从1开始）
            payload: 任意可序列化诊断信息
        """
        log_path = os.path.join(self.run_dir, "maas_diagnostics.jsonl")
        event = {
            "iteration": int(iteration),
            "attempt": int(attempt),
            "timestamp": datetime.now().isoformat(),
            "payload": payload,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.logger.info(f"  💾 MaaS diagnostics saved: iter={iteration}, attempt={attempt}")

    def save_rollback_event(
        self,
        iteration: int,
        rollback_reason: str,
        from_state_id: str,
        to_state_id: str,
        penalty_before: float,
        penalty_after: float
    ):
        """
        记录回退事件（Phase 4）

        Args:
            iteration: 触发回退的迭代次数
            rollback_reason: 回退原因
            from_state_id: 回退前的状态ID
            to_state_id: 回退后的状态ID
            penalty_before: 回退前的惩罚分
            penalty_after: 回退后的惩罚分
        """
        rollback_log_path = os.path.join(self.run_dir, "rollback_events.jsonl")

        event = {
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(),
            "reason": rollback_reason,
            "from_state": from_state_id,
            "to_state": to_state_id,
            "penalty_before": penalty_before,
            "penalty_after": penalty_after
        }

        # 追加到 JSONL 文件
        with open(rollback_log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')

        self.logger.warning(f"  ⚠️ Rollback event logged: {from_state_id} → {to_state_id}")


def get_logger(name: str) -> Any:
    """
    获取Python标准日志记录器

    Args:
        name: 日志记录器名称

    Returns:
        logging.Logger对象
    """
    import logging

    logger = logging.getLogger(name)

    if not logger.handlers:
        # 控制台处理器 - 设置UTF-8编码
        import sys
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.stream.reconfigure(encoding='utf-8') if hasattr(console_handler.stream, 'reconfigure') else None
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.INFO)

        # 文件处理器
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        file_handler = logging.FileHandler(
            log_dir / f"{name}.log",
            encoding='utf-8'
        )
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
        logger.setLevel(logging.DEBUG)

    return logger


def log_exception(logger, exception: Exception, context: str = ""):
    """
    记录异常详情

    Args:
        logger: 日志记录器
        exception: 异常对象
        context: 上下文信息
    """
    import traceback

    error_msg = f"Exception in {context}: {type(exception).__name__}: {str(exception)}"
    logger.error(error_msg)
    logger.debug(traceback.format_exc())
