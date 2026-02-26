"""
自定义异常定义
"""


class SatelliteDesignError(Exception):
    """卫星设计系统基础异常"""
    pass


class SimulationError(SatelliteDesignError):
    """仿真相关异常"""
    pass


class MatlabConnectionError(SimulationError):
    """MATLAB连接异常"""
    pass


class ComsolConnectionError(SimulationError):
    """COMSOL连接异常"""
    pass


class GeometryError(SatelliteDesignError):
    """几何处理异常"""
    pass


class PackingError(GeometryError):
    """装箱算法异常"""
    pass


class OptimizationError(SatelliteDesignError):
    """优化相关异常"""
    pass


class LLMError(OptimizationError):
    """LLM调用异常"""
    pass


class ConfigurationError(SatelliteDesignError):
    """配置错误异常"""
    pass


class ValidationError(SatelliteDesignError):
    """数据验证异常"""
    pass


class BOMParseError(SatelliteDesignError):
    """BOM文件解析异常"""
    pass


class VisualizationError(SatelliteDesignError):
    """可视化生成异常"""
    pass


class ConvergenceError(OptimizationError):
    """优化收敛失败异常"""
    pass


class ConstraintViolationError(OptimizationError):
    """约束违反异常"""
    def __init__(self, message: str, violations: list = None):
        super().__init__(message)
        self.violations = violations or []
