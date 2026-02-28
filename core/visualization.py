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
from typing import List, Dict, Any, Optional
from pathlib import Path
from core.exceptions import VisualizationError
from core.logger import get_logger

logger = get_logger("visualization")


def _component_min_corner(comp) -> List[float]:
    """将组件中心点坐标转换为包围盒最小角坐标。"""
    return [
        float(comp.position.x - comp.dimensions.x / 2.0),
        float(comp.position.y - comp.dimensions.y / 2.0),
        float(comp.position.z - comp.dimensions.z / 2.0),
    ]


def _envelope_bounds(design_state) -> tuple[List[float], List[float]]:
    """返回包络最小角与尺寸。"""
    size = design_state.envelope.outer_size
    if getattr(design_state.envelope, "origin", "center") == "center":
        min_corner = [-size.x / 2.0, -size.y / 2.0, -size.z / 2.0]
    else:
        min_corner = [0.0, 0.0, 0.0]
    dims = [size.x, size.y, size.z]
    return min_corner, dims


def _is_constant_series(values: np.ndarray, eps: float = 1e-9) -> bool:
    """判断序列是否几乎恒定。"""
    if values.size <= 1:
        return True
    finite_values = values[np.isfinite(values)]
    if finite_values.size <= 1:
        return True
    return float(np.max(finite_values) - np.min(finite_values)) <= eps


def _to_float_series(df: pd.DataFrame, column: str) -> Optional[np.ndarray]:
    """从DataFrame中安全提取浮点序列。"""
    if column not in df.columns:
        return None
    return pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=float)


def build_power_density_proxy(design_state, csv_path: str = "") -> Dict[str, float]:
    """
    构建组件热代理值（确定性，无随机数）。

    说明：当前每轮日志没有按组件温度分布，故使用功率密度生成可解释代理值，
    用于展示热风险空间分布。若有全局 max_temp，则用其缩放幅度。
    """
    components = getattr(design_state, "components", [])
    if not components:
        return {}

    density = {}
    for comp in components:
        volume = max(float(comp.dimensions.x * comp.dimensions.y * comp.dimensions.z), 1e-6)
        density[comp.id] = float(comp.power) / volume

    values = np.array(list(density.values()), dtype=float)
    v_min = float(np.min(values))
    v_max = float(np.max(values))
    if abs(v_max - v_min) < 1e-12:
        normalized = {k: 0.5 for k in density.keys()}
    else:
        normalized = {k: float((v - v_min) / (v_max - v_min)) for k, v in density.items()}

    # 默认 20~60°C；若有全局 max_temp 则适当拉伸到不超过 120°C
    t_base = 20.0
    t_span = 40.0
    try:
        if csv_path and Path(csv_path).exists():
            df = pd.read_csv(csv_path)
            if "max_temp" in df.columns and len(df) > 0:
                global_max = float(df["max_temp"].iloc[-1])
                global_max = float(np.clip(global_max, 30.0, 120.0))
                t_span = max(20.0, global_max - t_base)
    except Exception:
        pass

    return {k: t_base + t_span * v for k, v in normalized.items()}


