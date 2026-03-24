"""
Simulation module for satellite design optimization.

Provides COMSOL dynamic import interface and structural physics.
"""

from .base import SimulationDriver
from .comsol_driver import ComsolDriver
from .contracts import normalize_runtime_constraints
from .mission_proxy import evaluate_mission_fov_interface
from .thermal_proxy import estimate_proxy_thermal_metrics
from . import structural_physics

__all__ = [
    "SimulationDriver",
    "ComsolDriver",
    "estimate_proxy_thermal_metrics",
    "normalize_runtime_constraints",
    "evaluate_mission_fov_interface",
    "structural_physics",
]
