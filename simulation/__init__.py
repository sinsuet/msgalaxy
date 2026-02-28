"""
Simulation module for satellite design optimization.

Provides COMSOL dynamic import interface and structural physics.
"""

from .base import SimulationDriver
from .comsol_driver import ComsolDriver
from . import structural_physics

__all__ = [
    "SimulationDriver",
    "ComsolDriver",
    "structural_physics",
]
