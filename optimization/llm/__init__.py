"""
LLM controller layer.
"""

from .gateway import (
    DashScopeNativeAdapter,
    LLMCallResult,
    LLMEmbeddingResult,
    LLMGateway,
    LLMProfileResolver,
    LLMProviderProfile,
    OpenAICompatibleAdapter,
    build_legacy_gateway,
)

__all__ = [
    "DashScopeNativeAdapter",
    "LLMCallResult",
    "LLMEmbeddingResult",
    "LLMGateway",
    "LLMProfileResolver",
    "LLMProviderProfile",
    "OpenAICompatibleAdapter",
    "build_legacy_gateway",
]
