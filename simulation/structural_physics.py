"""
结构物理场计算模块

提供结构相关的物理指标计算，包括：
- 质心偏移 (Center of Mass Offset)
- 转动惯量 (Moment of Inertia)
- 质量分布分析
"""

import numpy as np
from typing import Tuple, List, Dict, Any
from core.protocol import DesignState, Vector3D
from core.logger import get_logger

logger = get_logger(__name__)


def calculate_center_of_mass(design_state: DesignState) -> Vector3D:
    """
    计算整星质心位置

    Args:
        design_state: 设计状态

    Returns:
        质心位置 (mm)
    """
    total_mass = 0.0
    weighted_sum = np.array([0.0, 0.0, 0.0])

    for comp in design_state.components:
        mass = comp.mass
        position = np.array([comp.position.x, comp.position.y, comp.position.z])

        total_mass += mass
        weighted_sum += mass * position

    if total_mass == 0:
        logger.warning("总质量为0，返回原点作为质心")
        return Vector3D(x=0.0, y=0.0, z=0.0)

    com = weighted_sum / total_mass

    return Vector3D(x=float(com[0]), y=float(com[1]), z=float(com[2]))


def calculate_cg_offset(design_state: DesignState) -> float:
    """
    计算质心偏移量

    质心偏移量定义为实际质心与理想几何中心的欧氏距离

    Args:
        design_state: 设计状态

    Returns:
        质心偏移量 (mm)
    """
    # 计算实际质心
    com = calculate_center_of_mass(design_state)

    # 计算理想几何中心（envelope的中心）
    envelope = design_state.envelope
    if envelope.origin == "center":
        # 如果原点在中心，几何中心就是 (0, 0, 0)
        geometric_center = np.array([0.0, 0.0, 0.0])
    else:
        # 如果原点在角点，几何中心是 envelope 尺寸的一半
        geometric_center = np.array([
            envelope.outer_size.x / 2.0,
            envelope.outer_size.y / 2.0,
            envelope.outer_size.z / 2.0
        ])

    # 计算偏移量（欧氏距离）
    com_array = np.array([com.x, com.y, com.z])
    offset = np.linalg.norm(com_array - geometric_center)

    logger.info(f"质心: ({com.x:.2f}, {com.y:.2f}, {com.z:.2f}) mm")
    logger.info(f"几何中心: ({geometric_center[0]:.2f}, {geometric_center[1]:.2f}, {geometric_center[2]:.2f}) mm")
    logger.info(f"质心偏移量: {offset:.2f} mm")

    return float(offset)


def calculate_moment_of_inertia(design_state: DesignState) -> Tuple[float, float, float]:
    """
    计算转动惯量（简化模型）

    假设每个组件是均匀密度的长方体，使用平行轴定理计算整星的转动惯量

    Args:
        design_state: 设计状态

    Returns:
        (Ixx, Iyy, Izz) 转动惯量 (kg·m²)
    """
    # 计算质心
    com = calculate_center_of_mass(design_state)
    com_array = np.array([com.x, com.y, com.z]) / 1000.0  # mm -> m

    Ixx = 0.0
    Iyy = 0.0
    Izz = 0.0

    for comp in design_state.components:
        mass = comp.mass  # kg
        pos = np.array([comp.position.x, comp.position.y, comp.position.z]) / 1000.0  # mm -> m
        dim = np.array([comp.dimensions.x, comp.dimensions.y, comp.dimensions.z]) / 1000.0  # mm -> m

        # 组件自身的转动惯量（长方体，绕质心）
        Ixx_local = mass * (dim[1]**2 + dim[2]**2) / 12.0
        Iyy_local = mass * (dim[0]**2 + dim[2]**2) / 12.0
        Izz_local = mass * (dim[0]**2 + dim[1]**2) / 12.0

        # 平行轴定理：I = I_local + m * d²
        r = pos - com_array  # 组件质心到整星质心的向量

        Ixx += Ixx_local + mass * (r[1]**2 + r[2]**2)
        Iyy += Iyy_local + mass * (r[0]**2 + r[2]**2)
        Izz += Izz_local + mass * (r[0]**2 + r[1]**2)

    logger.info(f"转动惯量: Ixx={Ixx:.4f}, Iyy={Iyy:.4f}, Izz={Izz:.4f} kg·m²")

    return (float(Ixx), float(Iyy), float(Izz))


def analyze_mass_distribution(design_state: DesignState) -> Dict[str, Any]:
    """
    分析质量分布

    Args:
        design_state: 设计状态

    Returns:
        质量分布分析结果
    """
    total_mass = sum(comp.mass for comp in design_state.components)
    com = calculate_center_of_mass(design_state)
    cg_offset = calculate_cg_offset(design_state)
    moi = calculate_moment_of_inertia(design_state)

    # 按类别统计质量
    mass_by_category = {}
    for comp in design_state.components:
        category = comp.category
        if category not in mass_by_category:
            mass_by_category[category] = 0.0
        mass_by_category[category] += comp.mass

    # 找出最重的组件
    heaviest_comp = max(design_state.components, key=lambda c: c.mass)

    return {
        "total_mass": total_mass,
        "center_of_mass": {"x": com.x, "y": com.y, "z": com.z},
        "cg_offset": cg_offset,
        "moment_of_inertia": {"Ixx": moi[0], "Iyy": moi[1], "Izz": moi[2]},
        "mass_by_category": mass_by_category,
        "heaviest_component": {
            "id": heaviest_comp.id,
            "mass": heaviest_comp.mass,
            "category": heaviest_comp.category
        }
    }


if __name__ == "__main__":
    # 测试代码
    from core.protocol import ComponentGeometry, Envelope

    # 创建测试设计
    components = [
        ComponentGeometry(
            id="battery_01",
            position=Vector3D(x=50.0, y=50.0, z=50.0),
            dimensions=Vector3D(x=100.0, y=80.0, z=50.0),
            mass=5.0,
            power=10.0,
            category="power"
        ),
        ComponentGeometry(
            id="payload_01",
            position=Vector3D(x=200.0, y=50.0, z=50.0),
            dimensions=Vector3D(x=80.0, y=80.0, z=60.0),
            mass=3.0,
            power=5.0,
            category="payload"
        )
    ]

    envelope = Envelope(
        outer_size=Vector3D(x=400.0, y=200.0, z=200.0),
        origin="center"
    )

    design_state = DesignState(
        iteration=0,
        components=components,
        envelope=envelope
    )

    # 测试质心计算
    print("=" * 60)
    print("测试质心偏移计算")
    print("=" * 60)

    com = calculate_center_of_mass(design_state)
    print(f"质心位置: ({com.x:.2f}, {com.y:.2f}, {com.z:.2f}) mm")

    cg_offset = calculate_cg_offset(design_state)
    print(f"质心偏移量: {cg_offset:.2f} mm")

    moi = calculate_moment_of_inertia(design_state)
    print(f"转动惯量: Ixx={moi[0]:.4f}, Iyy={moi[1]:.4f}, Izz={moi[2]:.4f} kg·m²")

    analysis = analyze_mass_distribution(design_state)
    print("\n质量分布分析:")
    print(f"  总质量: {analysis['total_mass']:.2f} kg")
    print(f"  质心偏移: {analysis['cg_offset']:.2f} mm")
    print(f"  按类别统计: {analysis['mass_by_category']}")
    print(f"  最重组件: {analysis['heaviest_component']['id']} ({analysis['heaviest_component']['mass']:.2f} kg)")
