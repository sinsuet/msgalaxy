"""
装箱核心 - 基于py3dbp的多启动装箱（多面贴壁 + 切层 + 重合度打分版）

从layout3dcube迁移
"""

import random
from typing import List, Tuple, NamedTuple, Dict

import numpy as np

from geometry.schema import AABB, Part, PackingResult
from py3dbp.constants import RotationType
RotationType.ALL = [RotationType.RT_WHD]  # 只保留一种朝向，等价于"完全不旋转"
from py3dbp import Packer, Bin, Item


# ===========================
# 工具类与辅助数据结构
# ===========================

class PlacedWithFace(NamedTuple):
    """内部使用：带有连接面标记的放置结果"""
    placed: Part
    face_id: int  # 0~5，见 BinFaceMapper 的约定


class BinFaceMapper:
    """
    负责：
    1. 把当前 bin 剩余 3D 空间映射到某个面的 2D 布局板；
    2. 把 2D 布局结果 (u, v) 反映射成 3D min 坐标；
    3. 在一块面布局完成后，按最大厚度沿该面的法向方向切掉一层空间。
    """

    # face_id 约定：
    #   0: -X 面 (x = min_x)，平面 (y,z)
    #   1: +X 面 (x = max_x)，平面 (y,z)
    #   2: -Y 面 (y = min_y)，平面 (x,z)
    #   3: +Y 面 (y = max_y)，平面 (x,z)
    #   4: -Z 面 (z = min_z)，平面 (x,y)
    #   5: +Z 面 (z = max_z)，平面 (x,y)

    @staticmethod
    def _clone_aabb(aabb: AABB) -> AABB:
        """根据 min/max 克隆一个新的 AABB，避免在原始 bins 上原地修改。"""
        return AABB(
            min=np.array(aabb.min, dtype=float).copy(),
            max=np.array(aabb.max, dtype=float).copy(),
        )

    def __init__(self, bin_aabb: AABB) -> None:
        self.original = self._clone_aabb(bin_aabb)
        self.remaining = self._clone_aabb(bin_aabb)

    # ---------- 3D -> 2D: 面板尺寸、part 投影 ----------

    def board_size(self, face_id: int) -> Tuple[float, float]:
        """给定 face_id，在当前 remaining 空间下返回 2D 板尺寸 (W, H)。"""
        size = np.array(self.remaining.size(), dtype=float)  # [dx, dy, dz]
        dx, dy, dz = size
        if face_id in (0, 1):      # ±X: 平面 (y,z)
            return float(dy), float(dz)
        elif face_id in (2, 3):    # ±Y: 平面 (x,z)
            return float(dx), float(dz)
        elif face_id in (4, 5):    # ±Z: 平面 (x,y)
            return float(dx), float(dy)
        else:
            raise ValueError(f"未知 face_id: {face_id}")

    def project_part_dims(self, face_id: int, dims: np.ndarray) -> Tuple[float, float, float]:
        """
        给定 face_id 和 3D 尺寸 (px, py, pz)，返回：
        (L_u, L_v, thickness)，其中：
          - L_u, L_v：在 2D 板上的 footprint 尺寸；
          - thickness：沿该面法向方向的厚度，用于"切层"。
        """
        px, py, pz = map(float, dims)
        if face_id in (4, 5):      # ±Z: 平面 (x,y)，法向为 ±Z，厚度 = pz
            return px, py, pz
        elif face_id in (0, 1):    # ±X: 平面 (y,z)，法向为 ±X，厚度 = px
            return py, pz, px
        elif face_id in (2, 3):    # ±Y: 平面 (x,z)，法向为 ±Y，厚度 = py
            return px, pz, py
        else:
            raise ValueError(f"未知 face_id: {face_id}")

    # ---------- 2D -> 3D: 布局结果反映射 ----------

    def uv_to_world_min(self, face_id: int, u: float, v: float, install_dims: np.ndarray) -> np.ndarray:
        """
        给定 face_id、2D 板上的 min corner (u,v) 以及安装尺寸，
        返回该 part 在世界坐标下的安装坐标（最小点）。

        注意：
        - 这里使用的是"当前剩余空间 remaining"的边界
        - 全程使用安装尺寸，不区分正负方向面
        - 返回的是安装坐标（包含间隙），实际坐标由 Part.get_actual_position() 计算
        """
        rm_min = np.array(self.remaining.min, dtype=float)
        size = np.array(self.remaining.size(), dtype=float)
        rm_max = rm_min + size
        px, py, pz = map(float, install_dims)

        if face_id == 0:  # -X, x = rm_min[0]，平面 (y,z)
            x_min = rm_min[0]
            y_min = rm_min[1] + u
            z_min = rm_min[2] + v
        elif face_id == 1:  # +X, x = rm_max[0] - px
            x_min = rm_max[0] - px
            y_min = rm_min[1] + u
            z_min = rm_min[2] + v

        elif face_id == 2:  # -Y, y = rm_min[1]，平面 (x,z)
            x_min = rm_min[0] + u
            y_min = rm_min[1]
            z_min = rm_min[2] + v
        elif face_id == 3:  # +Y, y = rm_max[1] - py
            x_min = rm_min[0] + u
            y_min = rm_max[1] - py
            z_min = rm_min[2] + v
        elif face_id == 4:  # -Z, 底板 z = rm_min[2]，平面 (x,y)
            x_min = rm_min[0] + u
            y_min = rm_min[1] + v
            z_min = rm_min[2]
        elif face_id == 5:  # +Z, 顶板 z = rm_max[2] - pz
            x_min = rm_min[0] + u
            y_min = rm_min[1] + v
            z_min = rm_max[2] - pz
        else:
            raise ValueError(f"未知 face_id: {face_id}")

        return np.array([x_min, y_min, z_min], dtype=float)

    # ---------- 切层：按该面最大厚度收缩剩余空间 ----------

    def cut_after_face(self, face_id: int, max_thickness: float) -> None:
        """
        某个面布局完成后，沿该面的法向方向切掉一层厚度 max_thickness，
        相当于把已经被这一层设备占据的空间从 remaining 空间中剔除。
        """
        if max_thickness <= 0:
            return

        rm_min = np.array(self.remaining.min, dtype=float)
        size = np.array(self.remaining.size(), dtype=float)
        rm_max = rm_min + size

        if face_id == 4:      # -Z：从下往上切
            rm_min[2] += max_thickness
        elif face_id == 5:    # +Z：从上往下切
            rm_max[2] -= max_thickness
        elif face_id == 0:    # -X：从左往右切
            rm_min[0] += max_thickness
        elif face_id == 1:    # +X：从右往左切
            rm_max[0] -= max_thickness
        elif face_id == 2:    # -Y：从后往前切
            rm_min[1] += max_thickness
        elif face_id == 3:    # +Y：从前往后切
            rm_max[1] -= max_thickness
        else:
            raise ValueError(f"未知 face_id: {face_id}")

        # 防御：避免 min > max，至少保证不反向
        new_min = np.minimum(rm_min, rm_max)
        new_max = np.maximum(rm_min, rm_max)
        self.remaining = AABB(min=new_min, max=new_max)


