"""
实验日志系统

提供完整的可追溯性支持，记录每次迭代的输入输出、指标变化和LLM交互。
"""

import os
import json
import logging
import math
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

from core.event_logger import EventLogger
from core.llm_interaction_store import LLMInteractionStore
from core.mode_contract import normalize_observability_mode
from core.path_policy import serialize_artifact_path, serialize_run_path
from core.modes.agent_loop.trace_store import (
    append_agent_loop_trace_row,
    init_agent_loop_trace_csv,
    materialize_metrics_payload,
)
from core.modes.mass.trace_store import (
    append_mass_trace_row,
    init_mass_trace_csv,
    materialize_trace_payload,
)

def _sanitize_json_value(value: Any) -> Any:
    """Convert non-JSON-safe numeric values (NaN/Inf) into JSON-safe nulls."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, dict):
        return {str(k): _sanitize_json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_sanitize_json_value(v) for v in value]

    # Handle numpy arrays or similar containers with .tolist()
    tolist_fn = getattr(value, "tolist", None)
    if callable(tolist_fn):
        try:
            return _sanitize_json_value(tolist_fn())
        except Exception:
            pass

    # Handle numpy scalars or similar objects with .item()
    item_fn = getattr(value, "item", None)
    if callable(item_fn):
        try:
            return _sanitize_json_value(item_fn())
        except Exception:
            return value

    return value


def _json_dump_safe(payload: Any, fp) -> None:
    json.dump(
        _sanitize_json_value(payload),
        fp,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    )


def _json_dumps_safe(payload: Any) -> str:
    return json.dumps(
        _sanitize_json_value(payload),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sanitize_run_label(raw_label: Any) -> str:
    """Normalize run label for directory/file-system safety."""
    text = str(raw_label or "").strip().lower()
    if not text:
        return ""
    text = text.replace(" ", "_")
    text = re.sub(r"[^a-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    if not text:
        return ""
    return text[:64]


def _normalize_run_algorithm(raw_algorithm: Any) -> str:
    """Normalize algorithm tag for run naming."""
    text = str(raw_algorithm or "").strip().lower()
    if not text:
        return ""

    compact = re.sub(r"[^a-z0-9]+", "", text)
    aliases = {
        "nsga2": "nsga2",
        "nsgaii": "nsga2",
        "nsga3": "nsga3",
        "nsgaiii": "nsga3",
        "moead": "moead",
    }
    mapped = aliases.get(compact, "")
    if mapped:
        return mapped

    token = _sanitize_run_label(text)
    return token[:16]


def _contains_algorithm_token(label: str, algorithm: str) -> bool:
    normalized_label = _sanitize_run_label(label)
    normalized_algorithm = _normalize_run_algorithm(algorithm)
    if not normalized_label or not normalized_algorithm:
        return False
    tokens = [item for item in normalized_label.split("_") if item]
    for token in tokens:
        if _normalize_run_algorithm(token) == normalized_algorithm:
            return True
    return False


def _build_compact_run_label(raw_label: Any, *, run_mode: str, run_algorithm: str) -> str:
    label = _sanitize_run_label(raw_label)
    algorithm = _normalize_run_algorithm(run_algorithm)
    mode_tag = "agent" if str(run_mode or "").strip().lower() == "agent_loop" else _sanitize_run_label(run_mode)

    if not label:
        label = mode_tag or "run"

    replacements = (
        ("operator_program", "op"),
        ("meta_policy", "mp"),
        ("baseline", "base"),
        ("deterministic", "det"),
        ("strict_replay", "replay"),
        ("agent_loop", "agent"),
        ("online_comsol", "ocomsol"),
        ("real_only", "real"),
        ("intermediate", "mid"),
        ("complex", "cx"),
        ("extreme", "x"),
    )
    for source, target in replacements:
        label = label.replace(source, target)

    raw_tokens = [item for item in label.split("_") if item]
    compacted_tokens: List[str] = []
    seen = set()
    for token in raw_tokens:
        if token in {"bm", "run"}:
            continue
        if re.fullmatch(r"\d{4}", token) or re.fullmatch(r"\d{6}", token):
            continue
        if token == "simple" and compacted_tokens and re.fullmatch(r"l\d+", compacted_tokens[-1]):
            continue
        if token in seen:
            continue
        seen.add(token)
        compacted_tokens.append(token)

    if not compacted_tokens:
        compacted_tokens = [mode_tag or "run"]

    compacted = "_".join(compacted_tokens)
    if algorithm and algorithm != "na" and not _contains_algorithm_token(compacted, algorithm):
        compacted = f"{compacted}_{algorithm}"
    compacted = _sanitize_run_label(compacted)

    if len(compacted) <= 40:
        return compacted

    preferred: List[str] = []
    for token in compacted.split("_"):
        if token.startswith("l") and token[1:].isdigit():
            preferred.append(token)
        elif token in {"op", "mp", "base", "agent", "mass"}:
            preferred.append(token)
        elif token.startswith("s") and token[1:].isdigit():
            preferred.append(token)
        elif _normalize_run_algorithm(token) in {"nsga2", "nsga3", "moead"}:
            preferred.append(_normalize_run_algorithm(token))
        elif token.startswith("t") and any(ch.isdigit() for ch in token):
            preferred.append(token)
        elif len(preferred) < 4:
            preferred.append(token)
    compacted = _sanitize_run_label("_".join(preferred) or compacted)
    return compacted[:40]


def _next_run_sequence(parent_dir: str, name_prefix: str) -> int:
    """Resolve next collision index for a run leaf name."""
    root = Path(str(parent_dir)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(name_prefix)}(?:_(\d{{2}}))?$")
    max_seq = 0
    try:
        for item in root.iterdir():
            if not item.is_dir():
                continue
            match = pattern.match(item.name)
            if not match:
                continue
            try:
                suffix = match.group(1)
                seq = int(suffix) if suffix is not None else 1
                max_seq = max(max_seq, seq)
            except Exception:
                continue
    except Exception:
        return 1
    return max_seq + 1


class ExperimentLogger:
    """实验日志管理器"""

    def __init__(
        self,
        base_dir: str = "experiments",
        run_mode: Optional[str] = None,
        run_label: Optional[str] = None,
        run_algorithm: Optional[str] = None,
        run_naming_strategy: Optional[str] = None,
    ):
        """
        初始化日志管理器

        Args:
            base_dir: 实验输出根目录
            run_mode: 运行模式标签（agent_loop/mass）
            run_label: 运行标签（通常来自 BOM/测试名）
            run_algorithm: 算法标签（如 NSGA-II/MOEAD）
            run_naming_strategy: 命名策略（compact/verbose）
        """
        self.base_dir = base_dir
        self.base_dir_path = Path(self.base_dir).resolve()
        resolved_mode = str(run_mode or "").strip().lower()
        if resolved_mode not in {"agent_loop", "mass"}:
            resolved_mode = "unknown"
        self.run_mode = resolved_mode
        self.run_mode_bucket = (
            normalize_observability_mode(self.run_mode, default="shared")
            if self.run_mode in {"agent_loop", "mass"}
            else "shared"
        )
        self.run_label = _sanitize_run_label(run_label)
        self.run_algorithm = _normalize_run_algorithm(run_algorithm) or "na"
        strategy = str(run_naming_strategy or "compact").strip().lower()
        if strategy not in {"compact", "verbose"}:
            strategy = "compact"
        self.run_naming_strategy = strategy
        mode_tag = "agent" if self.run_mode == "agent_loop" else self.run_mode

        started_at = datetime.now()
        self.run_started_at = started_at.isoformat()
        self.run_date = started_at.strftime("%m%d")
        self.run_time = started_at.strftime("%H%M")
        self.run_time_precise = started_at.strftime("%H%M%S")
        self.run_timestamp = f"{self.run_date}_{self.run_time_precise}"
        date_root = self.base_dir_path / self.run_date
        if self.run_naming_strategy == "verbose":
            run_prefix = f"{self.run_time_precise}_{mode_tag}"
            if self.run_label:
                run_prefix = f"{run_prefix}_{self.run_label}"
            if self.run_algorithm != "na" and not _contains_algorithm_token(run_prefix, self.run_algorithm):
                run_prefix = f"{run_prefix}_{self.run_algorithm}"
        else:
            short_tag = _build_compact_run_label(
                self.run_label,
                run_mode=self.run_mode,
                run_algorithm=self.run_algorithm,
            )
            run_prefix = f"{self.run_time}_{short_tag}"

        self.run_sequence = int(max(1, _next_run_sequence(parent_dir=str(date_root), name_prefix=run_prefix)))
        run_stem = run_prefix if self.run_sequence == 1 else f"{run_prefix}_{self.run_sequence:02d}"
        run_dir = date_root / run_stem
        while run_dir.exists():
            self.run_sequence += 1
            run_stem = f"{run_prefix}_{self.run_sequence:02d}"
            run_dir = date_root / run_stem
        self.run_dir = str(run_dir)
        self.exp_dir = self.run_dir  # 添加exp_dir别名
        self.run_id = f"run_{self.run_date}_{run_stem}"
        self.latest_index_path = str(self.base_dir_path / "_latest.json")
        os.makedirs(self.run_dir, exist_ok=True)
        self.event_logger = EventLogger(self.run_dir)
        self.event_logger.run_id = self.run_id
        self.save_run_manifest(
            {
                "run_mode": self.run_mode,
                "run_mode_bucket": self.run_mode_bucket,
                "run_label": self.run_label,
                "run_algorithm": self.run_algorithm,
                "run_naming_strategy": self.run_naming_strategy,
                "run_date": self.run_date,
                "run_time": self.run_time,
                "run_timestamp": self.run_timestamp,
                "run_started_at": self.run_started_at,
                "run_sequence": int(self.run_sequence),
            }
        )

        # 创建子文件夹
        self.llm_store = LLMInteractionStore(self.run_dir)
        self.llm_log_dir = self.llm_store.root_dir

        self.viz_dir = os.path.join(self.run_dir, "visualizations")
        os.makedirs(self.viz_dir, exist_ok=True)

        # 初始化CSV统计文件
        self.csv_path = os.path.join(self.run_dir, "evolution_trace.csv")
        self._init_csv()
        self.mass_csv_path = os.path.join(self.run_dir, "mass_trace.csv")
        self._init_mass_csv()

        # 历史记录
        self.history: List[str] = []

        # 创建Python logger
        self.logger = get_logger(f"experiment_{self.run_id}")

        # 添加文件处理器，将日志输出到实验目录的 run_log.txt
        self._add_run_log_handler(self.run_timestamp)

        print(f"Experiment logs: {self.run_dir}")

    def serialize_artifact_path(self, path_value: Any) -> str:
        return serialize_artifact_path(self.base_dir_path, path_value)

    def serialize_run_path(self, path_value: Any) -> str:
        return serialize_run_path(self.run_dir, path_value)

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
        init_agent_loop_trace_csv(self.csv_path)

    def _init_mass_csv(self):
        """初始化 mass 运行轨迹 CSV 文件头。"""
        init_mass_trace_csv(self.mass_csv_path)

    def log_llm_interaction(self, iteration: int, role: str = None, request: Dict[str, Any] = None,
                           response: Dict[str, Any] = None, context_dict: Dict[str, Any] = None,
                           response_dict: Dict[str, Any] = None, mode: Optional[str] = None):
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
            mode: 可选模式标签（agent_loop/mass）
        """
        # 兼容旧方式
        if context_dict is not None:
            request = context_dict
        if response_dict is not None:
            response = response_dict

        # 如果没有数据，跳过
        if request is None and response is None:
            return

        prefix = self.llm_store.write(
            iteration=int(iteration),
            role=role,
            request=request,
            response=response,
            mode=mode if mode is not None else self.run_mode_bucket,
        )

        if request is not None or response is not None:
            print(f"  💾 LLM interaction saved: {prefix}")

    def log_metrics(self, data: Dict[str, Any]):
        """
        记录迭代指标

        Args:
            data: 指标数据字典
        """
        row = materialize_metrics_payload(data)
        append_agent_loop_trace_row(self.csv_path, row)

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
            _json_dump_safe(design_state, f)

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
            "run_dir": self.serialize_artifact_path(self.run_dir),
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
            _json_dump_safe(payload, f)

        event_payload = {
            "iteration": int(iteration),
            "attempt": int(attempt),
            "sequence": int(sequence),
            "stage": str(stage or ""),
            "snapshot_path": self.serialize_run_path(snapshot_path),
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
            "run_dir": self.serialize_artifact_path(self.run_dir),
            "run_id": self.run_id,
            "run_mode": self.run_mode,
            "run_label": self.run_label,
            "run_algorithm": self.run_algorithm,
            "run_naming_strategy": self.run_naming_strategy,
            "run_date": self.run_date,
            "run_time": self.run_time,
            "run_timestamp": self.run_timestamp,
            "run_started_at": self.run_started_at,
            "run_sequence": int(self.run_sequence),
            "notes": notes
        }
        if extra:
            summary.update(extra)

        summary_path = os.path.join(self.run_dir, "summary.json")
        with open(summary_path, 'w', encoding='utf-8') as f:
            _json_dump_safe(summary, f)

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

    def _write_latest_index(self, manifest_payload: Dict[str, Any]) -> None:
        latest_path = Path(self.latest_index_path)
        latest_path.parent.mkdir(parents=True, exist_ok=True)

        existing: Dict[str, Any] = {}
        if latest_path.exists():
            try:
                existing = json.loads(latest_path.read_text(encoding="utf-8")) or {}
            except Exception:
                existing = {}

        existing_started_at = str(existing.get("run_started_at", "") or "").strip()
        existing_run_id = str(existing.get("run_id", "") or "").strip()
        if (
            existing_started_at
            and existing_run_id
            and existing_run_id != self.run_id
            and existing_started_at > self.run_started_at
        ):
            return

        extra = dict(manifest_payload.get("extra", {}) or {})
        latest_payload = {
            "run_id": self.run_id,
            "run_dir": self.serialize_artifact_path(self.run_dir),
            "run_leaf_dir": Path(self.run_dir).name,
            "run_date_dir": Path(self.run_dir).parent.name,
            "run_label": self.run_label,
            "run_mode": self.run_mode,
            "run_algorithm": self.run_algorithm,
            "run_naming_strategy": self.run_naming_strategy,
            "run_date": self.run_date,
            "run_time": self.run_time,
            "run_timestamp": self.run_timestamp,
            "run_started_at": self.run_started_at,
            "run_sequence": int(self.run_sequence),
            "optimization_mode": str(manifest_payload.get("optimization_mode", "") or ""),
            "pymoo_algorithm": str(manifest_payload.get("pymoo_algorithm", "") or ""),
            "thermal_evaluator_mode": str(manifest_payload.get("thermal_evaluator_mode", "") or ""),
            "search_space_mode": str(manifest_payload.get("search_space_mode", "") or ""),
            "profile": str(manifest_payload.get("profile", "") or ""),
            "level": str(manifest_payload.get("level", "") or ""),
            "seed": manifest_payload.get("seed"),
            "status": str(manifest_payload.get("status", "") or ""),
            "diagnosis_status": str(extra.get("diagnosis_status", "") or ""),
            "diagnosis_reason": str(extra.get("diagnosis_reason", "") or ""),
            "summary_path": self.serialize_artifact_path(Path(self.run_dir) / "summary.json"),
            "manifest_path": self.serialize_artifact_path(
                Path(self.run_dir) / "events" / "run_manifest.json"
            ),
            "updated_at": datetime.now().isoformat(),
        }
        with latest_path.open("w", encoding="utf-8") as f:
            _json_dump_safe(latest_payload, f)

    def save_run_manifest(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """更新事件层 run manifest。"""
        data = dict(payload or {})
        data.setdefault("run_id", self.run_id)
        data["run_dir"] = self.serialize_artifact_path(data.get("run_dir", self.run_dir))
        data.setdefault("run_mode", self.run_mode)
        data.setdefault("run_mode_bucket", self.run_mode_bucket)
        data.setdefault("run_label", self.run_label)
        data.setdefault("run_algorithm", self.run_algorithm)
        data.setdefault("run_naming_strategy", self.run_naming_strategy)
        data.setdefault("run_date", self.run_date)
        data.setdefault("run_time", self.run_time)
        data.setdefault("run_timestamp", self.run_timestamp)
        data.setdefault("run_started_at", self.run_started_at)
        data.setdefault("run_sequence", int(self.run_sequence))
        manifest = self.event_logger.write_run_manifest(data)
        self._write_latest_index(manifest)
        return manifest

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

    def log_mass_trace(self, data: Dict[str, Any]):
        """记录 mass 尝试级别轨迹。"""
        materialized = materialize_trace_payload(dict(data or {}))
        append_mass_trace_row(
            self.mass_csv_path,
            list(materialized.get("row", []) or []),
        )

        attempt_event_payload = dict(materialized.get("attempt_event_payload", {}) or {})
        candidate_event_payload = materialized.get("candidate_event_payload", None)
        is_best_attempt = bool(materialized.get("is_best_attempt", False))
        try:
            if not is_best_attempt:
                self.event_logger.append_attempt_event(attempt_event_payload)
            elif isinstance(candidate_event_payload, dict):
                self.event_logger.append_candidate_event(dict(candidate_event_payload))
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
            f.write(f"- mass trace: `mass_trace.csv`\n")
            f.write(f"- Events: `events/`\n")
            f.write(f"  - generation events: `events/generation_events.jsonl`\n")
            f.write(f"- Materialized tables: `tables/`\n")
            f.write(f"- LLM interactions: `llm_interactions/`\n")
            active_buckets = self.llm_store.get_active_buckets()
            if active_buckets:
                f.write(
                    "- mode buckets: "
                    + ", ".join([f"`{name}/`" for name in active_buckets])
                    + "\n"
                )
            else:
                f.write("- mode buckets: (none)\n")
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
                _json_dump_safe(context_pack, f)

        # 保存 StrategicPlan
        if strategic_plan is not None:
            plan_path = os.path.join(trace_dir, f"{prefix}_plan.json")
            with open(plan_path, 'w', encoding='utf-8') as f:
                _json_dump_safe(strategic_plan, f)

        # 保存 EvalResult
        if eval_result is not None:
            eval_path = os.path.join(trace_dir, f"{prefix}_eval.json")
            with open(eval_path, 'w', encoding='utf-8') as f:
                _json_dump_safe(eval_result, f)

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
            f.write(_json_dumps_safe(event) + "\n")
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
            f.write(_json_dumps_safe(event) + '\n')

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
