"""
Command Line Interface

提供命令行接口用于：
1. 运行优化
2. 查看实验结果
3. 管理知识库
"""

import argparse
import json
import sys
import io
from pathlib import Path

# 修复Windows控制台编码问题
if sys.platform == 'win32':
    try:
        # Python 3.7+ 方法
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        # Python 3.6 及更早版本
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from api.experiment_index import (
    find_experiment_dir,
    iter_experiment_dirs,
    load_json_if_exists,
    load_latest_index,
    resolve_experiments_root,
    serialize_experiment_dir,
)
from core.artifact_index import load_artifact_index
from workflow.orchestrator import WorkflowOrchestrator


def _discover_trace_artifacts(exp_path: Path) -> list[tuple[str, Path]]:
    index = load_artifact_index(str(exp_path))
    paths = dict(index.get("paths", {}) or {})
    discovered: list[tuple[str, Path]] = []
    for label, key, fallback in (
        ("Evolution trace", "agent_loop_trace_csv", "evolution_trace.csv"),
        ("Mass trace", "mass_trace_csv", "mass_trace.csv"),
    ):
        raw = str(paths.get(key, "") or fallback)
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = exp_path / candidate
        if candidate.exists():
            discovered.append((label, candidate))
    return discovered


def cmd_optimize(args):
    """运行优化命令"""
    print("="*60)
    print("Satellite Design Optimization System")
    print("="*60)

    try:
        # 初始化编排器
        orchestrator = WorkflowOrchestrator(config_path=args.config)

        # 运行优化
        final_state = orchestrator.run_optimization(
            bom_file=args.bom,
            max_iterations=args.max_iter,
            convergence_threshold=args.threshold
        )

        print("\n✓ Optimization completed successfully!")
        print(f"Results saved to: {orchestrator.logger.exp_dir}")

    except Exception as e:
        print(f"\n✗ Optimization failed: {e}")
        sys.exit(1)


def cmd_list_experiments(args):
    """列出所有实验"""
    exp_dir = resolve_experiments_root(args.exp_dir)

    if not exp_dir.exists():
        print(f"Experiments directory not found: {exp_dir}")
        return

    experiments = iter_experiment_dirs(exp_dir)
    experiments.sort(
        key=lambda path: (
            str(load_json_if_exists(path / "summary.json").get("run_started_at", "") or ""),
            serialize_experiment_dir(exp_dir, path),
        ),
        reverse=True,
    )

    if not experiments:
        print("No experiments found.")
        return

    print(f"\nFound {len(experiments)} experiments:\n")
    for exp in experiments[:args.limit]:
        summary = load_json_if_exists(exp / "summary.json")
        run_id = str(summary.get("run_id", "") or "")
        status = str(summary.get("status", "") or "")
        print(f"  - {serialize_experiment_dir(exp_dir, exp)}")
        if run_id:
            print(f"    run_id: {run_id}")
        if status:
            print(f"    status: {status}")


def cmd_show_experiment(args):
    """显示实验详情"""
    exp_root = resolve_experiments_root(args.exp_dir)
    exp_path = find_experiment_dir(exp_root, args.exp_name)

    if exp_path is None or not exp_path.exists():
        print(f"Experiment not found: {args.exp_name}")
        return

    print(f"Experiment: {serialize_experiment_dir(exp_root, exp_path)}")

    # 读取报告
    report_file = exp_path / "report.md"
    if report_file.exists():
        with open(report_file, 'r', encoding='utf-8') as f:
            print(f.read())
    else:
        print("Report not found.")

    for label, trace_file in _discover_trace_artifacts(exp_path):
        print(f"\n{label}: {serialize_experiment_dir(exp_root, trace_file)}")


def cmd_latest_experiment(args):
    """显示最新实验索引"""
    latest_payload = load_latest_index(args.exp_dir)
    if not latest_payload:
        print("Latest experiment not found.")
        return
    print(json.dumps(latest_payload, ensure_ascii=False, indent=2))


def cmd_add_knowledge(args):
    """添加知识到知识库"""
    from optimization.knowledge.mass import MassRAGSystem

    rag = MassRAGSystem(
        api_key=args.api_key or "",
        knowledge_base_path=args.kb_path,
        enable_semantic=False,
    )

    item = rag.add_knowledge(
        title=args.title,
        content=args.content,
        category=args.category,
        metadata={}
    )

    print(f"✓ Knowledge added: {item.item_id}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Satellite Design Optimization System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run optimization with default config
  python -m api.cli optimize

  # Run with custom config and BOM
  python -m api.cli optimize --config my_config.yaml --bom my_bom.json

  # List recent experiments
  python -m api.cli list

  # Show experiment details
  python -m api.cli show 0307/0236_pathrel_nsga3

  # Show latest experiment
  python -m api.cli latest

  # Add knowledge to knowledge base
  python -m api.cli add-knowledge --title "My Rule" --content "..." --category heuristic
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # optimize命令
    parser_opt = subparsers.add_parser("optimize", help="Run optimization")
    parser_opt.add_argument(
        "--config",
        default="config/system/mass/base.yaml",
        help="Config file path (default: config/system/mass/base.yaml)"
    )
    parser_opt.add_argument(
        "--bom",
        help="BOM file path (optional)"
    )
    parser_opt.add_argument(
        "--max-iter",
        type=int,
        default=20,
        help="Maximum iterations (default: 20)"
    )
    parser_opt.add_argument(
        "--threshold",
        type=float,
        default=0.01,
        help="Convergence threshold (default: 0.01)"
    )
    parser_opt.set_defaults(func=cmd_optimize)

    # list命令
    parser_list = subparsers.add_parser("list", help="List experiments")
    parser_list.add_argument(
        "--exp-dir",
        default="experiments",
        help="Experiments directory (default: experiments)"
    )
    parser_list.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of experiments to show (default: 10)"
    )
    parser_list.set_defaults(func=cmd_list_experiments)

    # show命令
    parser_show = subparsers.add_parser("show", help="Show experiment details")
    parser_show.add_argument(
        "exp_name",
        help="Experiment leaf name / relative path / 'latest'"
    )
    parser_show.add_argument(
        "--exp-dir",
        default="experiments",
        help="Experiments directory (default: experiments)"
    )
    parser_show.set_defaults(func=cmd_show_experiment)

    # latest命令
    parser_latest = subparsers.add_parser("latest", help="Show latest experiment index")
    parser_latest.add_argument(
        "--exp-dir",
        default="experiments",
        help="Experiments directory (default: experiments)"
    )
    parser_latest.set_defaults(func=cmd_latest_experiment)

    # add-knowledge命令
    parser_kb = subparsers.add_parser("add-knowledge", help="Add knowledge to knowledge base")
    parser_kb.add_argument("--title", required=True, help="Knowledge title")
    parser_kb.add_argument("--content", required=True, help="Knowledge content")
    parser_kb.add_argument(
        "--category",
        choices=["standard", "case", "formula", "heuristic"],
        required=True,
        help="Knowledge category"
    )
    parser_kb.add_argument(
        "--api-key",
        default="",
        help="Optional API key (not required by mass RAG backend)",
    )
    parser_kb.add_argument(
        "--kb-path",
        default="data/knowledge_base",
        help="Knowledge base path (default: data/knowledge_base)"
    )
    parser_kb.set_defaults(func=cmd_add_knowledge)

    # 解析参数
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 执行命令
    args.func(args)


if __name__ == "__main__":
    main()
