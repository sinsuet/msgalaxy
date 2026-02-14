"""
Core module for satellite design optimization system.

Provides unified data protocols, logging, and exception handling.
"""

from .protocol import (
    # Enums
    OperatorType,
    ViolationType,
    SimulationType,
    # Geometry
    Vector3D,
    ComponentGeometry,
    Envelope,
    KeepoutZone,
    DesignState,
    # Simulation
    SimulationRequest,
    SimulationResult,
    ViolationItem,
    # Optimization
    SearchAction,
    OptimizationPlan,
    ContextPack,
    # Config
    SystemConfig,
    GeometryConfig,
    SimulationConfig,
    OptimizationConfig,
    OpenAIConfig,
    LoggingConfig,
)

from .logger import ExperimentLogger, get_logger

from .exceptions import (
    SatelliteDesignError,
    SimulationError,
    MatlabConnectionError,
    ComsolConnectionError,
    GeometryError,
    PackingError,
    OptimizationError,
    LLMError,
    ConfigurationError,
    ValidationError,
)

__all__ = [
    # Enums
    "OperatorType",
    "ViolationType",
    "SimulationType",
    # Geometry
    "Vector3D",
    "ComponentGeometry",
    "Envelope",
    "KeepoutZone",
    "DesignState",
    # Simulation
    "SimulationRequest",
    "SimulationResult",
    "ViolationItem",
    # Optimization
    "SearchAction",
    "OptimizationPlan",
    "ContextPack",
    # Config
    "SystemConfig",
    "GeometryConfig",
    "SimulationConfig",
    "OptimizationConfig",
    "OpenAIConfig",
    "LoggingConfig",
    # Logger
    "ExperimentLogger",
    "get_logger",
    # Exceptions
    "SatelliteDesignError",
    "SimulationError",
    "MatlabConnectionError",
    "ComsolConnectionError",
    "GeometryError",
    "PackingError",
    "OptimizationError",
    "LLMError",
    "ConfigurationError",
    "ValidationError",
]