# ===========================
# 面任务生成 & 单面排布
# ===========================

def create_face_tasks(bins: List[AABB]) -> List[Tuple[int, int]]:
    """
    面分解函数：
    给定多个 bin，生成所有 (bin_idx, face_id) 组合，face_id ∈ [0,5]。
    """
    tasks: List[Tuple[int, int]] = []
    for bin_idx in range(len(bins)):
        for face_id in range(6):
            tasks.append((bin_idx, face_id))
    return tasks


def pack_single_face(
    mapper: BinFaceMapper,
    bin_idx: int,
    face_id: int,
    candidate_parts: List[Part],
) -> Tuple[List[PlacedWithFace], List[Part]]:
    """
    单面排布函数：
    - 使用当前 bin 的 BinFaceMapper，在指定 face_id 上做 2D 布局；
    - 按该面上的最大厚度切层更新 mapper.remaining；
    - 返回本面成功布置的元件（带面标记）和剩余未放置元件。
    """
    if not candidate_parts:
        return [], candidate_parts

    # 当前剩余空间下，该面的 2D 板尺寸
    W, H = mapper.board_size(face_id)
    if W <= 0 or H <= 0:
        # 剩余空间在这一面已经为 0，无法再布局
        return [], candidate_parts

    # 建立单面 Packer
    packer = Packer()
    packer.add_bin(Bin(f"BIN{bin_idx}_F{face_id}", float(W), float(H), 1.0, max_weight=99999))

    id2part: Dict[str, Part] = {p.id: p for p in candidate_parts}

    # 为每个候选 Part 添加 2D item（使用安装尺寸）
    for p in candidate_parts:
        install_dims = p.get_install_dims(face_id)
        L_u, L_v, _ = mapper.project_part_dims(face_id, install_dims)

        # 使用安装尺寸（已经包含了间隙）
        w = float(L_u)
        h = float(L_v)
        d = 1.0

        item = Item(p.id, w, h, d, p.mass)
        packer.add_item(item)

    # 在该面上做一次 2D 装箱
    packer.pack(
        distribute_items=False,
        bigger_first=True,
        number_of_decimals=0,
    )

    b = packer.bins[0]
    if not b.items:
        # 这一面一个都放不下
        return [], candidate_parts

    placed_with_face: List[PlacedWithFace] = []
    placed_ids_face = set()
    max_thickness = 0.0

    for it in b.items:
        pid = it.name
        original_part = id2part[pid]
        install_dims = original_part.get_install_dims(face_id)

        # 映射 (u,v) -> 3D 安装坐标（使用安装尺寸）
        u = float(it.position[0])
        v = float(it.position[1])
        install_pos = mapper.uv_to_world_min(face_id, u, v, install_dims)

        # 计算当前 part 在该面的厚度（用于切层，使用安装尺寸）
        _, _, thickness = mapper.project_part_dims(face_id, install_dims)
        max_thickness = max(max_thickness, thickness)

        # 计算安装位点（使用安装坐标和安装尺寸）
        mount_point = original_part.compute_mount_point(face_id, install_pos)

        # 创建已放置的 Part 对象（复制原 part 的属性，设置放置信息）
        # 注意：position 存储的是安装坐标，实际坐标由 get_actual_position() 计算
        placed_part = Part(
            id=original_part.id,
            dims=original_part.dims,
            mass=original_part.mass,
            power=original_part.power,
            category=original_part.category,
            color=original_part.color,
            clearance_mm=original_part.clearance_mm,
            position=install_pos,  # 存储安装坐标
            bin_index=bin_idx,
            mount_face=face_id,
            mount_point=mount_point
        )

        placed_with_face.append(PlacedWithFace(placed=placed_part, face_id=face_id))
        placed_ids_face.add(original_part.id)

    # 按当前面最大厚度沿法向切掉一层空间
    mapper.cut_after_face(face_id, max_thickness)

    # 更新剩余未放置元件
    remaining_parts = [p for p in candidate_parts if p.id not in placed_ids_face]

    return placed_with_face, remaining_parts


