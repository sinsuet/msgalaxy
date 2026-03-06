"""
mass compatibility protocol re-export.
"""

import optimization.protocol as _protocol
from optimization.protocol import *  # noqa: F401,F403

__all__ = list(getattr(_protocol, "__all__", []))
