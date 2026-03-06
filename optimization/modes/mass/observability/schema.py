"""
Compatibility re-export for observability schemas.

Canonical definitions live in `core/observability_schema.py` to avoid
core->optimization import coupling during logger bootstrap.
"""

from core.observability_schema import (
    AttemptEvent,
    CandidateEvent,
    GenerationEvent,
    PhaseEvent,
    PhysicsEvent,
    PolicyEvent,
    RunManifestEvent,
)

__all__ = [
    "RunManifestEvent",
    "PhaseEvent",
    "AttemptEvent",
    "GenerationEvent",
    "PolicyEvent",
    "PhysicsEvent",
    "CandidateEvent",
]