# ===========================
# 重合度计算
# ===========================

def compute_overlap_count(placed_with_face_list: List[PlacedWithFace]) -> int:
    """
    计算 3D 重合次数：
    - 面内默认不重合（依赖 py3dbp 的 2D 排布），不再检查；
    - 对不同 face_id 但同一 bin 的元件，两两检查 3D AABB 是否有体积交集；
    - 使用实际坐标和实际尺寸进行碰撞检测；
    - 返回发生重合的 pair 数目。
    """
    overlaps = 0
    eps = 1e-6
    n = len(placed_with_face_list)

    for i in range(n):
        a = placed_with_face_list[i]
        part_a = a.placed
        # 使用实际坐标和实际尺寸
        pos_a = part_a.get_actual_position()
        dims_a = part_a.get_actual_dims()
        min_a = pos_a
        max_a = pos_a + dims_a

        for j in range(i + 1, n):
            b = placed_with_face_list[j]

            # 不同面、且在同一个 bin 上才需要检查
            if a.face_id == b.face_id:
                continue
            if part_a.bin_index != b.placed.bin_index:
                continue

            part_b = b.placed
            # 使用实际坐标和实际尺寸
            pos_b = part_b.get_actual_position()
            dims_b = part_b.get_actual_dims()
            min_b = pos_b
            max_b = pos_b + dims_b

            # 3D AABB 相交条件（严格体积交集，接触不算重合）
            overlap_x = (min_a[0] < max_b[0] - eps) and (min_b[0] < max_a[0] - eps)
            overlap_y = (min_a[1] < max_b[1] - eps) and (min_b[1] < max_a[1] - eps)
            overlap_z = (min_a[2] < max_b[2] - eps) and (min_b[2] < max_a[2] - eps)

            if overlap_x and overlap_y and overlap_z:
                overlaps += 1

    return overlaps


# ===========================
# 单次运行：一个 layout run
# ===========================

