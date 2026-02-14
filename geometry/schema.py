"""
几何数据结构定义

从layout3dcube迁移并适配统一协议
"""

from dataclasses import dataclass
from typing import Tuple, Optional, List
import numpy as np


@dataclass
class AABB:
    """轴对齐包围盒 (Axis-Aligned Bounding Box)"""
    min: np.ndarray  # [x, y, z] 最小点
    max: np.ndarray  # [x, y, z] 最大点

    def __post_init__(self):
        """确保min和max是numpy数组"""
        if not isinstance(self.min, np.ndarray):
            self.min = np.array(self.min, dtype=float)
        if not isinstance(self.max, np.ndarray):
            self.max = np.array(self.max, dtype=float)

    def volume(self) -> float:
        """计算体积"""
        size = self.max - self.min
        return float(np.prod(size))

    def min_edge(self) -> float:
        """获取最小边长"""
        size = self.max - self.min
        return float(np.min(size))

    def center(self) -> np.ndarray:
        """获取中心点"""
        return (self.min + self.max) / 2.0

    def size(self) -> np.ndarray:
        """获取尺寸"""
        return self.max - self.min

    def clone(self) -> 'AABB':
        """克隆AABB"""
        return AABB(min=self.min.copy(), max=self.max.copy())

    def intersects(self, other: 'AABB') -> bool:
        """检查是否与另一个AABB相交"""
        return np.all(self.min < other.max) and np.all(other.min < self.max)


@dataclass
class Part:
    """
    设备件（合并了原Part和PlacedPart）
    """
    id: str                    # 唯一标识
    dims: Tuple[float, float, float]  # (x, y, z) 实际尺寸，单位 mm
    mass: float                # 质量，单位 kg
    power: float               # 功率，单位 W
    category: str              # 类别
    color: Tuple[int, int, int, int]  # RGBA 颜色
    clearance_mm: float = 0.0  # 间隙（mm）

    # 放置相关属性（未放置时为None或默认值）
    position: Optional[np.ndarray] = None  # 全局坐标 [x, y, z]（最小点）
    bin_index: int = -1        # 所在子容器索引（-1表示未放置）
    mount_face: Optional[int] = None  # 安装面（0~5，None表示未放置）
    mount_point: Optional[np.ndarray] = None  # 安装位点（安装面中点）

    def __post_init__(self):
        """确保数组类型正确"""
        if self.position is not None and not isinstance(self.position, np.ndarray):
            self.position = np.array(self.position, dtype=float)
        if self.mount_point is not None and not isinstance(self.mount_point, np.ndarray):
            self.mount_point = np.array(self.mount_point, dtype=float)

    def get_actual_dims(self) -> np.ndarray:
        """返回实际尺寸"""
        return np.array(self.dims, dtype=float)

    def get_install_dims(self, face_id: int) -> np.ndarray:
        """
        根据安装面计算安装尺寸

        规则：
        - 安装面方向：实际尺寸 + clearance_mm/2（半个间隙）
        - 其他两个方向：实际尺寸 + clearance_mm（完整间隙）

        安装面约定：
        0: -X面, 1: +X面, 2: -Y面, 3: +Y面, 4: -Z面, 5: +Z面
        """
        actual_dims = np.array(self.dims, dtype=float)
        half_clearance = self.clearance_mm / 2.0
        full_clearance = self.clearance_mm

        # 安装面对应的轴索引：0,1->X轴(0), 2,3->Y轴(1), 4,5->Z轴(2)
        mount_axis = face_id // 2

        # 创建间隙数组：安装面方向+半个间隙，其他方向+完整间隙
        clearance_array = np.full(3, full_clearance)
        clearance_array[mount_axis] = half_clearance

        return actual_dims + clearance_array

    def get_actual_position(self) -> np.ndarray:
        """
        根据安装坐标（position）和安装面，计算实际部件的最小角坐标

        逻辑：
        - position 存储的是使用安装尺寸时的坐标（包含间隙）
        - 对于负方向面（-X, -Y, -Z）：实际部件直接贴在墙上，安装面方向不加偏移
        - 对于正方向面（+X, +Y, +Z）：实际部件需要在安装面方向加半个间隙
        - 所有方向的非安装面方向都加完整间隙

        返回：
        实际部件的最小角坐标（用于CAD输出和可视化）
        """
        if self.position is None or self.mount_face is None:
            raise ValueError(f"Part {self.id} 未放置，无法计算实际坐标")

        install_pos = np.array(self.position, dtype=float)
        half_clearance = self.clearance_mm / 2.0
        full_clearance = self.clearance_mm

        # 安装面对应的轴索引
        mount_axis = self.mount_face // 2
        is_negative_face = (self.mount_face % 2 == 0)  # 负方向面

        # 创建偏移量数组
        offset = np.full(3, full_clearance)  # 默认所有方向都加完整间隙

        if is_negative_face:
            # 负方向面：安装面方向不加偏移（直接贴墙）
            offset[mount_axis] = 0.0
        else:
            # 正方向面：安装面方向加半个间隙
            offset[mount_axis] = half_clearance

        # 实际坐标 = 安装坐标 + 偏移量
        actual_pos = install_pos + offset

        return actual_pos

    def compute_mount_point(self, face_id: int, position: np.ndarray) -> np.ndarray:
        """
        计算安装位点（安装面的中心点）

        参数:
            face_id: 安装面ID（0~5）
            position: 全局坐标最小点 [x, y, z]

        返回:
            安装位点坐标 [x, y, z]
        """
        pos = np.array(position, dtype=float)
        dims = np.array(self.dims, dtype=float)

        # 安装面对应的轴索引和方向
        mount_axis = face_id // 2
        is_positive = face_id % 2 == 1  # True表示正方向（+X/+Y/+Z）

        mount_point = pos.copy()

        # 安装面方向：使用最小点或最大点
        if is_positive:
            mount_point[mount_axis] = pos[mount_axis] + dims[mount_axis]
        else:
            mount_point[mount_axis] = pos[mount_axis]

        # 其他两个方向：使用中点
        for axis in range(3):
            if axis != mount_axis:
                mount_point[axis] = pos[axis] + dims[axis] / 2.0

        return mount_point


