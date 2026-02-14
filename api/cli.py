"""
Command Line Interface

提供命令行接口用于：
1. 运行优化
2. 查看实验结果
3. 管理知识库
"""

import argparse
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

from workflow.orchestrator import WorkflowOrchestrator


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
    exp_dir = Path(args.exp_dir)

    if not exp_dir.exists():
        print(f"Experiments directory not found: {exp_dir}")
        return

    experiments = sorted(exp_dir.glob("run_*"), reverse=True)

    if not experiments:
        print("No experiments found.")
        return

    print(f"\nFound {len(experiments)} experiments:\n")
    for exp in experiments[:args.limit]:
        print(f"  - {exp.name}")


def cmd_show_experiment(args):
    """显示实验详情"""
    exp_path = Path(args.exp_dir) / args.exp_name

    if not exp_path.exists():
        print(f"Experiment not found: {exp_path}")
        return

    # 读取报告
    report_file = exp_path / "report.md"
    if report_file.exists():
        with open(report_file, 'r', encoding='utf-8') as f:
            print(f.read())
    else:
        print("Report not found.")

    # 读取演化轨迹
    trace_file = exp_path / "evolution_trace.csv"
    if trace_file.exists():
        print(f"\nEvolution trace: {trace_file}")


def cmd_add_knowledge(args):
    """添加知识到知识库"""
    from optimization.knowledge.rag_system import RAGSystem

    rag = RAGSystem(
        api_key=args.api_key,
        knowledge_base_path=args.kb_path
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
  python -m api.cli show run_20260215_143022

  # Add knowledge to knowledge base
  python -m api.cli add-knowledge --title "My Rule" --content "..." --category heuristic
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # optimize命令
    parser_opt = subparsers.add_parser("optimize", help="Run optimization")
    parser_opt.add_argument(
        "--config",
        default="config/system.yaml",
        help="Config file path (default: config/system.yaml)"
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
        help="Experiment name (e.g., run_20260215_143022)"
    )
    parser_show.add_argument(
        "--exp-dir",
        default="experiments",
        help="Experiments directory (default: experiments)"
    )
    parser_show.set_defaults(func=cmd_show_experiment)

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
    parser_kb.add_argument("--api-key", required=True, help="OpenAI API key")
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
