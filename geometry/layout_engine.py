"""
布局引擎 - 整合所有几何功能

提供统一的接口用于生成3D布局
"""

from typing import List, Dict, Any
import random
import numpy as np

from geometry.schema import Part, AABB, EnvelopeGeometry, PackingResult, generate_category_color
from geometry.keepout import build_envelope, build_bins, create_keepout_aabbs
from geometry.packing import multistart_pack
from core.logger import get_logger

logger = get_logger(__name__)


def generate_bom_from_config(cfg: Dict[str, Any]) -> List[Part]:
    """
    从配置生成BOM（设备清单）

    Args:
        cfg: 配置字典，包含components列表

    Returns:
        Part列表
    """
    components = cfg.get('components', [])
    parts = []

    for comp in components:
        part = Part(
            id=comp['id'],
            dims=tuple(comp['dims_mm']),
            mass=comp['mass_kg'],
            power=comp['power_w'],
            category=comp['category'],
            color=generate_category_color(comp['category']),
            clearance_mm=cfg.get('clearance_mm', 5.0)
        )
        parts.append(part)

    logger.info(f"生成BOM: {len(parts)} 个部件")
    return parts


def generate_synthetic_bom(
    n_parts: int,
    dims_min_mm: List[float],
    dims_max_mm: List[float],
    mass_range_kg: List[float],
    power_range_W: List[float],
    categories: List[str],
    clearance_mm: float = 5.0,
    seed: int = 42
) -> List[Part]:
    """
    生成合成BOM（用于测试）

    Args:
        n_parts: 部件数量
        dims_min_mm: 最小尺寸 [x, y, z]
        dims_max_mm: 最大尺寸 [x, y, z]
        mass_range_kg: 质量范围 [min, max]
        power_range_W: 功率范围 [min, max]
        categories: 类别列表
        clearance_mm: 间隙
        seed: 随机种子

    Returns:
        Part列表
    """
    random.seed(seed)
    np.random.seed(seed)

    parts = []
    for i in range(n_parts):
        # 随机生成尺寸
        dims = [
            random.uniform(dims_min_mm[0], dims_max_mm[0]),
            random.uniform(dims_min_mm[1], dims_max_mm[1]),
            random.uniform(dims_min_mm[2], dims_max_mm[2])
        ]

        # 随机生成质量和功率
        mass = random.uniform(mass_range_kg[0], mass_range_kg[1])
        power = random.uniform(power_range_W[0], power_range_W[1])

        # 随机选择类别
        category = random.choice(categories)

        part = Part(
            id=f"P{i:03d}",
            dims=tuple(dims),
            mass=mass,
            power=power,
            category=category,
            color=generate_category_color(category),
            clearance_mm=clearance_mm
        )
        parts.append(part)

    logger.info(f"生成合成BOM: {n_parts} 个部件")
    return parts


class LayoutEngine:
    """3D布局引擎"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化布局引擎

        Args:
            config: 几何配置字典
        """
        self.config = config
        self.envelope: EnvelopeGeometry = None
        self.bins: List[AABB] = None
        self.keepouts: List[AABB] = None
        self.parts: List[Part] = None
        self.packing_result: PackingResult = None

    def generate_layout(self, parts: List[Part] = None) -> PackingResult:
        """
        生成3D布局

        Args:
            parts: 部件列表（如果为None，则从配置生成）

        Returns:
            PackingResult对象
        """
        logger.info("=" * 60)
        logger.info("开始生成3D布局")
        logger.info("=" * 60)

        # 1. 生成或使用提供的BOM
        if parts is None:
            if 'synth' in self.config:
                # 生成合成BOM
                synth_cfg = self.config['synth']
                self.parts = generate_synthetic_bom(
                    n_parts=synth_cfg['n_parts'],
                    dims_min_mm=synth_cfg['dims_min_mm'],
                    dims_max_mm=synth_cfg['dims_max_mm'],
                    mass_range_kg=synth_cfg['mass_range_kg'],
                    power_range_W=synth_cfg['power_range_W'],
                    categories=synth_cfg['categories'],
                    clearance_mm=self.config.get('clearance_mm', 5.0),
                    seed=synth_cfg.get('seed', 42)
                )
            else:
                # 从配置生成BOM
                self.parts = generate_bom_from_config(self.config)
        else:
            self.parts = parts

        # 2. 构建舱体
        logger.info("\n[1/4] 构建舱体...")
        self.envelope = build_envelope(self.config, self.parts)
        logger.info(f"  外壳尺寸: {self.envelope.outer_size()}")
        logger.info(f"  内部尺寸: {self.envelope.inner_size()}")
        logger.info(f"  壁厚: {self.envelope.thickness_mm} mm")

        # 3. 处理禁区
        logger.info("\n[2/4] 处理禁区...")
        self.keepouts = create_keepout_aabbs(self.config)
        logger.info(f"  禁区数量: {len(self.keepouts)}")

        # 4. 构建可用子容器
        logger.info("\n[3/4] 构建可用子容器...")
        self.bins = build_bins(
            envelope=self.envelope.inner,
            keepouts=self.keepouts,
            min_edge_threshold=5.0
        )

        # 5. 执行装箱
        logger.info("\n[4/4] 执行装箱...")
        self.packing_result = multistart_pack(
            parts=self.parts,
            bins=self.bins,
            clearance_mm=self.config.get('clearance_mm', 5.0),
            multistart=self.config.get('multistart', 3)
        )

        logger.info("\n" + "=" * 60)
        logger.info("布局生成完成")
        logger.info(f"  已放置: {len(self.packing_result.placed)} 件")
        logger.info(f"  未放置: {len(self.packing_result.unplaced)} 件")
        logger.info(f"  重合数: {self.packing_result.overlap_count}")
        logger.info("=" * 60)

        return self.packing_result

    def get_design_summary(self) -> str:
        """
        获取设计摘要（用于LLM输入）

        Returns:
            Markdown格式的设计摘要
        """
        if self.packing_result is None:
            return "布局尚未生成"

        summary = "## 几何布局\n\n"
        summary += f"- 舱体尺寸: {self.envelope.outer_size()}\n"
        summary += f"- 已放置部件: {len(self.packing_result.placed)} 件\n"
        summary += f"- 未放置部件: {len(self.packing_result.unplaced)} 件\n\n"

        if self.packing_result.placed:
            summary += "### 已放置部件位置\n\n"
            for part in self.packing_result.placed[:5]:  # 只显示前5个
                pos = part.position
                summary += f"- {part.id} ({part.category}): 位置 ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}) mm\n"

            if len(self.packing_result.placed) > 5:
                summary += f"- ... 还有 {len(self.packing_result.placed) - 5} 个部件\n"

        return summary

    def get_total_mass(self) -> float:
        """获取总质量（kg）"""
        if self.packing_result is None:
            return 0.0
        return sum(p.mass for p in self.packing_result.placed)

    def get_total_power(self) -> float:
        """获取总功率（W）"""
        if self.packing_result is None:
            return 0.0
        return sum(p.power for p in self.packing_result.placed)
