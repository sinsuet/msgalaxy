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

        print(f"📁 Experiment logs: {self.run_dir}")

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
            "llm_tokens"
        ]
        with open(self.csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)

    def log_llm_interaction(self, iteration: int, context_dict: Dict[str, Any], response_dict: Dict[str, Any]):
        """
        记录LLM交互

        Args:
            iteration: 迭代次数
            context_dict: 输入上下文（ContextPack）
            response_dict: LLM响应（OptimizationPlan）
        """
        # 保存输入
        req_path = os.path.join(self.llm_log_dir, f"iter_{iteration:02d}_req.json")
        with open(req_path, 'w', encoding='utf-8') as f:
            json.dump(context_dict, f, indent=2, ensure_ascii=False)

        # 保存输出
        resp_path = os.path.join(self.llm_log_dir, f"iter_{iteration:02d}_resp.json")
        with open(resp_path, 'w', encoding='utf-8') as f:
            json.dump(response_dict, f, indent=2, ensure_ascii=False)

        print(f"  💾 LLM interaction saved: iter_{iteration:02d}")

    def log_metrics(self, data: Dict[str, Any]):
        """
        记录迭代指标

        Args:
            data: 指标数据字典
        """
        row = [
            data.get("iteration", 0),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            f"{data.get('max_temp', 0):.2f}",
            f"{data.get('min_clearance', 0):.2f}",
            f"{data.get('total_mass', 0):.2f}",
            f"{data.get('total_power', 0):.2f}",
            data.get("num_violations", 0),
            data.get("is_safe", False),
            f"{data.get('solver_cost', 0):.4f}",
            data.get("llm_tokens", 0)
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
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger
