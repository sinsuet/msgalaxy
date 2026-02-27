#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
实时监控长序列测试进度

显示当前迭代进度、温度趋势、回退次数等关键指标
"""

import sys
import os
import time
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def find_latest_experiment():
    """查找最新的实验目录"""
    exp_dir = Path("experiments")
    if not exp_dir.exists():
        return None

    runs = sorted([d for d in exp_dir.iterdir() if d.is_dir() and d.name.startswith("run_")])
    return runs[-1] if runs else None

def monitor_progress():
    """监控测试进度"""
    print("=" * 80)
    print("长序列测试实时监控")
    print("=" * 80)
    print()

    last_iteration = 0
    last_size = 0

    while True:
        exp_dir = find_latest_experiment()

        if not exp_dir:
            print("⏳ 等待实验开始...")
            time.sleep(5)
            continue

        print(f"\r实验目录: {exp_dir.name}", end="")

        # 检查 evolution_trace.csv
        csv_file = exp_dir / "evolution_trace.csv"
        if csv_file.exists():
            try:
                import pandas as pd
                df = pd.read_csv(csv_file)

                if len(df) > last_iteration:
                    last_iteration = len(df)
                    print(f"\n\n{'='*80}")
                    print(f"迭代 {last_iteration}/10 完成")
                    print(f"{'='*80}")

                    if len(df) > 0:
                        latest = df.iloc[-1]
                        print(f"  最高温度: {latest['max_temp']:.2f} °C")
                        print(f"  最小间隙: {latest['min_clearance']:.2f} mm")
                        print(f"  违规数量: {int(latest['num_violations'])}")
                        print(f"  惩罚分数: {latest['penalty_score']:.2f}")
                        print(f"  状态ID: {latest['state_id']}")

                        # 显示温度趋势
                        if len(df) >= 3:
                            recent_temps = df['max_temp'].tail(3).tolist()
                            print(f"\n  最近3次温度: {' → '.join([f'{t:.1f}°C' for t in recent_temps])}")

                            # 判断趋势
                            if recent_temps[-1] < recent_temps[0]:
                                print("  📉 温度下降趋势")
                            elif recent_temps[-1] > recent_temps[0]:
                                print("  📈 温度上升趋势")
                            else:
                                print("  ➡️ 温度稳定")

            except Exception as e:
                print(f"\n  ⚠ 读取数据失败: {e}")

        # 检查回退事件
        rollback_file = exp_dir / "rollback_events.jsonl"
        if rollback_file.exists():
            try:
                with open(rollback_file, 'r', encoding='utf-8') as f:
                    rollback_count = len(f.readlines())
                if rollback_count > 0:
                    print(f"  🔄 回退次数: {rollback_count}")
            except:
                pass

        # 检查是否完成
        if last_iteration >= 10:
            print(f"\n\n{'='*80}")
            print("✅ 测试完成！")
            print(f"{'='*80}")
            print(f"\n实验目录: {exp_dir}")
            print(f"可视化图表: {exp_dir / 'visualizations' / 'evolution_trace.png'}")
            break

        time.sleep(5)

if __name__ == "__main__":
    try:
        monitor_progress()
    except KeyboardInterrupt:
        print("\n\n监控已停止")
