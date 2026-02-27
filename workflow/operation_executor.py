"""
操作执行器模块

负责执行LLM提出的各种几何操作，包括：
- MOVE: 移动组件
- ROTATE: 旋转组件
- SWAP: 交换组件位置
- DEFORM: FFD自由变形
- REPACK: 重新装箱
"""

import copy
import numpy as np
from typing import Dict, Any, List
from pathlib import Path

from core.protocol import DesignState, Vector3D
from core.logger import get_logger
from geometry.ffd import FFDDeformer

logger = get_logger(__name__)


class OperationExecutor:
    """操作执行器"""

    def __init__(self, layout_engine=None):
        """
        初始化操作执行器

        Args:
            layout_engine: 布局引擎（用于REPACK操作）
        """
        self.layout_engine = layout_engine

    def execute_plan(self, execution_plan, current_state: DesignState) -> DesignState:
        """
        执行优化计划

        Args:
            execution_plan: 执行计划（包含多个Agent的提案）
            current_state: 当前设计状态

        Returns:
            新的设计状态
        """
        # 深拷贝当前状态
        new_state = copy.deepcopy(current_state)

        # 如果execution_plan为空或没有actions，直接返回
        if not execution_plan or not hasattr(execution_plan, 'geometry_proposal'):
            logger.warning("执行计划为空或无几何操作")
            return new_state

        # 提取几何操作
        geometry_proposal = execution_plan.geometry_proposal
        if not geometry_proposal or not geometry_proposal.actions:
            logger.info("无几何操作需要执行")
            return new_state

        # 执行每个操作
        for action in geometry_proposal.actions:
            try:
                op_type = action.op_type
                component_id = action.component_id
                parameters = action.parameters

                logger.info(f"  执行操作: {op_type} on {component_id}")

                # 查找目标组件
                comp_idx = self._find_component(new_state, component_id)
                if comp_idx is None:
                    logger.warning(f"  组件 {component_id} 未找到，跳过")
                    continue

                # 执行不同类型的操作
                if op_type == "MOVE":
                    self._execute_move(new_state, comp_idx, parameters)
                elif op_type == "ROTATE":
                    self._execute_rotate(new_state, comp_idx, parameters)
                elif op_type == "SWAP":
                    self._execute_swap(new_state, comp_idx, parameters)
                elif op_type == "DEFORM":
                    self._execute_deform(new_state, comp_idx, parameters)
                elif op_type == "REPACK":
                    self._execute_repack(new_state, parameters)
                else:
                    logger.warning(f"  未知操作类型: {op_type}")

            except Exception as e:
                logger.error(f"  执行操作失败: {e}", exc_info=True)
                continue

        # 更新迭代次数
        new_state.iteration = current_state.iteration + 1

        return new_state

    def _find_component(self, state: DesignState, component_id: str) -> int:
        """查找组件索引"""
        for idx, comp in enumerate(state.components):
            if comp.id == component_id:
                return idx
        return None

    def _execute_move(self, state: DesignState, comp_idx: int, parameters: Dict[str, Any]):
        """执行移动操作"""
        axis = parameters.get("axis", "X")
        move_range = parameters.get("range", [0, 0])
        # 取范围中点作为移动距离
        delta = (move_range[0] + move_range[1]) / 2.0

        if axis == "X":
            state.components[comp_idx].position.x += delta
        elif axis == "Y":
            state.components[comp_idx].position.y += delta
        elif axis == "Z":
            state.components[comp_idx].position.z += delta

        logger.info(f"    移动 {axis} 轴 {delta:.2f} mm")

    def _execute_rotate(self, state: DesignState, comp_idx: int, parameters: Dict[str, Any]):
        """执行旋转操作"""
        axis = parameters.get("axis", "Z")
        angle_range = parameters.get("angle_range", [0, 0])
        angle = (angle_range[0] + angle_range[1]) / 2.0

        if axis == "X":
            state.components[comp_idx].rotation.x += angle
        elif axis == "Y":
            state.components[comp_idx].rotation.y += angle
        elif axis == "Z":
            state.components[comp_idx].rotation.z += angle

        logger.info(f"    旋转 {axis} 轴 {angle:.2f} 度")

    def _execute_swap(self, state: DesignState, comp_idx: int, parameters: Dict[str, Any]):
        """执行交换操作"""
        component_b = parameters.get("component_b")
        comp_b_idx = self._find_component(state, component_b)

        if comp_b_idx is not None:
            # 交换位置
            pos_a = state.components[comp_idx].position
            pos_b = state.components[comp_b_idx].position
            state.components[comp_idx].position = pos_b
            state.components[comp_b_idx].position = pos_a
            logger.info(f"    交换 {state.components[comp_idx].id} 和 {component_b} 的位置")
        else:
            logger.warning(f"    组件 {component_b} 未找到，跳过交换")

    def _execute_deform(self, state: DesignState, comp_idx: int, parameters: Dict[str, Any]):
        """执行FFD变形操作"""
        deform_type = parameters.get("deform_type", "stretch_z")
        magnitude = parameters.get("magnitude", 10.0)

        logger.info(f"    FFD变形: {deform_type}, 幅度 {magnitude:.2f} mm")

        # 获取组件的包围盒
        comp = state.components[comp_idx]
        pos = comp.position
        dim = comp.dimensions

        # 计算包围盒
        bbox_min = np.array([
            pos.x - dim.x / 2,
            pos.y - dim.y / 2,
            pos.z - dim.z / 2
        ])
        bbox_max = np.array([
            pos.x + dim.x / 2,
            pos.y + dim.y / 2,
            pos.z + dim.z / 2
        ])

        # 创建FFD变形器
        ffd = FFDDeformer(nx=3, ny=3, nz=3)
        lattice = ffd.create_lattice(bbox_min, bbox_max, margin=0.1)

        # 根据变形类型设置控制点位移
        displacements = {}

        if deform_type == "stretch_x":
            # 沿X轴拉伸：移动右侧控制点
            for j in range(3):
                for k in range(3):
                    displacements[(2, j, k)] = np.array([magnitude, 0, 0])
            # 更新组件尺寸
            state.components[comp_idx].dimensions.x += magnitude

        elif deform_type == "stretch_y":
            # 沿Y轴拉伸
            for i in range(3):
                for k in range(3):
                    displacements[(i, 2, k)] = np.array([0, magnitude, 0])
            state.components[comp_idx].dimensions.y += magnitude

        elif deform_type == "stretch_z":
            # 沿Z轴拉伸
            for i in range(3):
                for j in range(3):
                    displacements[(i, j, 2)] = np.array([0, 0, magnitude])
            state.components[comp_idx].dimensions.z += magnitude

        elif deform_type == "bulge":
            # 膨胀：所有外侧控制点向外移动
            scale = magnitude / 2.0
            for i in range(3):
                for j in range(3):
                    for k in range(3):
                        if i == 0 or i == 2 or j == 0 or j == 2 or k == 0 or k == 2:
                            # 外侧控制点
                            direction = np.array([
                                (i - 1) * scale,
                                (j - 1) * scale,
                                (k - 1) * scale
                            ])
                            displacements[(i, j, k)] = direction
            # 膨胀会增加所有维度
            state.components[comp_idx].dimensions.x += magnitude * 0.5
            state.components[comp_idx].dimensions.y += magnitude * 0.5
            state.components[comp_idx].dimensions.z += magnitude * 0.5

        logger.info(f"    ✓ FFD变形完成，新尺寸: {state.components[comp_idx].dimensions}")

    def _execute_repack(self, state: DesignState, parameters: Dict[str, Any]):
        """执行重新装箱操作"""
        if self.layout_engine is None:
            logger.warning("    布局引擎未初始化，跳过REPACK操作")
            return

        strategy = parameters.get("strategy", "greedy")
        clearance = parameters.get("clearance", 20.0)

        logger.info(f"    重新装箱: strategy={strategy}, clearance={clearance}")

        # 调用layout_engine重新布局
        # 注意：这会重置所有组件位置
        packing_result = self.layout_engine.generate_layout()

        # 更新组件位置
        for part in packing_result.placed:
            pos = part.get_actual_position()
            for idx, comp in enumerate(state.components):
                if comp.id == part.id:
                    state.components[idx].position = Vector3D(
                        x=float(pos[0]),
                        y=float(pos[1]),
                        z=float(pos[2])
                    )
                    break

        logger.info(f"    ✓ 重新装箱完成")
