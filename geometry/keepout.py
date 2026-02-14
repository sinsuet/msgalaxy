"""
AABB六面切分算法 - Keep-out区域处理

从layout3dcube迁移
"""

from typing import List, Dict, Any
import numpy as np
from geometry.schema import AABB, EnvelopeGeometry, Part


def boxes_overlap(A: AABB, B: AABB) -> bool:
    """判断两个AABB是否重叠"""
    return (A.min[0] < B.max[0] and A.max[0] > B.min[0] and
            A.min[1] < B.max[1] and A.max[1] > B.min[1] and
            A.min[2] < B.max[2] and A.max[2] > B.min[2])


def intersect_box(A: AABB, B: AABB) -> AABB:
    """计算两个AABB的交集"""
    i_min = np.maximum(A.min, B.min)
    i_max = np.minimum(A.max, B.max)
    return AABB(min=i_min, max=i_max)


def subtract_box(A: AABB, B: AABB) -> List[AABB]:
    """
    AABB盒差：从A中减去B，返回剩余的AABB列表
    使用六面切分法

    参数:
        A: 被减盒
        B: 减去的盒

    返回:
        切分后的AABB列表（最多6个）
    """
    # 如果不重叠，直接返回A
    if not boxes_overlap(A, B):
        return [A]

    # 计算交集
    I = intersect_box(A, B)

    pieces = []

    # X轴方向：左片和右片
    if A.min[0] < I.min[0]:  # 左片
        pieces.append(AABB(
            min=np.array([A.min[0], A.min[1], A.min[2]]),
            max=np.array([I.min[0], A.max[1], A.max[2]])
        ))

    if I.max[0] < A.max[0]:  # 右片
        pieces.append(AABB(
            min=np.array([I.max[0], A.min[1], A.min[2]]),
            max=np.array([A.max[0], A.max[1], A.max[2]])
        ))

    # Y轴方向：前片和后片（仅在X方向交集内）
    if A.min[1] < I.min[1]:  # 前片
        pieces.append(AABB(
            min=np.array([I.min[0], A.min[1], A.min[2]]),
            max=np.array([I.max[0], I.min[1], A.max[2]])
        ))

    if I.max[1] < A.max[1]:  # 后片
        pieces.append(AABB(
            min=np.array([I.min[0], I.max[1], A.min[2]]),
            max=np.array([I.max[0], A.max[1], A.max[2]])
        ))

    # Z轴方向：底片和顶片（仅在X-Y交集内）
    if A.min[2] < I.min[2]:  # 底片
        pieces.append(AABB(
            min=np.array([I.min[0], I.min[1], A.min[2]]),
            max=np.array([I.max[0], I.max[1], I.min[2]])
        ))

    if I.max[2] < A.max[2]:  # 顶片
        pieces.append(AABB(
            min=np.array([I.min[0], I.min[1], I.max[2]]),
            max=np.array([I.max[0], I.max[1], A.max[2]])
        ))

    # 过滤体积为0的盒子
    pieces = [p for p in pieces if p.volume() > 1e-6]

    return pieces


def build_bins(envelope: AABB, keepouts: List[AABB], min_edge_threshold: float = 5.0) -> List[AABB]:
    """
    构建可用子容器列表

    参数:
        envelope: 舱体AABB
        keepouts: 禁区AABB列表
        min_edge_threshold: 最小边长阈值（mm），小于此值的碎片将被过滤

    返回:
        可用子容器AABB列表
    """
    bins = [envelope]

    # 逐个禁区切分
    for i, ko in enumerate(keepouts):
        new_bins = []
        for b in bins:
            pieces = subtract_box(b, ko)
            new_bins.extend(pieces)
        bins = new_bins
        print(f"  处理禁区 {i+1}/{len(keepouts)}: 当前子容器数 = {len(bins)}")

    # 过滤过小的碎片
    bins_filtered = [b for b in bins if b.min_edge() >= min_edge_threshold]

    print(f"切分完成: 总子容器数 = {len(bins)}, 过滤后 = {len(bins_filtered)}")
    print(f"  子容器总体积: {sum(b.volume() for b in bins_filtered):.0f} mm^3")

    return bins_filtered


def build_envelope(cfg: Dict[str, Any], parts: List[Part]) -> EnvelopeGeometry:
    """
    根据配置和BOM创建外壳+内部可用AABB

    Args:
        cfg: 配置字典
        parts: 部件列表

    Returns:
        EnvelopeGeometry对象
    """
    env_cfg = cfg.get('envelope', {})
    thickness = float(env_cfg.get('shell_thickness_mm', 1.0))
    fill_ratio = float(env_cfg.get('fill_ratio', 0.6))
    ratio = np.array(env_cfg.get('size_ratio', [1.0, 1.0, 1.0]), dtype=float)
    auto = bool(env_cfg.get('auto_envelope', False))

    if auto:
        # 自动计算外壳尺寸
        parts_volume = sum(np.prod(p.get_actual_dims()) for p in parts)
        target_volume = parts_volume / max(fill_ratio, 1e-6)
        base = np.prod(ratio)
        scale = (target_volume / max(base, 1e-6)) ** (1.0 / 3.0)
        size = ratio * scale
        env_cfg['size_mm'] = size.tolist()
    else:
        size = np.array(env_cfg['size_mm'], dtype=float)

    # 计算内部尺寸
    inner_size = size - 2 * thickness
    if np.any(inner_size <= 0):
        raise ValueError("壳体厚度过大，内部尺寸为负")

    # 确定原点位置
    origin = env_cfg.get('origin', 'center')
    if origin == 'center':
        outer_min = -size / 2.0
        outer_max = size / 2.0
    else:
        outer_min = np.array([0.0, 0.0, 0.0])
        outer_max = size

    # 计算内部AABB
    offset = np.array([thickness, thickness, thickness])
    inner_min = outer_min + offset
    inner_max = outer_max - offset

    outer_aabb = AABB(min=outer_min, max=outer_max)
    inner_aabb = AABB(min=inner_min, max=inner_max)

    return EnvelopeGeometry(
        outer=outer_aabb,
        inner=inner_aabb,
        thickness_mm=thickness,
        fill_ratio=fill_ratio,
        size_ratio=tuple(ratio.tolist())
    )


def create_keepout_aabbs(cfg: Dict[str, Any]) -> List[AABB]:
    """
    根据配置创建禁区AABB列表

    Args:
        cfg: 配置字典

    Returns:
        禁区AABB列表
    """
    keepouts = []
    if cfg.get('keep_out', None) is not None:
        for ko in cfg.get('keep_out', []):
            if 'min_mm' in ko and 'max_mm' in ko:
                min_pt = np.array(ko['min_mm'], dtype=float)
                max_pt = np.array(ko['max_mm'], dtype=float)
                keepouts.append(AABB(min=min_pt, max=max_pt))
    return keepouts