def _single_run_pack(
    parts: List[Part],
    bins: List[AABB],
    clearance_mm: float,
) -> Tuple[List[Part], List[Part], Dict[str, float]]:
    """
    单次运行：
    - 为每个 bin 构造一个 BinFaceMapper（管理剩余空间和 3D<->2D 映射）；
    - 生成所有 (bin,face) 面任务，随机顺序逐个执行；
    - 每完成一个面的布局，用该面的最大厚度切掉一层空间；
    - 记录所有放置结果（带 face_id）；
    - 计算重合次数、已放件数、体积和使用 bin 数。
    """
    # 拷贝一份元件列表并打乱
    remaining_parts: List[Part] = parts.copy()
    random.shuffle(remaining_parts)

    # 为每个 bin 创建独立的 face mapper（带独立 remaining AABB）
    mappers: List[BinFaceMapper] = [BinFaceMapper(b) for b in bins]

    # 生成面任务，并随机顺序
    face_tasks = create_face_tasks(bins)
    random.shuffle(face_tasks)

    placed_with_face_all: List[PlacedWithFace] = []
    used_bins = set()

    for (bin_idx, face_id) in face_tasks:
        if not remaining_parts:
            break

        mapper = mappers[bin_idx]
        placed_face, remaining_parts = pack_single_face(
            mapper=mapper,
            bin_idx=bin_idx,
            face_id=face_id,
            candidate_parts=remaining_parts,
        )

        if placed_face:
            used_bins.add(bin_idx)
            placed_with_face_all.extend(placed_face)

    placed_parts: List[Part] = [pwf.placed for pwf in placed_with_face_all]
    unplaced_parts: List[Part] = remaining_parts

    placed_count = len(placed_parts)
    placed_volume = float(sum(np.prod(pp.get_actual_dims()) for pp in placed_parts)) if placed_parts else 0.0
    used_bin_count = len(used_bins)
    overlap_count = compute_overlap_count(placed_with_face_all)

    stats = {
        "overlap_count": overlap_count,
        "placed_count": float(placed_count),
        "placed_volume": placed_volume,
        "used_bins": float(used_bin_count),
    }

    return placed_parts, unplaced_parts, stats


# ===========================
# 多次运行：对外主接口
# ===========================

def multistart_pack(
    parts: List[Part],
    bins: List[AABB],
    clearance_mm: float = 5.0,
    multistart: int = 3
) -> PackingResult:
    """
    多启动装箱（多面贴壁 + 切层 + 重合度优先评分版）

    评分优先级：
    1. 重合次数更少（primary）；
    2. 放置件数更多；
    3. 放置体积更大；
    4. 使用的 bin 数更少。

    Args:
        parts: 待放置的部件列表
        bins: 可用子容器列表
        clearance_mm: 间隙（mm）
        multistart: 多启动次数

    Returns:
        PackingResult对象
    """
    print(
        f"\n开始装箱(多面贴壁布局+切层): {len(parts)} 件设备, "
        f"{len(bins)} 个子容器, 平面间隙={clearance_mm}mm, 多启动={multistart}"
    )

    if not parts or not bins:
        print("  警告: parts 或 bins 为空，直接返回。")
        return PackingResult(
            placed=[],
            unplaced=parts,
            bins_used=0,
            total_volume=0.0,
            overlap_count=0,
            score=(0, 0, 0.0, 0)
        )

    best_placed_global: List[Part] = []
    best_unplaced_global: List[Part] = parts
    # score = (-overlap_count, placed_count, placed_volume, -used_bins)
    best_score: Tuple[float, float, float, float] = (
        float("-inf"),  # -overlap_count
        -1.0,           # placed_count
        0.0,            # placed_volume
        float("-inf"),  # -used_bins
    )

    for run in range(multistart):
        print(f"\n  === 启动 {run + 1}/{multistart} ===")
        placed_run, unplaced_run, stats = _single_run_pack(parts, bins, clearance_mm)

        overlap_count = stats["overlap_count"]
        placed_count = stats["placed_count"]
        placed_volume = stats["placed_volume"]
        used_bins = stats["used_bins"]

        score = (
            -overlap_count,     # 重合越少越好
            placed_count,       # 件数越多越好
            placed_volume,      # 体积越大越好
            -used_bins,         # bin 越少越好
        )

        print(
            f"    结果: 重合对数={overlap_count}, "
            f"放置 {int(placed_count)}/{len(parts)} 件, "
            f"体积 {placed_volume:.0f}, 使用 {int(used_bins)} 个容器"
        )

        if score > best_score:
            best_score = score
            best_placed_global = placed_run
            best_unplaced_global = unplaced_run

    print(
        f"\n最优结果: 重合对数={int(-best_score[0])}, "
        f"放置 {int(best_score[1])}/{len(parts)} 件, "
        f"体积 {best_score[2]:.0f}, 使用 {int(-best_score[3])} 个容器"
    )
    print(
        f"装箱完成(多面贴壁布局+切层): 已放置 {len(best_placed_global)} 件, "
        f"未放置 {len(best_unplaced_global)} 件"
    )

    return PackingResult(
        placed=best_placed_global,
        unplaced=best_unplaced_global,
        bins_used=int(-best_score[3]),
        total_volume=best_score[2],
        overlap_count=int(-best_score[0]),
        score=(int(-best_score[0]), int(best_score[1]), best_score[2], int(-best_score[3]))
    )
