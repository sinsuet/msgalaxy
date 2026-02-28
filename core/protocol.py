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
    """拓扑算子类型 (DV2.0: 10类算子)"""
    # === 基础几何算子 ===
    MOVE = "MOVE"                    # 移动组件
    SWAP = "SWAP"                    # 交换组件
    ROTATE = "ROTATE"                # 旋转组件
    DEFORM = "DEFORM"                # FFD变形
    ALIGN = "ALIGN"                  # 对齐组件（沿指定轴对齐多个组件）

    # === 包络与结构算子 ===
    CHANGE_ENVELOPE = "CHANGE_ENVELOPE"  # 包络切换（Box→Cylinder等）
    ADD_BRACKET = "ADD_BRACKET"          # 添加结构支架（垫高组件、改变质心）

    # === 热学算子 ===
    ADD_HEATSINK = "ADD_HEATSINK"        # 添加散热窗/板（高导热附加几何体）
    MODIFY_COATING = "MODIFY_COATING"    # 修改涂层（表面发射率/吸收率）
    SET_THERMAL_CONTACT = "SET_THERMAL_CONTACT"  # 设置接触热阻（热隔离/热桥）


class ViolationType(str, Enum):
    """违规类型"""
    THERMAL_OVERHEAT = "THERMAL_OVERHEAT"
    GEOMETRY_CLASH = "GEOMETRY_CLASH"
    MASS_LIMIT = "MASS_LIMIT"
    POWER_LIMIT = "POWER_LIMIT"
    PATH_BLOCK = "PATH_BLOCK"


class SimulationType(str, Enum):
    """仿真类型 (v2.0.3: 仅支持COMSOL动态导入)"""
    COMSOL = "COMSOL"


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
    """组件几何信息 (DV2.0: 扩展热学属性)"""
    id: str
    position: Vector3D              # 位置（mm）
    dimensions: Vector3D            # 尺寸（mm）
    rotation: Vector3D = Field(default_factory=lambda: Vector3D(x=0, y=0, z=0))  # 旋转角度（度）
    mass: float                     # 质量（kg）
    power: float                    # 功率（W）
    category: str                   # 类别（payload, avionics, power等）
    clearance: float = 5.0          # 间隙（mm）

    # === DV2.0: 包络类型 ===
    envelope_type: str = "box"      # 包络类型（box, cylinder）

    # === DV2.0: 热学属性 ===
    emissivity: float = 0.8         # 表面发射率（0-1，默认铝合金）
    absorptivity: float = 0.3       # 太阳吸收率（0-1，默认白漆）
    coating_type: str = "default"   # 涂层类型（default, high_emissivity, low_absorptivity, MLI）

    # === DV2.0: 接触热阻 ===
    thermal_contacts: Dict[str, float] = Field(default_factory=dict)  # {邻接组件ID: 接触热导(W/m²·K)}

    # === DV2.0: 附加结构 ===
    heatsink: Optional[Dict[str, Any]] = None   # 散热器参数 {"face": "+Y", "thickness": 2.0, "conductivity": 400}
    bracket: Optional[Dict[str, Any]] = None    # 支架参数 {"height": 20.0, "material": "aluminum"}

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
    """设计状态（支持版本树追溯）"""
    iteration: int
    components: List[ComponentGeometry]
    envelope: Envelope
    keepouts: List[KeepoutZone] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Phase 4: 状态版本树支持
    state_id: str = Field(default="")  # 状态唯一标识，如 "state_iter_01_a"
    parent_id: Optional[str] = None    # 父状态ID，用于构建演化树


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


class EvaluationResult(BaseModel):
    """评估结果（Phase 4: 用于状态池存储）"""
    state_id: str
    iteration: int
    success: bool
    metrics: Dict[str, float]
    violations: List[Dict[str, Any]] = Field(default_factory=list)  # 使用字典列表避免类型冲突
    penalty_score: float = 0.0  # 惩罚分（越低越好）
    timestamp: str = ""
    error_message: Optional[str] = None


# ============ 优化协议 ============

class SearchAction(BaseModel):
    """优化动作 (DV2.0: 支持10类算子)

    参数说明 (parameters 字段):
    - MOVE: {"axis": "X/Y/Z", "delta": float} 或 {"target_position": [x,y,z]}
    - ROTATE: {"axis": "X/Y/Z", "angle": float}
    - SWAP: {"target_component": str}
    - DEFORM: {"control_points": [...], "displacements": [...]}
    - ALIGN: {"axis": "X/Y/Z", "reference_component": str, "components": [str]}
    - CHANGE_ENVELOPE: {"shape": "box/cylinder", "dimensions": {...}}
    - ADD_BRACKET: {"height": float, "material": str, "attach_face": "-Z"}
    - ADD_HEATSINK: {"face": "+Y/-Y/+X/-X/+Z/-Z", "thickness": float, "conductivity": float}
    - MODIFY_COATING: {"emissivity": float, "absorptivity": float, "coating_type": str}
    - SET_THERMAL_CONTACT: {"contact_component": str, "conductance": float, "gap": float}
    """
    op_id: OperatorType
    target_component: str
    parameters: Dict[str, Any]      # 灵活的参数字典（见上方说明）
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

    # Phase 4: 历史失败记录，用于警告 LLM 避免重复错误
    recent_failures: List[str] = Field(default_factory=list)  # 最近失败的操作描述
    rollback_warning: Optional[str] = None  # 回退警告信息

    def to_markdown(self) -> str:
        """转换为Markdown格式的Prompt"""
        md = f"# Satellite Design State (Iteration {self.design_iteration})\n\n"

        # Phase 4: 回退警告（最高优先级显示）
        if self.rollback_warning:
            md += "## ⚠️ ROLLBACK WARNING\n"
            md += f"**{self.rollback_warning}**\n\n"
            md += "**CRITICAL**: Your previous strategy caused severe degradation. "
            md += "You MUST change your optimization direction completely!\n\n"

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

        # Phase 4: 最近失败记录
        if self.recent_failures:
            md += "\n## Recent Failed Attempts\n"
            md += "**Learn from these failures - DO NOT repeat them:**\n"
            for f in self.recent_failures:
                md += f"- ❌ {f}\n"

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
