"""Mass-specific RAG components."""

from .evidence_schema import MassEvidence, RetrievalCandidate
from .mass_rag_system import MassRAGSystem

__all__ = [
    "MassEvidence",
    "RetrievalCandidate",
    "MassRAGSystem",
]