@dataclass
class EnvelopeGeometry:
    """包含外壳与内部可用空间的舱体描述"""
    outer: AABB              # 外壳AABB（实体外表面）
    inner: AABB              # 内部可用AABB（扣除厚度后）
    thickness_mm: float      # 壁厚
    fill_ratio: float        # 占空比
    size_ratio: Tuple[float, float, float]  # 计算外壳时的比例

    def outer_size(self) -> np.ndarray:
        return self.outer.size()

    def inner_size(self) -> np.ndarray:
        return self.inner.size()


@dataclass
class PackingResult:
    """装箱结果"""
    placed: List[Part]       # 已放置的部件
    unplaced: List[Part]     # 未放置的部件
    bins_used: int           # 使用的子容器数量
    total_volume: float      # 已放置部件的总体积
    overlap_count: int       # 重叠数量（应为0）
    score: Tuple[int, int, float, int]  # 评分元组


def generate_category_color(category: str) -> Tuple[int, int, int, int]:
    """
    根据类别生成颜色

    Args:
        category: 类别名称

    Returns:
        RGBA颜色元组
    """
    color_map = {
        'payload': (100, 150, 255, 200),    # 蓝色
        'avionics': (255, 200, 100, 200),   # 橙色
        'power': (100, 255, 150, 200),      # 绿色
        'thermal': (255, 100, 100, 200),    # 红色
        'structure': (150, 150, 150, 200),  # 灰色
        'communication': (255, 150, 255, 200),  # 紫色
    }
    return color_map.get(category.lower(), (200, 200, 200, 200))
