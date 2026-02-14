"""
Simulation module for satellite design optimization.

Provides interfaces to MATLAB, COMSOL, and simplified physics engines.
"""

from .base import SimulationDriver
from .matlab_driver import MatlabDriver
from .comsol_driver import ComsolDriver
from .physics_engine import SimplifiedPhysicsEngine

__all__ = [
    "SimulationDriver",
    "MatlabDriver",
    "ComsolDriver",
    "SimplifiedPhysicsEngine",
]
