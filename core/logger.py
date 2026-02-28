"""
实验日志系统

提供完整的可追溯性支持，记录每次迭代的输入输出、指标变化和LLM交互。
"""

import os
import json
import csv
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path


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
        os.makedirs(self.run_dir, exist_ok=True)

        # 创建子文件夹
        self.llm_log_dir = os.path.join(self.run_dir, "llm_interactions")
        os.makedirs(self.llm_log_dir, exist_ok=True)

        self.viz_dir = os.path.join(self.run_dir, "visualizations")
        os.makedirs(self.viz_dir, exist_ok=True)

        # 初始化CSV统计文件
        self.csv_path = os.path.join(self.run_dir, "evolution_trace.csv")
        self._init_csv()

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
        import logging

        # 创建 run_log.txt 文件路径
        run_log_path = os.path.join(self.run_dir, "run_log.txt")

        # 创建文件处理器
        file_handler = logging.FileHandler(run_log_path, encoding='utf-8')
        file_handler.setLevel(logging.INFO)

        # 设置格式器
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)

        # 只添加到根 logger，这样可以捕获所有模块的日志
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)

        # 确保根 logger 的级别不会过滤掉 INFO 级别的日志
        if root_logger.level > logging.INFO:
            root_logger.setLevel(logging.INFO)

        self.logger.info(f"Run log initialized: {run_log_path}")

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

    def save_summary(self, status: str, final_iteration: int, notes: str = ""):
        """
        保存总结报告

        Args:
            status: 状态（SUCCESS, TIMEOUT, ERROR）
            final_iteration: 最终迭代次数
            notes: 备注信息
        """
        summary = {
            "status": status,
            "final_iteration": final_iteration,
            "timestamp": datetime.now().isoformat(),
            "run_dir": self.run_dir,
            "notes": notes
        }

        summary_path = os.path.join(self.run_dir, "summary.json")
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        # 生成Markdown报告
        self._generate_markdown_report(summary)

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
