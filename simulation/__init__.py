"""
Simulation module for satellite design optimization.

Provides COMSOL dynamic import interface and structural physics.
"""

from .base import SimulationDriver
from .comsol_driver import ComsolDriver
from .contracts import normalize_runtime_constraints
from .mission_proxy import evaluate_mission_fov_interface
from .physics_engine import SimplifiedPhysicsEngine
from . import structural_physics

__all__ = [
    "SimulationDriver",
    "ComsolDriver",
    "SimplifiedPhysicsEngine",
    "normalize_runtime_constraints",
    "evaluate_mission_fov_interface",
    "structural_physics",
]