def _build_visualization_summary(
    csv_path: str,
    initial_state=None,
    final_state=None,
    thermal_data: Optional[Dict[str, float]] = None,
) -> str:
    """
    构建可视化摘要文本，快速说明迭代是否有效。
    """
    lines: List[str] = []
    lines.append("=== Optimization Visualization Summary ===")

    df: Optional[pd.DataFrame] = None
    if csv_path and os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
        except Exception:
            df = None

    if df is not None and len(df) > 0:
        lines.append(f"- Iterations logged: {len(df)}")

        penalty = _to_float_series(df, "penalty_score")
        if penalty is not None and len(penalty) > 0:
            p0 = float(np.nan_to_num(penalty[0], nan=0.0))
            p1 = float(np.nan_to_num(penalty[-1], nan=0.0))
            if p1 <= p0:
                abs_drop = p0 - p1
                rel_drop = (abs_drop / max(abs(p0), 1e-9)) * 100.0
                lines.append(
                    f"- Penalty: {p0:.2f} -> {p1:.2f} "
                    f"(reduction {abs_drop:.2f}, {rel_drop:.1f}%)"
                )
            else:
                abs_up = p1 - p0
                rel_up = (abs_up / max(abs(p0), 1e-9)) * 100.0
                lines.append(
                    f"- Penalty: {p0:.2f} -> {p1:.2f} "
                    f"(increase {abs_up:.2f}, {rel_up:.1f}%)"
                )

            final_idx = len(df) - 1
            part_cols = [
                ("penalty_violation", "violation"),
                ("penalty_temp", "temp"),
                ("penalty_clearance", "clearance"),
                ("penalty_cg", "cg"),
                ("penalty_collision", "collision"),
            ]
            part_values = []
            for col, label in part_cols:
                series = _to_float_series(df, col)
                if series is None or final_idx >= len(series):
                    continue
                val = float(np.nan_to_num(series[final_idx], nan=0.0))
                part_values.append((label, val))
            if part_values:
                dominant = max(part_values, key=lambda x: x[1])
                lines.append(
                    f"- Dominant penalty term (final): {dominant[0]} = {dominant[1]:.2f}"
                )

        violations = _to_float_series(df, "num_violations")
        if violations is not None and len(violations) > 0:
            v0 = int(round(float(np.nan_to_num(violations[0], nan=0.0))))
            v1 = int(round(float(np.nan_to_num(violations[-1], nan=0.0))))
            lines.append(f"- Violations: {v0} -> {v1} ({v1 - v0:+d})")

        eff = _to_float_series(df, "effectiveness_score")
        if eff is not None and len(eff) > 0:
            eff = np.nan_to_num(eff, nan=0.0)
            positive_ratio = float(np.mean(eff > 0.0)) * 100.0
            lines.append(
                f"- Effectiveness: mean={float(np.mean(eff)):.2f}, "
                f"positive_ratio={positive_ratio:.1f}%"
            )

        stable_metrics = []
        for col in ("max_temp", "min_clearance", "cg_offset"):
            series = _to_float_series(df, col)
            if series is not None and _is_constant_series(np.nan_to_num(series, nan=0.0)):
                stable_metrics.append(col)
        if stable_metrics:
            lines.append(
                "- Near-constant metrics detected: " + ", ".join(stable_metrics)
            )

    # 布局位移摘要
    if initial_state is not None and final_state is not None:
        init_map = {c.id: c for c in initial_state.components}
        final_map = {c.id: c for c in final_state.components}
        common_ids = sorted(set(init_map.keys()) & set(final_map.keys()))
        if common_ids:
            movements = []
            for cid in common_ids:
                c0 = init_map[cid]
                c1 = final_map[cid]
                dx = float(c1.position.x - c0.position.x)
                dy = float(c1.position.y - c0.position.y)
                dz = float(c1.position.z - c0.position.z)
                dist = float(np.sqrt(dx * dx + dy * dy + dz * dz))
                movements.append((cid, dist, dx, dy, dz))

            max_item = max(movements, key=lambda x: x[1])
            mean_move = float(np.mean([m[1] for m in movements]))
            lines.append(
                f"- Layout movement: mean={mean_move:.2f} mm, "
                f"max={max_item[1]:.2f} mm ({max_item[0]})"
            )
            if max_item[1] > 1e-9:
                lines.append(
                    f"  direction({max_item[0]}): "
                    f"dx={max_item[2]:+.2f}, dy={max_item[3]:+.2f}, dz={max_item[4]:+.2f} mm"
                )

    # 热代理摘要
    if thermal_data:
        hottest = max(thermal_data.items(), key=lambda x: x[1])
        coldest = min(thermal_data.items(), key=lambda x: x[1])
        lines.append(
            f"- Thermal proxy: hottest={hottest[0]} {hottest[1]:.1f} degC, "
            f"coolest={coldest[0]} {coldest[1]:.1f} degC"
        )

    if len(lines) == 1:
        lines.append("- No available data for summary.")

    return "\n".join(lines)


