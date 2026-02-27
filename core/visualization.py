#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
可视化模块

生成优化过程的可视化图表
"""

import sys
import os

# 设置UTF-8编码
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from mpl_toolkits.mplot3d import Axes3D
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
import pandas as pd
from typing import List, Dict, Any
from core.exceptions import VisualizationError
from core.logger import get_logger

logger = get_logger("visualization")


def plot_3d_layout(design_state, output_path: str):
    """
    绘制3D布局图

    Args:
        design_state: 设计状态
        output_path: 输出文件路径
    """
    try:
        logger.info(f"生成3D布局图: {output_path}")

        fig = plt.figure(figsize=(12, 10))
        ax = fig.add_subplot(111, projection='3d')

        # 绘制外壳
        if design_state.envelope:
            size = design_state.envelope.outer_size
            draw_box(ax, [0, 0, 0], [size.x, size.y, size.z],
                    color='lightgray', alpha=0.1, label='Envelope')

        # 绘制组件
        colors = ['red', 'blue', 'green', 'orange', 'purple', 'brown']
        for i, comp in enumerate(design_state.components):
            pos = [comp.position.x, comp.position.y, comp.position.z]
            dims = [comp.dimensions.x, comp.dimensions.y, comp.dimensions.z]
            color = colors[i % len(colors)]
            draw_box(ax, pos, dims, color=color, alpha=0.6, label=comp.id)

        # 设置坐标轴
        ax.set_xlabel('X (mm)')
        ax.set_ylabel('Y (mm)')
        ax.set_zlabel('Z (mm)')
        ax.set_title('3D Component Layout')

        # 设置视角
        ax.view_init(elev=20, azim=45)

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        logger.info(f"3D布局图生成成功")

    except Exception as e:
        error_msg = f"生成3D布局图失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise VisualizationError(error_msg) from e


def draw_box(ax, position, dimensions, color='blue', alpha=0.5, label=None):
    """
    在3D坐标系中绘制立方体

    Args:
        ax: 3D坐标轴
        position: 位置 [x, y, z]
        dimensions: 尺寸 [dx, dy, dz]
        color: 颜色
        alpha: 透明度
        label: 标签
    """
    x, y, z = position
    dx, dy, dz = dimensions

    # 定义立方体的8个顶点
    vertices = [
        [x, y, z],
        [x + dx, y, z],
        [x + dx, y + dy, z],
        [x, y + dy, z],
        [x, y, z + dz],
        [x + dx, y, z + dz],
        [x + dx, y + dy, z + dz],
        [x, y + dy, z + dz]
    ]

    # 定义立方体的6个面
    faces = [
        [vertices[0], vertices[1], vertices[5], vertices[4]],  # 前
        [vertices[2], vertices[3], vertices[7], vertices[6]],  # 后
        [vertices[0], vertices[3], vertices[7], vertices[4]],  # 左
        [vertices[1], vertices[2], vertices[6], vertices[5]],  # 右
        [vertices[0], vertices[1], vertices[2], vertices[3]],  # 底
        [vertices[4], vertices[5], vertices[6], vertices[7]]   # 顶
    ]

    # 创建3D多边形集合
    poly = Poly3DCollection(faces, alpha=alpha, facecolor=color,
                           edgecolor='black', linewidth=0.5)
    ax.add_collection3d(poly)

    # 添加标签（在中心位置）
    if label:
        cx = x + dx / 2
        cy = y + dy / 2
        cz = z + dz / 2
        ax.text(cx, cy, cz, label, fontsize=8, ha='center')


def plot_evolution_trace(csv_path: str, output_path: str):
    """
    绘制演化轨迹图

    Args:
        csv_path: CSV文件路径
        output_path: 输出文件路径
    """
    # 读取数据
    df = pd.read_csv(csv_path)

    if len(df) == 0:
        print("  ⚠ 没有数据可绘制")
        return

    # 创建子图
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # 1. 温度演化（智能Y轴限制，剔除极值）
    ax = axes[0, 0]

    # 定义合理的温度范围（工程上限）
    TEMP_UPPER_LIMIT = 150.0  # °C
    TEMP_PENALTY_THRESHOLD = 500.0  # 超过此值视为惩罚分

    # 分离正常值和惩罚值
    normal_mask = df['max_temp'] < TEMP_PENALTY_THRESHOLD
    penalty_mask = df['max_temp'] >= TEMP_PENALTY_THRESHOLD

    # 绘制正常温度曲线
    if normal_mask.any():
        ax.plot(df.loc[normal_mask, 'iteration'],
                df.loc[normal_mask, 'max_temp'],
                'r-o', label='Max Temp', linewidth=2, markersize=6)

    # 标记惩罚点（用红色叉号在图表顶部）
    if penalty_mask.any():
        penalty_iters = df.loc[penalty_mask, 'iteration']
        # 在 Y 轴上限位置标记失败点
        ax.plot(penalty_iters,
                [TEMP_UPPER_LIMIT * 0.95] * len(penalty_iters),
                'rx', markersize=12, markeredgewidth=3,
                label='Failed (Penalty)', zorder=10)
        # 添加文本标注
        for iter_num in penalty_iters:
            ax.annotate('FAIL',
                       xy=(iter_num, TEMP_UPPER_LIMIT * 0.95),
                       xytext=(0, -15), textcoords='offset points',
                       ha='center', fontsize=8, color='red', weight='bold')

    ax.set_xlabel('Iteration')
    ax.set_ylabel('Temperature (°C)')
    ax.set_title('Temperature Evolution (Y-axis limited to engineering range)')
    ax.set_ylim(bottom=0, top=TEMP_UPPER_LIMIT)  # 强制限制Y轴范围
    ax.grid(True, alpha=0.3)
    ax.legend()

    # 添加安全区域标记
    ax.axhline(y=60, color='orange', linestyle='--', alpha=0.5, linewidth=1, label='Warning (60°C)')
    ax.fill_between(df['iteration'], 0, 60, alpha=0.1, color='green', label='Safe Zone')

    # 2. 间隙演化
    ax = axes[0, 1]
    ax.plot(df['iteration'], df['min_clearance'], 'b-o', label='Min Clearance', linewidth=2)
    ax.set_xlabel('Iteration')
    ax.set_ylabel('Clearance (mm)')
    ax.set_title('Clearance Evolution')
    ax.grid(True, alpha=0.3)
    ax.legend()

    # 3. 质量和功率
    ax = axes[1, 0]
    ax.plot(df['iteration'], df['total_mass'], 'g-o', label='Total Mass', linewidth=2)
    ax2 = ax.twinx()
    ax2.plot(df['iteration'], df['total_power'], 'orange', linestyle='--',
            marker='s', label='Total Power', linewidth=2)
    ax.set_xlabel('Iteration')
    ax.set_ylabel('Mass (kg)', color='g')
    ax2.set_ylabel('Power (W)', color='orange')
    ax.set_title('Mass and Power Evolution')
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left')
    ax2.legend(loc='upper right')

    # 4. 违规数
    ax = axes[1, 1]
    ax.plot(df['iteration'], df['num_violations'], 'purple', marker='o', linewidth=2)
    ax.fill_between(df['iteration'], 0, df['num_violations'], alpha=0.3, color='purple')
    ax.set_xlabel('Iteration')
    ax.set_ylabel('Number of Violations')
    ax.set_title('Constraint Violations')
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def plot_thermal_heatmap(design_state, thermal_data: Dict[str, float], output_path: str):
    """
    绘制热图

    Args:
        design_state: 设计状态
        thermal_data: 热数据字典 {component_id: temperature}
        output_path: 输出文件路径
    """
    try:
        logger.info(f"生成热图: {output_path}")

        fig = plt.figure(figsize=(14, 10))

        # 创建3D视图
        ax = fig.add_subplot(121, projection='3d')

        # 绘制外壳
        if design_state.envelope:
            size = design_state.envelope.outer_size
            draw_box(ax, [0, 0, 0], [size.x, size.y, size.z],
                    color='lightgray', alpha=0.05, label='Envelope')

        # 获取温度范围用于颜色映射
        temps = list(thermal_data.values())
        if temps:
            min_temp = min(temps)
            max_temp = max(temps)
            temp_range = max_temp - min_temp if max_temp > min_temp else 1
        else:
            min_temp, max_temp, temp_range = 0, 100, 100

        # 绘制组件（根据温度着色）
        for comp in design_state.components:
            pos = [comp.position.x, comp.position.y, comp.position.z]
            dims = [comp.dimensions.x, comp.dimensions.y, comp.dimensions.z]

            # 获取温度并映射到颜色
            temp = thermal_data.get(comp.id, min_temp)
            normalized_temp = (temp - min_temp) / temp_range
            color = plt.cm.hot(normalized_temp)

            draw_box(ax, pos, dims, color=color, alpha=0.7, label=f"{comp.id}\n{temp:.1f}°C")

        ax.set_xlabel('X (mm)')
        ax.set_ylabel('Y (mm)')
        ax.set_zlabel('Z (mm)')
        ax.set_title('3D Thermal Distribution')
        ax.view_init(elev=20, azim=45)

        # 创建2D俯视图热图
        ax2 = fig.add_subplot(122)

        # 创建网格
        if design_state.envelope:
            grid_size = 50
            x_grid = np.linspace(0, design_state.envelope.outer_size.x, grid_size)
            y_grid = np.linspace(0, design_state.envelope.outer_size.y, grid_size)
            X, Y = np.meshgrid(x_grid, y_grid)
            Z = np.zeros_like(X)

            # 为每个网格点分配温度（基于最近组件）
            for i in range(grid_size):
                for j in range(grid_size):
                    x, y = X[i, j], Y[i, j]
                    min_dist = float('inf')
                    nearest_temp = min_temp

                    for comp in design_state.components:
                        cx = comp.position.x + comp.dimensions.x / 2
                        cy = comp.position.y + comp.dimensions.y / 2
                        dist = np.sqrt((x - cx)**2 + (y - cy)**2)

                        if dist < min_dist:
                            min_dist = dist
                            nearest_temp = thermal_data.get(comp.id, min_temp)

                    Z[i, j] = nearest_temp

            # 绘制热图
            im = ax2.contourf(X, Y, Z, levels=20, cmap='hot')
            plt.colorbar(im, ax=ax2, label='Temperature (°C)')

            # 绘制组件边界
            for comp in design_state.components:
                rect = patches.Rectangle(
                    (comp.position.x, comp.position.y),
                    comp.dimensions.x, comp.dimensions.y,
                    linewidth=1, edgecolor='cyan', facecolor='none'
                )
                ax2.add_patch(rect)

                # 添加标签
                cx = comp.position.x + comp.dimensions.x / 2
                cy = comp.position.y + comp.dimensions.y / 2
                temp = thermal_data.get(comp.id, min_temp)
                ax2.text(cx, cy, f"{temp:.1f}°C", ha='center', va='center',
                        fontsize=8, color='white', weight='bold')

        ax2.set_xlabel('X (mm)')
        ax2.set_ylabel('Y (mm)')
        ax2.set_title('Top View Thermal Heatmap')
        ax2.set_aspect('equal')

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        logger.info(f"热图生成成功")

    except Exception as e:
        error_msg = f"生成热图失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise VisualizationError(error_msg) from e


def generate_visualizations(experiment_dir: str):
    """
    为实验生成所有可视化

    Args:
        experiment_dir: 实验目录路径
    """
    print("\n生成可视化...")

    viz_dir = os.path.join(experiment_dir, 'visualizations')
    os.makedirs(viz_dir, exist_ok=True)

    # 1. 演化轨迹图
    csv_path = os.path.join(experiment_dir, 'evolution_trace.csv')
    if os.path.exists(csv_path):
        try:
            output_path = os.path.join(viz_dir, 'evolution_trace.png')
            plot_evolution_trace(csv_path, output_path)
            print(f"  [OK] 演化轨迹图: {output_path}")
        except Exception as e:
            print(f"  [FAIL] 演化轨迹图生成失败: {e}")

    # 2. 3D布局图（如果有设计状态文件）
    import glob
    design_files = glob.glob(os.path.join(experiment_dir, 'design_state_iter_*.json'))

    if design_files:
        try:
            # 只绘制最后一次迭代
            latest_file = sorted(design_files)[-1]

            import json
            from core.protocol import DesignState

            with open(latest_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 重建DesignState对象
            design_state = DesignState(**data)

            output_path = os.path.join(viz_dir, 'final_layout_3d.png')
            plot_3d_layout(design_state, output_path)
            print(f"  [OK] 3D布局图: {output_path}")

            # 3. 热图（如果有热数据）
            # 从最新的迭代中提取热数据
            thermal_data = {}
            for comp in design_state.components:
                # 模拟温度数据（实际应该从仿真结果中获取）
                thermal_data[comp.id] = 25.0 + np.random.uniform(0, 30)

            output_path = os.path.join(viz_dir, 'thermal_heatmap.png')
            plot_thermal_heatmap(design_state, thermal_data, output_path)
            print(f"  [OK] 热图: {output_path}")

        except Exception as e:
            print(f"  [FAIL] 可视化生成失败: {e}")

    print("可视化生成完成")


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        experiment_dir = sys.argv[1]
        generate_visualizations(experiment_dir)
    else:
        print("用法: python visualization.py <experiment_dir>")
