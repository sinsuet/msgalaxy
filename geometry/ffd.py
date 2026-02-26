#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
3D自由变形(FFD - Free-Form Deformation)模块

实现基于控制点网格的3D几何变形功能,用于参数化几何优化。

参考: AutoFlowSim项目的3D-Free-Form-Deformation模块
"""

import numpy as np
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass

from core.logger import get_logger
from core.exceptions import GeometryError

logger = get_logger("ffd")


@dataclass
class ControlPoint:
    """FFD控制点"""
    x: float
    y: float
    z: float
    index: Tuple[int, int, int]  # (i, j, k) 在控制网格中的索引


@dataclass
class FFDLattice:
    """FFD控制网格"""
    nx: int  # x方向控制点数量
    ny: int  # y方向控制点数量
    nz: int  # z方向控制点数量
    origin: np.ndarray  # 网格原点 [x, y, z]
    size: np.ndarray  # 网格尺寸 [dx, dy, dz]
    control_points: np.ndarray  # 控制点坐标 (nx, ny, nz, 3)


class FFDDeformer:
    """
    3D自由变形器

    使用Bernstein多项式实现基于控制点网格的几何变形。

    原理:
    1. 定义包围几何体的控制点网格(lattice)
    2. 将几何体顶点映射到参数空间(u,v,w) ∈ [0,1]³
    3. 移动控制点
    4. 使用Bernstein多项式插值计算新的顶点位置

    公式:
    P(u,v,w) = Σ Σ Σ B_i^l(u) * B_j^m(v) * B_k^n(w) * P_ijk
    其中 B_i^n(t) = C(n,i) * t^i * (1-t)^(n-i) 是Bernstein基函数
    """

    def __init__(self, nx: int = 3, ny: int = 3, nz: int = 3):
        """
        初始化FFD变形器

        Args:
            nx: x方向控制点数量
            ny: y方向控制点数量
            nz: z方向控制点数量
        """
        if nx < 2 or ny < 2 or nz < 2:
            raise GeometryError("控制点数量必须至少为2")

        self.nx = nx
        self.ny = ny
        self.nz = nz
        self.lattice: Optional[FFDLattice] = None

        logger.info(f"FFD变形器初始化: {nx}x{ny}x{nz} 控制点网格")

    def create_lattice(self, bbox_min: np.ndarray, bbox_max: np.ndarray,
                       margin: float = 0.1) -> FFDLattice:
        """
        创建FFD控制网格

        Args:
            bbox_min: 几何体包围盒最小点 [x_min, y_min, z_min]
            bbox_max: 几何体包围盒最大点 [x_max, y_max, z_max]
            margin: 网格边界扩展比例(相对于包围盒尺寸)

        Returns:
            FFDLattice: 控制网格
        """
        # 计算包围盒尺寸
        bbox_size = bbox_max - bbox_min

        # 扩展网格边界
        origin = bbox_min - margin * bbox_size
        size = bbox_size * (1 + 2 * margin)

        # 初始化控制点网格
        control_points = np.zeros((self.nx, self.ny, self.nz, 3))

        # 均匀分布控制点
        for i in range(self.nx):
            for j in range(self.ny):
                for k in range(self.nz):
                    u = i / (self.nx - 1) if self.nx > 1 else 0.5
                    v = j / (self.ny - 1) if self.ny > 1 else 0.5
                    w = k / (self.nz - 1) if self.nz > 1 else 0.5

                    control_points[i, j, k] = origin + size * np.array([u, v, w])

        self.lattice = FFDLattice(
            nx=self.nx,
            ny=self.ny,
            nz=self.nz,
            origin=origin,
            size=size,
            control_points=control_points
        )

        logger.info(f"FFD网格创建完成: 原点={origin}, 尺寸={size}")
        return self.lattice

    def world_to_parametric(self, points: np.ndarray) -> np.ndarray:
        """
        将世界坐标转换为参数空间坐标

        Args:
            points: 世界坐标点 (N, 3)

        Returns:
            参数空间坐标 (N, 3), 每个坐标 (u,v,w) ∈ [0,1]³
        """
        if self.lattice is None:
            raise GeometryError("FFD网格未初始化")

        # 归一化到[0,1]
        parametric = (points - self.lattice.origin) / self.lattice.size

        return parametric

    def parametric_to_world(self, parametric: np.ndarray) -> np.ndarray:
        """
        将参数空间坐标转换为世界坐标(使用当前控制点)

        Args:
            parametric: 参数空间坐标 (N, 3)

        Returns:
            世界坐标点 (N, 3)
        """
        if self.lattice is None:
            raise GeometryError("FFD网格未初始化")

        N = parametric.shape[0]
        world_points = np.zeros((N, 3))

        # 对每个点进行Bernstein插值
        for idx in range(N):
            u, v, w = parametric[idx]

            # 计算Bernstein基函数
            point = np.zeros(3)
            for i in range(self.nx):
                for j in range(self.ny):
                    for k in range(self.nz):
                        # Bernstein多项式
                        B_i = self._bernstein(self.nx - 1, i, u)
                        B_j = self._bernstein(self.ny - 1, j, v)
                        B_k = self._bernstein(self.nz - 1, k, w)

                        # 加权求和
                        weight = B_i * B_j * B_k
                        point += weight * self.lattice.control_points[i, j, k]

            world_points[idx] = point

        return world_points

    def deform(self, points: np.ndarray, control_point_displacements: Dict[Tuple[int, int, int], np.ndarray]) -> np.ndarray:
        """
        对点集进行FFD变形

        Args:
            points: 原始点集 (N, 3)
            control_point_displacements: 控制点位移字典 {(i,j,k): [dx, dy, dz]}

        Returns:
            变形后的点集 (N, 3)
        """
        if self.lattice is None:
            raise GeometryError("FFD网格未初始化")

        # 应用控制点位移
        original_control_points = self.lattice.control_points.copy()

        for (i, j, k), displacement in control_point_displacements.items():
            if 0 <= i < self.nx and 0 <= j < self.ny and 0 <= k < self.nz:
                self.lattice.control_points[i, j, k] += displacement
            else:
                logger.warning(f"控制点索引超出范围: ({i},{j},{k})")

        # 转换到参数空间
        parametric = self.world_to_parametric(points)

        # 使用新的控制点计算世界坐标
        deformed_points = self.parametric_to_world(parametric)

        # 恢复原始控制点(如果需要多次变形)
        # self.lattice.control_points = original_control_points

        logger.info(f"FFD变形完成: {len(points)}个点, {len(control_point_displacements)}个控制点移动")

        return deformed_points

    def deform_component(self, component_geometry: Any,
                        control_point_displacements: Dict[Tuple[int, int, int], np.ndarray]) -> Any:
        """
        对组件几何进行FFD变形

        Args:
            component_geometry: 组件几何对象(包含position和dimensions)
            control_point_displacements: 控制点位移字典

        Returns:
            变形后的组件几何对象
        """
        # 获取组件的8个顶点
        pos = np.array([component_geometry.position.x,
                       component_geometry.position.y,
                       component_geometry.position.z])
        dim = np.array([component_geometry.dimensions.x,
                       component_geometry.dimensions.y,
                       component_geometry.dimensions.z])

        # 构建8个顶点
        vertices = np.array([
            pos,
            pos + [dim[0], 0, 0],
            pos + [0, dim[1], 0],
            pos + [0, 0, dim[2]],
            pos + [dim[0], dim[1], 0],
            pos + [dim[0], 0, dim[2]],
            pos + [0, dim[1], dim[2]],
            pos + dim
        ])

        # 变形顶点
        deformed_vertices = self.deform(vertices, control_point_displacements)

        # 计算新的位置和尺寸(使用变形后的包围盒)
        new_min = deformed_vertices.min(axis=0)
        new_max = deformed_vertices.max(axis=0)
        new_dim = new_max - new_min

        # 更新组件几何
        from core.protocol import Vector3D
        component_geometry.position = Vector3D(x=new_min[0], y=new_min[1], z=new_min[2])
        component_geometry.dimensions = Vector3D(x=new_dim[0], y=new_dim[1], z=new_dim[2])

        return component_geometry

    @staticmethod
    def _bernstein(n: int, i: int, t: float) -> float:
        """
        计算Bernstein基函数

        B_i^n(t) = C(n,i) * t^i * (1-t)^(n-i)

        Args:
            n: 多项式阶数
            i: 基函数索引
            t: 参数 ∈ [0,1]

        Returns:
            Bernstein基函数值
        """
        # 二项式系数 C(n,i)
        coeff = FFDDeformer._binomial_coefficient(n, i)

        # Bernstein多项式
        return coeff * (t ** i) * ((1 - t) ** (n - i))

    @staticmethod
    def _binomial_coefficient(n: int, k: int) -> int:
        """
        计算二项式系数 C(n,k) = n! / (k! * (n-k)!)

        Args:
            n: 总数
            k: 选择数

        Returns:
            二项式系数
        """
        if k < 0 or k > n:
            return 0
        if k == 0 or k == n:
            return 1

        # 优化计算
        k = min(k, n - k)
        result = 1
        for i in range(k):
            result = result * (n - i) // (i + 1)

        return result

    def get_control_point(self, i: int, j: int, k: int) -> np.ndarray:
        """
        获取控制点坐标

        Args:
            i, j, k: 控制点索引

        Returns:
            控制点坐标 [x, y, z]
        """
        if self.lattice is None:
            raise GeometryError("FFD网格未初始化")

        if not (0 <= i < self.nx and 0 <= j < self.ny and 0 <= k < self.nz):
            raise GeometryError(f"控制点索引超出范围: ({i},{j},{k})")

        return self.lattice.control_points[i, j, k].copy()

    def set_control_point(self, i: int, j: int, k: int, position: np.ndarray):
        """
        设置控制点坐标

        Args:
            i, j, k: 控制点索引
            position: 新的控制点坐标 [x, y, z]
        """
        if self.lattice is None:
            raise GeometryError("FFD网格未初始化")

        if not (0 <= i < self.nx and 0 <= j < self.ny and 0 <= k < self.nz):
            raise GeometryError(f"控制点索引超出范围: ({i},{j},{k})")

        self.lattice.control_points[i, j, k] = position

    def get_lattice_info(self) -> Dict[str, Any]:
        """
        获取FFD网格信息

        Returns:
            网格信息字典
        """
        if self.lattice is None:
            return {"initialized": False}

        return {
            "initialized": True,
            "nx": self.nx,
            "ny": self.ny,
            "nz": self.nz,
            "total_control_points": self.nx * self.ny * self.nz,
            "origin": self.lattice.origin.tolist(),
            "size": self.lattice.size.tolist(),
            "control_points_shape": self.lattice.control_points.shape
        }


# ============ 辅助函数 ============

def create_simple_deformation(deformer: FFDDeformer,
                             axis: str = 'z',
                             magnitude: float = 10.0) -> Dict[Tuple[int, int, int], np.ndarray]:
    """
    创建简单的单轴变形

    Args:
        deformer: FFD变形器
        axis: 变形轴 ('x', 'y', 'z')
        magnitude: 变形幅度(mm)

    Returns:
        控制点位移字典
    """
    if deformer.lattice is None:
        raise GeometryError("FFD网格未初始化")

    displacements = {}
    axis_idx = {'x': 0, 'y': 1, 'z': 2}[axis.lower()]

    # 只移动顶层控制点
    for i in range(deformer.nx):
        for j in range(deformer.ny):
            k = deformer.nz - 1  # 顶层
            displacement = np.zeros(3)
            displacement[axis_idx] = magnitude
            displacements[(i, j, k)] = displacement

    return displacements


def create_taper_deformation(deformer: FFDDeformer,
                            axis: str = 'z',
                            taper_ratio: float = 0.8) -> Dict[Tuple[int, int, int], np.ndarray]:
    """
    创建锥形变形

    Args:
        deformer: FFD变形器
        axis: 锥形轴 ('x', 'y', 'z')
        taper_ratio: 顶部缩放比例

    Returns:
        控制点位移字典
    """
    if deformer.lattice is None:
        raise GeometryError("FFD网格未初始化")

    displacements = {}

    # 沿指定轴线性缩放
    for i in range(deformer.nx):
        for j in range(deformer.ny):
            for k in range(deformer.nz):
                # 计算沿轴的位置比例
                if axis.lower() == 'z':
                    ratio = k / (deformer.nz - 1) if deformer.nz > 1 else 0.5
                    scale = 1.0 + (taper_ratio - 1.0) * ratio

                    # 计算中心
                    center_x = (deformer.lattice.control_points[:, j, k, 0].mean())
                    center_y = (deformer.lattice.control_points[i, :, k, 1].mean())

                    # 向中心缩放
                    current_pos = deformer.lattice.control_points[i, j, k]
                    displacement = np.array([
                        (current_pos[0] - center_x) * (scale - 1.0),
                        (current_pos[1] - center_y) * (scale - 1.0),
                        0.0
                    ])

                    displacements[(i, j, k)] = displacement

    return displacements