def plot_layout_evolution(initial_state, final_state, output_path: str):
    """
    绘制布局演化图：左侧 XY 位移箭头，右侧组件位移量条形图。
    """
    try:
        logger.info(f"生成布局演化图: {output_path}")

        init_map = {c.id: c for c in initial_state.components}
        final_map = {c.id: c for c in final_state.components}
        common_ids = sorted(set(init_map.keys()) & set(final_map.keys()))
        if not common_ids:
            logger.warning("布局演化图跳过：初始与最终状态无公共组件")
            return

        disp_data = []
        for cid in common_ids:
            c0 = init_map[cid]
            c1 = final_map[cid]
            dx = float(c1.position.x - c0.position.x)
            dy = float(c1.position.y - c0.position.y)
            dz = float(c1.position.z - c0.position.z)
            dist = float(np.sqrt(dx * dx + dy * dy + dz * dz))
            disp_data.append((cid, c0.position.x, c0.position.y, dx, dy, dist))

        fig, axes = plt.subplots(1, 2, figsize=(14, 6))

        # 左图：XY 平面箭头
        ax = axes[0]
        for cid, x0, y0, dx, dy, dist in disp_data:
            ax.scatter([x0], [y0], color="tab:blue", s=20)
            ax.arrow(
                x0, y0, dx, dy,
                width=0.2, head_width=3.0, head_length=4.0,
                length_includes_head=True, color="tab:red", alpha=0.7
            )
            if dist > 1e-6:
                ax.text(x0 + dx, y0 + dy, cid, fontsize=8)

        ax.set_title("Layout Shift (XY)")
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        ax.grid(True, alpha=0.3)
        ax.axis("equal")

        # 右图：位移条形图
        ax2 = axes[1]
        disp_sorted = sorted(disp_data, key=lambda x: x[5], reverse=True)
        labels = [item[0] for item in disp_sorted]
        values = [item[5] for item in disp_sorted]
        bars = ax2.bar(labels, values, color="tab:orange", alpha=0.8)
        ax2.set_title("Component Displacement Magnitude")
        ax2.set_ylabel("Displacement (mm)")
        ax2.tick_params(axis='x', rotation=45)
        ax2.grid(True, axis='y', alpha=0.3)

        for bar, v in zip(bars, values):
            ax2.text(bar.get_x() + bar.get_width() / 2.0, bar.get_height(), f"{v:.1f}",
                     ha="center", va="bottom", fontsize=8)

        if np.max(values) <= 1e-6:
            ax2.text(0.5, 0.5, "No component movement detected",
                     transform=ax2.transAxes, ha="center", va="center",
                     fontsize=11, color="gray")

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        logger.info("布局演化图生成成功")

    except Exception as e:
        error_msg = f"生成布局演化图失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise VisualizationError(error_msg) from e


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
            env_pos, env_dims = _envelope_bounds(design_state)
            draw_box(
                ax,
                env_pos,
                env_dims,
                color='lightgray',
                alpha=0.08,
                label='Envelope'
            )

        # 绘制组件
        colors = ['red', 'blue', 'green', 'orange', 'purple', 'brown']
        x_points: List[float] = []
        y_points: List[float] = []
        z_points: List[float] = []

        for i, comp in enumerate(design_state.components):
            pos = _component_min_corner(comp)
            dims = [comp.dimensions.x, comp.dimensions.y, comp.dimensions.z]
            color = colors[i % len(colors)]
            draw_box(ax, pos, dims, color=color, alpha=0.6, label=comp.id)
            x_points.extend([pos[0], pos[0] + dims[0]])
            y_points.extend([pos[1], pos[1] + dims[1]])
            z_points.extend([pos[2], pos[2] + dims[2]])

        if design_state.envelope:
            env_pos, env_dims = _envelope_bounds(design_state)
            x_points.extend([env_pos[0], env_pos[0] + env_dims[0]])
            y_points.extend([env_pos[1], env_pos[1] + env_dims[1]])
            z_points.extend([env_pos[2], env_pos[2] + env_dims[2]])

        if x_points and y_points and z_points:
            x_min, x_max = min(x_points), max(x_points)
            y_min, y_max = min(y_points), max(y_points)
            z_min, z_max = min(z_points), max(z_points)

            dx = max(x_max - x_min, 1.0)
            dy = max(y_max - y_min, 1.0)
            dz = max(z_max - z_min, 1.0)
            span = max(dx, dy, dz)

            cx = (x_min + x_max) / 2.0
            cy = (y_min + y_max) / 2.0
            cz = (z_min + z_max) / 2.0

            margin = span * 0.08
            half = span / 2.0 + margin
            ax.set_xlim(cx - half, cx + half)
            ax.set_ylim(cy - half, cy + half)
            ax.set_zlim(cz - half, cz + half)

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
    try:
        logger.info(f"生成演化轨迹图: {output_path}")
        df = pd.read_csv(csv_path)

        if len(df) == 0:
            logger.warning("演化轨迹图跳过：没有数据可绘制")
            return

        if "iteration" not in df.columns:
            df["iteration"] = np.arange(1, len(df) + 1, dtype=float)

        iterations = _to_float_series(df, "iteration")
        if iterations is None:
            iterations = np.arange(1, len(df) + 1, dtype=float)

        # 对齐为连续索引，便于显示
        x_ticks = np.array(iterations, dtype=float)

        fig, axes = plt.subplots(2, 2, figsize=(16, 11))

        # 1) 惩罚分与违规数（主图）
        ax = axes[0, 0]
        penalty_total = _to_float_series(df, "penalty_score")
        num_violations = _to_float_series(df, "num_violations")

        if penalty_total is not None:
            penalty_total = np.nan_to_num(penalty_total, nan=0.0)
            ax.plot(x_ticks, penalty_total, color="black", marker="o", linewidth=2.2, label="Penalty Total")

            breakdown_specs = [
                ("penalty_violation", "Violation", "#d62728"),
                ("penalty_temp", "Temp", "#ff7f0e"),
                ("penalty_clearance", "Clearance", "#1f77b4"),
                ("penalty_cg", "CG", "#9467bd"),
                ("penalty_collision", "Collision", "#8c564b"),
            ]
            stack_values = []
            stack_labels = []
            stack_colors = []
            for col, label, color in breakdown_specs:
                values = _to_float_series(df, col)
                if values is None:
                    continue
                values = np.nan_to_num(values, nan=0.0)
                if np.any(np.abs(values) > 1e-12):
                    stack_values.append(values)
                    stack_labels.append(label)
                    stack_colors.append(color)

            if stack_values:
                ax.stackplot(
                    x_ticks,
                    *stack_values,
                    labels=stack_labels,
                    colors=stack_colors,
                    alpha=0.18
                )

            if _is_constant_series(penalty_total):
                ax.text(
                    0.03,
                    0.92,
                    "Penalty nearly constant",
                    transform=ax.transAxes,
                    fontsize=10,
                    color="gray",
                    bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8),
                )
        else:
            max_temp = _to_float_series(df, "max_temp")
            if max_temp is not None:
                ax.plot(x_ticks, max_temp, "r-o", linewidth=2.0, label="Max Temp (Fallback)")

        ax.set_title("Penalty Decomposition")
        ax.set_xlabel("Iteration")
        ax.set_ylabel("Penalty Score")
        ax.grid(True, alpha=0.25)

        ax_r = ax.twinx()
        if num_violations is not None:
            num_violations = np.nan_to_num(num_violations, nan=0.0)
            num_violations = np.clip(num_violations, a_min=0.0, a_max=None)
            v_color = "#d62728"

            # 违规数量主轨迹：粗线 + 阶梯填充，视觉优先级提升
            ax_r.step(
                x_ticks,
                num_violations,
                where="post",
                color=v_color,
                linewidth=3.0,
                linestyle="-",
                label="Violations"
            )
            ax_r.fill_between(
                x_ticks,
                0.0,
                num_violations,
                step="post",
                color=v_color,
                alpha=0.18,
                zorder=4
            )

            # 每轮违规点：随违规数量增大而加粗
            marker_sizes = 46.0 + 34.0 * num_violations
            ax_r.scatter(
                x_ticks,
                num_violations,
                s=marker_sizes,
                color=v_color,
                edgecolors="white",
                linewidths=0.8,
                zorder=20
            )

            # 末轮星标和标注
            final_v = float(num_violations[-1])
            ax_r.scatter(
                [x_ticks[-1]],
                [final_v],
                s=220,
                marker="*",
                color="gold",
                edgecolors=v_color,
                linewidths=1.4,
                zorder=25,
                label=f"Final Violations={int(round(final_v))}"
            )
            ax_r.annotate(
                f"final={int(round(final_v))}",
                xy=(x_ticks[-1], final_v),
                xytext=(8, 8),
                textcoords="offset points",
                fontsize=9,
                color=v_color,
                fontweight="bold"
            )

            # 首次清零高亮：只在从>0下降到0时标注
            first_clear_idx = None
            for i in range(1, len(num_violations)):
                if num_violations[i] <= 0.0 and num_violations[i - 1] > 0.0:
                    first_clear_idx = i
                    break
            if first_clear_idx is not None:
                clear_x = float(x_ticks[first_clear_idx])
                ax.axvline(
                    x=clear_x,
                    color="#2ca02c",
                    linestyle=":",
                    linewidth=2.0,
                    alpha=0.95,
                    zorder=3
                )
                ax_r.scatter(
                    [clear_x],
                    [0.0],
                    s=90,
                    marker="D",
                    color="#2ca02c",
                    edgecolors="white",
                    linewidths=0.8,
                    zorder=24,
                    label=f"Cleared @ iter {int(round(clear_x))}"
                )
                ax_r.annotate(
                    f"cleared@{int(round(clear_x))}",
                    xy=(clear_x, 0.0),
                    xytext=(6, 12),
                    textcoords="offset points",
                    fontsize=9,
                    color="#2ca02c",
                    fontweight="bold"
                )

            y_max_v = max(float(np.max(num_violations)), 1.0)
            ax_r.set_ylim(0.0, y_max_v + 0.6)
            ax_r.set_ylabel("Violations", color=v_color, fontweight="bold")
            ax_r.tick_params(axis='y', colors=v_color, width=1.5)
            ax_r.spines["right"].set_color(v_color)
            ax_r.spines["right"].set_linewidth(2.0)

            if _is_constant_series(num_violations):
                ax_r.text(
                    0.03,
                    0.82,
                    "Violations nearly constant",
                    transform=ax_r.transAxes,
                    fontsize=9,
                    color=v_color,
                    bbox=dict(boxstyle="round", fc="white", ec=v_color, alpha=0.85),
                )

        handles_l, labels_l = ax.get_legend_handles_labels()
        handles_r, labels_r = ax_r.get_legend_handles_labels()
        if handles_l or handles_r:
            ax.legend(handles_l + handles_r, labels_l + labels_r, loc="upper right", fontsize=8)

        # 2) 单轮有效性分数
        ax = axes[0, 1]
        effectiveness = _to_float_series(df, "effectiveness_score")
        if effectiveness is not None:
            effectiveness = np.nan_to_num(effectiveness, nan=0.0)
            colors = ["#2ca02c" if val >= 0 else "#d62728" for val in effectiveness]
            ax.bar(x_ticks, effectiveness, color=colors, alpha=0.75, label="Effectiveness")
            ax.plot(x_ticks, effectiveness, color="black", linewidth=1.0, alpha=0.6)
            ax.axhline(0.0, color="gray", linewidth=1.0, linestyle="--")
            if _is_constant_series(effectiveness):
                ax.text(
                    0.03,
                    0.92,
                    "Effectiveness nearly constant",
                    transform=ax.transAxes,
                    fontsize=10,
                    color="gray",
                    bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8),
                )
        else:
            solver_cost = _to_float_series(df, "solver_cost")
            if solver_cost is not None:
                solver_cost = np.nan_to_num(solver_cost, nan=0.0)
                ax.plot(x_ticks, solver_cost, "m-o", linewidth=2.0, label="Solver Cost (Fallback)")

        ax.set_title("Iteration Effectiveness")
        ax.set_xlabel("Iteration")
        ax.set_ylabel("Score (-100~100)")
        ax.grid(True, alpha=0.25)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(loc="best", fontsize=8)

        # 3) 关键连续指标（温度 / 间隙 / 质心偏移）
        ax = axes[1, 0]
        max_temp = _to_float_series(df, "max_temp")
        min_clearance = _to_float_series(df, "min_clearance")
        cg_offset = _to_float_series(df, "cg_offset")

        if max_temp is not None:
            max_temp = np.nan_to_num(max_temp, nan=0.0)
            ax.plot(x_ticks, max_temp, "r-o", linewidth=2.0, label="Max Temp (degC)")

        ax2 = ax.twinx()
        if min_clearance is not None:
            min_clearance = np.nan_to_num(min_clearance, nan=0.0)
            ax2.plot(x_ticks, min_clearance, "b-s", linewidth=1.8, label="Min Clearance (mm)")
        if cg_offset is not None:
            cg_offset = np.nan_to_num(cg_offset, nan=0.0)
            ax2.plot(x_ticks, cg_offset, color="#6a3d9a", marker="^", linewidth=1.8, label="CG Offset (mm)")

        # 恒定序列提示（避免“看起来没变化”）
        constant_flags = []
        if max_temp is not None:
            constant_flags.append(("max_temp", _is_constant_series(max_temp)))
        if min_clearance is not None:
            constant_flags.append(("min_clearance", _is_constant_series(min_clearance)))
        if cg_offset is not None:
            constant_flags.append(("cg_offset", _is_constant_series(cg_offset)))
        if constant_flags and all(flag for _, flag in constant_flags):
            ax.text(
                0.03,
                0.92,
                "Key metrics nearly constant",
                transform=ax.transAxes,
                fontsize=10,
                color="gray",
                bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8),
            )

        ax.set_title("Thermal & Geometry Key Metrics")
        ax.set_xlabel("Iteration")
        ax.set_ylabel("Temperature (degC)", color="r")
        ax2.set_ylabel("Clearance / CG Offset (mm)", color="b")
        ax.grid(True, alpha=0.25)
        handles_l, labels_l = ax.get_legend_handles_labels()
        handles_r, labels_r = ax2.get_legend_handles_labels()
        if handles_l or handles_r:
            ax.legend(handles_l + handles_r, labels_l + labels_r, loc="best", fontsize=8)

        # 4) 增量指标（每轮变化）
        ax = axes[1, 1]
        delta_specs = [
            ("delta_penalty", "Delta Penalty", "#d62728"),
            ("delta_cg_offset", "Delta CG Offset", "#6a3d9a"),
            ("delta_max_temp", "Delta Max Temp", "#ff7f0e"),
            ("delta_min_clearance", "Delta Min Clearance", "#1f77b4"),
        ]

        plotted = 0
        delta_series_for_constant_check: List[np.ndarray] = []
        for col, label, color in delta_specs:
            values = _to_float_series(df, col)
            if values is None:
                if col == "delta_penalty" and penalty_total is not None:
                    values = np.insert(np.diff(penalty_total), 0, 0.0)
                else:
                    continue
            values = np.nan_to_num(values, nan=0.0)
            ax.plot(x_ticks, values, marker="o", linewidth=1.8, color=color, label=label)
            delta_series_for_constant_check.append(values)
            plotted += 1

        ax.axhline(0.0, color="gray", linestyle="--", linewidth=1.0)
        ax.set_title("Per-Iteration Deltas")
        ax.set_xlabel("Iteration")
        ax.set_ylabel("Delta Value")
        ax.grid(True, alpha=0.25)

        if plotted == 0:
            ax.text(0.5, 0.5, "No delta metrics available", transform=ax.transAxes,
                    ha="center", va="center", fontsize=10, color="gray")
        else:
            if delta_series_for_constant_check and all(
                _is_constant_series(series) for series in delta_series_for_constant_check
            ):
                ax.text(
                    0.03,
                    0.92,
                    "Delta metrics near zero",
                    transform=ax.transAxes,
                    fontsize=10,
                    color="gray",
                    bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.8),
                )
            ax.legend(loc="best", fontsize=8)

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
        logger.info("演化轨迹图生成成功")

    except Exception as e:
        error_msg = f"生成演化轨迹图失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise VisualizationError(error_msg) from e


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
            env_pos, env_dims = _envelope_bounds(design_state)
            draw_box(
                ax,
                env_pos,
                env_dims,
                color='lightgray',
                alpha=0.05,
                label='Envelope'
            )

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
            pos = _component_min_corner(comp)
            dims = [comp.dimensions.x, comp.dimensions.y, comp.dimensions.z]

            # 获取温度并映射到颜色
            temp = thermal_data.get(comp.id, min_temp)
            normalized_temp = (temp - min_temp) / temp_range
            color = plt.cm.hot(normalized_temp)

            draw_box(ax, pos, dims, color=color, alpha=0.7, label=f"{comp.id}\n{temp:.1f}°C")

        # 设置3D轴范围，避免因数据稀疏导致图像压缩
        x_points: List[float] = []
        y_points: List[float] = []
        z_points: List[float] = []
        for comp in design_state.components:
            pos = _component_min_corner(comp)
            dims = [comp.dimensions.x, comp.dimensions.y, comp.dimensions.z]
            x_points.extend([pos[0], pos[0] + dims[0]])
            y_points.extend([pos[1], pos[1] + dims[1]])
            z_points.extend([pos[2], pos[2] + dims[2]])
        if design_state.envelope:
            env_pos, env_dims = _envelope_bounds(design_state)
            x_points.extend([env_pos[0], env_pos[0] + env_dims[0]])
            y_points.extend([env_pos[1], env_pos[1] + env_dims[1]])
            z_points.extend([env_pos[2], env_pos[2] + env_dims[2]])
        if x_points and y_points and z_points:
            x_min, x_max = min(x_points), max(x_points)
            y_min, y_max = min(y_points), max(y_points)
            z_min, z_max = min(z_points), max(z_points)
            span = max(x_max - x_min, y_max - y_min, z_max - z_min, 1.0)
            cx = (x_min + x_max) / 2.0
            cy = (y_min + y_max) / 2.0
            cz = (z_min + z_max) / 2.0
            half = span / 2.0 + span * 0.08
            ax.set_xlim(cx - half, cx + half)
            ax.set_ylim(cy - half, cy + half)
            ax.set_zlim(cz - half, cz + half)

        ax.set_xlabel('X (mm)')
        ax.set_ylabel('Y (mm)')
        ax.set_zlabel('Z (mm)')
        ax.set_title('3D Thermal Distribution')
        ax.view_init(elev=20, azim=45)

        # 创建2D俯视图热图
        ax2 = fig.add_subplot(122)

        # 创建网格
        if design_state.envelope:
            env_pos, env_dims = _envelope_bounds(design_state)
            x_min, y_min = env_pos[0], env_pos[1]
            x_max, y_max = env_pos[0] + env_dims[0], env_pos[1] + env_dims[1]
        else:
            min_corner = np.array([np.inf, np.inf], dtype=float)
            max_corner = np.array([-np.inf, -np.inf], dtype=float)
            for comp in design_state.components:
                cmin = np.array(_component_min_corner(comp)[:2], dtype=float)
                cmax = cmin + np.array([comp.dimensions.x, comp.dimensions.y], dtype=float)
                min_corner = np.minimum(min_corner, cmin)
                max_corner = np.maximum(max_corner, cmax)
            if not np.isfinite(min_corner).all():
                min_corner = np.array([-100.0, -100.0], dtype=float)
                max_corner = np.array([100.0, 100.0], dtype=float)
            x_min, y_min = float(min_corner[0]), float(min_corner[1])
            x_max, y_max = float(max_corner[0]), float(max_corner[1])

        grid_size = 120
        x_grid = np.linspace(x_min, x_max, grid_size)
        y_grid = np.linspace(y_min, y_max, grid_size)
        X, Y = np.meshgrid(x_grid, y_grid)
        weighted_temp = np.zeros_like(X, dtype=float)
        weights = np.zeros_like(X, dtype=float)

        # 使用高斯核构建平滑二维热代理场（确定性）
        for comp in design_state.components:
            cx = float(comp.position.x)
            cy = float(comp.position.y)
            temp = float(thermal_data.get(comp.id, min_temp))
            sigma = max(float(max(comp.dimensions.x, comp.dimensions.y)) * 0.55, 5.0)
            influence = np.exp(-(((X - cx) ** 2 + (Y - cy) ** 2) / (2.0 * sigma ** 2)))
            weighted_temp += influence * temp
            weights += influence

        Z = np.where(weights > 1e-9, weighted_temp / weights, min_temp)

        # 绘制热图
        im = ax2.contourf(X, Y, Z, levels=24, cmap='hot')
        plt.colorbar(im, ax=ax2, label='Temperature (°C)')

        # 绘制组件边界与标签
        for comp in design_state.components:
            min_pos = _component_min_corner(comp)
            rect = patches.Rectangle(
                (min_pos[0], min_pos[1]),
                comp.dimensions.x, comp.dimensions.y,
                linewidth=1, edgecolor='cyan', facecolor='none'
            )
            ax2.add_patch(rect)

            temp = float(thermal_data.get(comp.id, min_temp))
            ax2.text(
                float(comp.position.x),
                float(comp.position.y),
                f"{comp.id}\n{temp:.1f}°C",
                ha='center',
                va='center',
                fontsize=7,
                color='white',
                weight='bold'
            )

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
    logger.info(f"开始生成可视化: {experiment_dir}")

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

    # 2. 布局与热图（需要设计状态文件）
    import glob
    import json
    from core.protocol import DesignState

    design_files = glob.glob(os.path.join(experiment_dir, 'design_state_iter_*.json'))

    def _iteration_from_filename(path: str) -> int:
        stem = Path(path).stem
        try:
            return int(stem.split("_")[-1])
        except Exception:
            return -1

    initial_state = None
    final_state = None
    thermal_data: Dict[str, float] = {}

    if design_files:
        try:
            design_files = sorted(design_files, key=_iteration_from_filename)
            first_file = design_files[0]
            latest_file = design_files[-1]

            with open(first_file, 'r', encoding='utf-8') as f:
                initial_data = json.load(f)
            with open(latest_file, 'r', encoding='utf-8') as f:
                latest_data = json.load(f)

            initial_state = DesignState(**initial_data)
            final_state = DesignState(**latest_data)

            # 最终3D布局图
            output_path = os.path.join(viz_dir, 'final_layout_3d.png')
            plot_3d_layout(final_state, output_path)
            print(f"  [OK] 3D布局图: {output_path}")

            # 布局演化图（初始 vs 最终）
            output_path = os.path.join(viz_dir, 'layout_evolution.png')
            plot_layout_evolution(initial_state, final_state, output_path)
            print(f"  [OK] 布局演化图: {output_path}")

            # 热代理图（确定性，基于功率密度）
            thermal_data = build_power_density_proxy(final_state, csv_path=csv_path)
            output_path = os.path.join(viz_dir, 'thermal_heatmap.png')
            plot_thermal_heatmap(final_state, thermal_data, output_path)
            print(f"  [OK] 热图: {output_path}")

        except Exception as e:
            print(f"  [FAIL] 可视化生成失败: {e}")
            logger.error(f"可视化生成失败: {e}", exc_info=True)

    # 3. 可视化摘要文本
    try:
        summary_text = _build_visualization_summary(
            csv_path=csv_path,
            initial_state=initial_state,
            final_state=final_state,
            thermal_data=thermal_data if thermal_data else None,
        )
        summary_path = os.path.join(viz_dir, "visualization_summary.txt")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(summary_text + "\n")
        print(f"  [OK] 可视化摘要: {summary_path}")
    except Exception as e:
        print(f"  [FAIL] 可视化摘要生成失败: {e}")
        logger.error(f"可视化摘要生成失败: {e}", exc_info=True)

    print("可视化生成完成")
    logger.info("可视化生成完成")


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        experiment_dir = sys.argv[1]
        generate_visualizations(experiment_dir)
    else:
        print("用法: python visualization.py <experiment_dir>")
