"""
统一数据协议定义

使用 Pydantic 进行强类型校验，定义系统中所有数据交换格式。
"""

from enum import Enum
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field
import numpy as np


# ============ 枚举定义 ============

class OperatorType(str, Enum):
    """拓扑算子类型"""
    MOVE = "MOVE"                    # 移动组件
    SWAP = "SWAP"                    # 交换组件
    ROTATE = "ROTATE"                # 旋转组件
    ADD_SURFACE = "ADD_SURFACE"      # 增加散热面
    DEFORM = "DEFORM"                # FFD变形


class ViolationType(str, Enum):
    """违规类型"""
    THERMAL_OVERHEAT = "THERMAL_OVERHEAT"
    GEOMETRY_CLASH = "GEOMETRY_CLASH"
    MASS_LIMIT = "MASS_LIMIT"
    POWER_LIMIT = "POWER_LIMIT"
    PATH_BLOCK = "PATH_BLOCK"


class SimulationType(str, Enum):
    """仿真类型"""
    MATLAB = "MATLAB"
    COMSOL = "COMSOL"
    SIMPLIFIED = "SIMPLIFIED"


# ============ 几何数据结构 ============

class Vector3D(BaseModel):
    """三维向量"""
    x: float
    y: float
    z: float

    def to_array(self) -> np.ndarray:
        """转换为numpy数组"""
        return np.array([self.x, self.y, self.z])

    @classmethod
    def from_array(cls, arr: np.ndarray) -> 'Vector3D':
        """从numpy数组创建"""
        return cls(x=float(arr[0]), y=float(arr[1]), z=float(arr[2]))

    def __str__(self) -> str:
        return f"({self.x:.2f}, {self.y:.2f}, {self.z:.2f})"


class ComponentGeometry(BaseModel):
    """组件几何信息"""
    id: str
    position: Vector3D              # 位置（mm）
    dimensions: Vector3D            # 尺寸（mm）
    rotation: Vector3D = Field(default_factory=lambda: Vector3D(x=0, y=0, z=0))  # 旋转角度（度）
    mass: float                     # 质量（kg）
    power: float                    # 功率（W）
    category: str                   # 类别（payload, avionics, power等）
    clearance: float = 5.0          # 间隙（mm）

    class Config:
        json_encoders = {
            Vector3D: lambda v: {"x": v.x, "y": v.y, "z": v.z}
        }


class Envelope(BaseModel):
    """舱体包络信息"""
    outer_size: Vector3D            # 外部尺寸（mm）
    inner_size: Optional[Vector3D] = None  # 内部尺寸（mm）
    thickness: float = 0.0          # 壁厚（mm）
    fill_ratio: float = 0.30        # 占空比
    origin: str = "center"          # 原点位置：center 或 corner


class KeepoutZone(BaseModel):
    """禁区定义"""
    min_point: Vector3D             # 最小点（mm）
    max_point: Vector3D             # 最大点（mm）
    tag: str                        # 标签（如 sensor_fov, antenna_cone）


class DesignState(BaseModel):
    """设计状态"""
    iteration: int
    components: List[ComponentGeometry]
    envelope: Envelope
    keepouts: List[KeepoutZone] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ============ 仿真协议 ============

class SimulationRequest(BaseModel):
    """仿真请求"""
    sim_type: SimulationType
    design_state: DesignState
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ViolationItem(BaseModel):
    """违规项"""
    id: str
    type: ViolationType
    description: str
    involved_components: List[str]
    severity: float = Field(ge=0.0, le=1.0)  # 0-1范围


class SimulationResult(BaseModel):
    """仿真结果"""
    success: bool
    metrics: Dict[str, float]       # 如：max_temp, min_distance, total_mass
    violations: List[ViolationItem] = Field(default_factory=list)
    raw_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


# ============ 优化协议 ============

class SearchAction(BaseModel):
    """优化动作"""
    op_id: OperatorType
    target_component: str
    parameters: Dict[str, Any]      # 灵活的参数字典
    bounds: Optional[List[float]] = None  # 搜索边界 [min, max]
    conflicts: List[str] = Field(default_factory=list)  # 关联的违规ID
    hints: List[str] = Field(default_factory=list)      # 提示信息


class OptimizationPlan(BaseModel):
    """优化计划（LLM输出）"""
    plan_id: str
    reasoning: str
    actions: List[SearchAction]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ContextPack(BaseModel):
    """上下文包（LLM输入）"""
    design_iteration: int
    metrics: Dict[str, float]
    violations: List[ViolationItem] = Field(default_factory=list)
    geometry_summary: str
    physics_summary: str
    history_trace: List[str] = Field(default_factory=list)
    allowed_ops: List[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        """转换为Markdown格式的Prompt"""
        md = f"# Satellite Design State (Iteration {self.design_iteration})\n\n"

        # 指标
        md += "## Metrics\n"
        for k, v in self.metrics.items():
            md += f"- **{k}**: {v:.2f}\n"

        # 违规
        md += "\n## Violations\n"
        if not self.violations:
            md += "None. Design is feasible.\n"
        else:
            for v in self.violations:
                md += f"- [{v.type.value}] {v.description}\n"
                md += f"  - Components: {', '.join(v.involved_components)}\n"
                md += f"  - Severity: {v.severity:.2f}\n"

        # 几何
        md += f"\n## Geometry\n{self.geometry_summary}\n"

        # 物理
        md += f"\n## Physics\n{self.physics_summary}\n"

        # 历史
        if self.history_trace:
            md += "\n## History\n"
            for h in self.history_trace:
                md += f"- {h}\n"

        # 允许的操作
        if self.allowed_ops:
            md += f"\n## Allowed Operators\n{', '.join(self.allowed_ops)}\n"

        return md


# ============ 配置数据结构 ============

class GeometryConfig(BaseModel):
    """几何配置"""
    envelope: Dict[str, Any]
    components: List[Dict[str, Any]]
    keepouts: List[Dict[str, Any]] = Field(default_factory=list)
    clearance_mm: float = 5.0


class SimulationConfig(BaseModel):
    """仿真配置"""
    type: SimulationType
    matlab_workspace: Optional[str] = None
    matlab_function: Optional[str] = None
    comsol_model: Optional[str] = None
    comsol_parameters: List[str] = Field(default_factory=list)
    constraints: Dict[str, float] = Field(default_factory=dict)


class OptimizationConfig(BaseModel):
    """优化配置"""
    max_iterations: int = 20
    convergence_threshold: float = 0.01
    allowed_operators: List[str] = Field(default_factory=lambda: ["MOVE"])
    solver_method: str = "bounded"
    solver_tolerance: float = 1e-6


class OpenAIConfig(BaseModel):
    """OpenAI配置"""
    api_key: str
    model: str = "gpt-4"
    temperature: float = 0.5
    max_tokens: int = 2000


class LoggingConfig(BaseModel):
    """日志配置"""
    level: str = "INFO"
    output_dir: str = "experiments"
    save_llm_interactions: bool = True
    save_visualizations: bool = True


class SystemConfig(BaseModel):
    """系统配置"""
    project: Dict[str, str]
    geometry: GeometryConfig
    simulation: SimulationConfig
    optimization: OptimizationConfig
    openai: OpenAIConfig
    logging: LoggingConfig
