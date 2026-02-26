#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
实验数据清理工具

用于清理旧的实验数据，保留重要的实验结果
"""

import os
import shutil
from datetime import datetime
from pathlib import Path


def list_experiments(experiments_dir='experiments'):
    """列出所有实验"""
    if not os.path.exists(experiments_dir):
        print(f"实验目录不存在: {experiments_dir}")
        return []

    experiments = []
    for exp_dir in sorted(os.listdir(experiments_dir)):
        exp_path = os.path.join(experiments_dir, exp_dir)
        if os.path.isdir(exp_path):
            # 获取目录大小
            total_size = sum(
                os.path.getsize(os.path.join(dirpath, filename))
                for dirpath, dirnames, filenames in os.walk(exp_path)
                for filename in filenames
            )

            # 检查是否有可视化
            viz_dir = os.path.join(exp_path, 'visualizations')
            has_viz = os.path.exists(viz_dir) and len(os.listdir(viz_dir)) > 0

            experiments.append({
                'name': exp_dir,
                'path': exp_path,
                'size': total_size,
                'has_viz': has_viz
            })

    return experiments


def print_experiments(experiments):
    """打印实验列表"""
    print("\n实验列表:")
    print("-" * 80)
    print(f"{'实验ID':<30} {'大小':<15} {'可视化':<10}")
    print("-" * 80)

    total_size = 0
    for exp in experiments:
        size_mb = exp['size'] / 1024 / 1024
        viz_status = "Yes" if exp['has_viz'] else "No"
        print(f"{exp['name']:<30} {size_mb:>10.2f} MB   {viz_status:<10}")
        total_size += exp['size']

    print("-" * 80)
    print(f"{'总计':<30} {total_size/1024/1024:>10.2f} MB")
    print()


def archive_old_experiments(experiments_dir='experiments', archive_dir='experiments/archive', keep_recent=3):
    """归档旧实验，保留最近的N个"""
    experiments = list_experiments(experiments_dir)

    if len(experiments) <= keep_recent:
        print(f"实验数量 ({len(experiments)}) <= 保留数量 ({keep_recent})，无需归档")
        return

    # 创建归档目录
    os.makedirs(archive_dir, exist_ok=True)

    # 归档旧实验
    to_archive = experiments[:-keep_recent]

    print(f"\n将归档 {len(to_archive)} 个旧实验:")
    for exp in to_archive:
        print(f"  - {exp['name']}")

    response = input("\n确认归档? (y/n): ")
    if response.lower() != 'y':
        print("已取消")
        return

    for exp in to_archive:
        src = exp['path']
        dst = os.path.join(archive_dir, exp['name'])
        print(f"归档: {exp['name']}")
        shutil.move(src, dst)

    print(f"\n✓ 已归档 {len(to_archive)} 个实验到 {archive_dir}")


def clean_empty_visualizations(experiments_dir='experiments'):
    """清理空的可视化目录"""
    experiments = list_experiments(experiments_dir)

    cleaned = 0
    for exp in experiments:
        viz_dir = os.path.join(exp['path'], 'visualizations')
        if os.path.exists(viz_dir) and len(os.listdir(viz_dir)) == 0:
            os.rmdir(viz_dir)
            print(f"清理空目录: {exp['name']}/visualizations")
            cleaned += 1

    if cleaned > 0:
        print(f"\n✓ 清理了 {cleaned} 个空的可视化目录")
    else:
        print("\n无需清理")


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == 'list':
            experiments = list_experiments()
            print_experiments(experiments)

        elif command == 'archive':
            keep = int(sys.argv[2]) if len(sys.argv) > 2 else 3
            archive_old_experiments(keep_recent=keep)

        elif command == 'clean':
            clean_empty_visualizations()

        else:
            print(f"未知命令: {command}")
            print("\n用法:")
            print("  python scripts/clean_experiments.py list              - 列出所有实验")
            print("  python scripts/clean_experiments.py archive [N]       - 归档旧实验，保留最近N个（默认3）")
            print("  python scripts/clean_experiments.py clean             - 清理空的可视化目录")
    else:
        print("实验数据清理工具")
        print("\n用法:")
        print("  python scripts/clean_experiments.py list              - 列出所有实验")
        print("  python scripts/clean_experiments.py archive [N]       - 归档旧实验，保留最近N个（默认3）")
        print("  python scripts/clean_experiments.py clean             - 清理空的可视化目录")
        print("\n示例:")
        print("  python scripts/clean_experiments.py list")
        print("  python scripts/clean_experiments.py archive 3")
