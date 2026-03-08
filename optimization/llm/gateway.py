"""
Unified LLM gateway and provider profile resolver.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import dashscope
import requests
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
)

from core.exceptions import ConfigurationError, LLMError
from optimization.llm.runtime_client import extract_json_object_text, mask_secret


DEFAULT_TEXT_PROFILE = "qwen_max_default"
DEFAULT_EMBEDDING_PROFILE = "qwen_embedding_default"
DEFAULT_TIMEOUT_S = 120.0
ENV_PLACEHOLDER_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
DEFAULT_REASONING_PROFILE = "balanced"
DEFAULT_THINKING_MODE = "auto"


@dataclass
class LLMProviderProfile:
    name: str
    provider: str
    api_style: str
    model: str
    base_url: str = ""
    api_key: str = ""
    api_key_env: str = ""
    api_key_envs: List[str] = field(default_factory=list)
    api_key_source: str = ""
    temperature: float = 0.7
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_tokens: int = 0
    completion_budget_tokens: int = 0
    reasoning_profile: str = DEFAULT_REASONING_PROFILE
    thinking_mode: str = DEFAULT_THINKING_MODE
    strict_json_thinking_mode: str = ""
    reasoning_budget_tokens: int = 0
    provider_options: Dict[str, Any] = field(default_factory=dict)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    native_fallback: Dict[str, Any] = field(default_factory=dict)

    @property
    def key_source_masked(self) -> str:
        if not self.api_key_source:
            return ""
        secret = mask_secret(self.api_key)
        if not secret:
            return self.api_key_source
        return f"{self.api_key_source}:{secret}"


@dataclass
class LLMCallResult:
    content: str
    status_code: int
    provider: str
    profile: str
    model: str
    api_style: str
    fallback_used: bool = False
    fallback_reason: str = ""
    key_source: str = ""
    key_source_masked: str = ""
    reasoning_profile: str = ""
    thinking_mode: str = ""
    strict_json_thinking_mode: str = ""
    completion_budget_tokens: int = 0
    reasoning_budget_tokens: int = 0
    request_payload: Dict[str, Any] = field(default_factory=dict)
    response_payload: Any = None

    def as_log_metadata(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "provider": self.provider,
            "model": self.model,
            "api_style": self.api_style,
            "fallback_used": bool(self.fallback_used),
            "fallback_reason": str(self.fallback_reason or ""),
            "key_source": str(self.key_source or ""),
            "key_source_masked": str(self.key_source_masked or ""),
            "reasoning_profile": str(self.reasoning_profile or ""),
            "thinking_mode": str(self.thinking_mode or ""),
            "strict_json_thinking_mode": str(self.strict_json_thinking_mode or ""),
            "completion_budget_tokens": int(self.completion_budget_tokens or 0),
            "reasoning_budget_tokens": int(self.reasoning_budget_tokens or 0),
        }


@dataclass
class LLMEmbeddingResult:
    vectors: List[List[float]]
    provider: str
    profile: str
    model: str
    api_style: str
    key_source: str = ""
    key_source_masked: str = ""
    request_payload: Dict[str, Any] = field(default_factory=dict)
    response_payload: Any = None


class LLMProfileResolver:
    def __init__(self, openai_config: Optional[Dict[str, Any]] = None):
        self.openai_config = dict(openai_config or {})

    def resolve_text_profile(self, profile_name: str = "") -> LLMProviderProfile:
        selected = str(profile_name or self.openai_config.get("default_text_profile", "") or "").strip()
        if not selected:
            selected = self._default_legacy_text_profile_name()
        profiles = self._normalized_profiles()
        if selected not in profiles:
            raise ConfigurationError(f"Unknown text LLM profile: {selected}")
        return self._materialize_profile(selected, profiles[selected], profile_kind="text")

    def resolve_embedding_profile(self, profile_name: str = "") -> LLMProviderProfile:
        selected = str(
            profile_name
            or self.openai_config.get("default_embedding_profile", "")
            or ""
        ).strip()
        if not selected:
            selected = self._default_legacy_embedding_profile_name()
        profiles = self._normalized_profiles()
        if selected not in profiles:
            raise ConfigurationError(f"Unknown embedding LLM profile: {selected}")
        return self._materialize_profile(selected, profiles[selected], profile_kind="embedding")

    def _normalized_profiles(self) -> Dict[str, Dict[str, Any]]:
        profiles = dict(self.openai_config.get("profiles", {}) or {})
        normalized: Dict[str, Dict[str, Any]] = {
            str(name).strip(): dict(payload or {})
            for name, payload in profiles.items()
            if str(name).strip()
        }

        legacy_text_name = self._default_legacy_text_profile_name()
        normalized.setdefault(legacy_text_name, self._build_legacy_text_profile())

        legacy_embedding_name = self._default_legacy_embedding_profile_name()
        normalized.setdefault(legacy_embedding_name, self._build_legacy_embedding_profile())

        return normalized

    def _default_legacy_text_profile_name(self) -> str:
        return str(
            self.openai_config.get("legacy_text_profile_name", "") or DEFAULT_TEXT_PROFILE
        ).strip() or DEFAULT_TEXT_PROFILE

    def _default_legacy_embedding_profile_name(self) -> str:
        return str(
            self.openai_config.get("legacy_embedding_profile_name", "") or DEFAULT_EMBEDDING_PROFILE
        ).strip() or DEFAULT_EMBEDDING_PROFILE

    def _build_legacy_text_profile(self) -> Dict[str, Any]:
        model = str(self.openai_config.get("model", "") or "qwen3-max").strip() or "qwen3-max"
        base_url = str(self.openai_config.get("base_url", "") or "").strip()
        api_style = str(self.openai_config.get("api_style", "") or "").strip() or (
            "openai_chat" if base_url else "dashscope_generation"
        )
        provider = self._infer_provider(
            explicit_provider=str(self.openai_config.get("provider", "") or "").strip(),
            base_url=base_url,
            model=model,
        )
        return {
            "provider": provider,
            "api_style": api_style,
            "model": model,
            "base_url": base_url,
            "api_key": str(self.openai_config.get("api_key", "") or "").strip(),
            "api_key_envs": list(self.openai_config.get("api_key_envs", []) or [])
            or self._default_api_key_envs(provider),
            "temperature": float(self.openai_config.get("temperature", 0.7) or 0.7),
            "timeout_s": float(self.openai_config.get("timeout_s", DEFAULT_TIMEOUT_S) or DEFAULT_TIMEOUT_S),
            "max_tokens": int(self.openai_config.get("max_tokens", 0) or 0),
            "capabilities": dict(self.openai_config.get("capabilities", {}) or {}),
            "native_fallback": dict(self.openai_config.get("native_fallback", {}) or {}),
        }

    def _build_legacy_embedding_profile(self) -> Dict[str, Any]:
        embedding_model = str(
            self.openai_config.get("embedding_model", "")
            or self.openai_config.get("model", "")
            or "text-embedding-v4"
        ).strip() or "text-embedding-v4"
        base_url = str(self.openai_config.get("base_url", "") or "").strip()
        provider = self._infer_provider(
            explicit_provider=str(self.openai_config.get("provider", "") or "").strip(),
            base_url=base_url,
            model=embedding_model,
        )
        return {
            "provider": provider,
            "api_style": "openai_embeddings" if base_url else "openai_embeddings",
            "model": embedding_model,
            "base_url": base_url,
            "api_key": str(self.openai_config.get("api_key", "") or "").strip(),
            "api_key_envs": list(self.openai_config.get("api_key_envs", []) or [])
            or self._default_api_key_envs(provider),
            "temperature": 0.0,
            "timeout_s": float(self.openai_config.get("timeout_s", DEFAULT_TIMEOUT_S) or DEFAULT_TIMEOUT_S),
            "max_tokens": 0,
            "capabilities": {"embeddings": True},
            "native_fallback": {},
        }

    def _materialize_profile(
        self,
        profile_name: str,
        payload: Dict[str, Any],
        *,
        profile_kind: str,
    ) -> LLMProviderProfile:
        profile_payload = dict(payload or {})
        fallback_model = (
            self.openai_config.get("embedding_model", "")
            if profile_kind == "embedding"
            else self.openai_config.get("model", "")
        )
        base_url = str(
            profile_payload.get("base_url", self.openai_config.get("base_url", "")) or ""
        ).strip()
        model = str(profile_payload.get("model", fallback_model) or "").strip()
        provider = self._infer_provider(
            explicit_provider=str(profile_payload.get("provider", "") or "").strip(),
            base_url=base_url,
            model=model,
        )
        api_style = str(profile_payload.get("api_style", "") or "").strip()
        if not api_style:
            api_style = "openai_embeddings" if profile_kind == "embedding" else "openai_chat"

        api_key = str(
            profile_payload.get("api_key", self.openai_config.get("api_key", "")) or ""
        ).strip()
        if self._extract_env_placeholder(api_key):
            api_key = ""
        api_key_env = str(
            profile_payload.get("api_key_env", self.openai_config.get("api_key_env", "")) or ""
        ).strip()
        api_key_env = self._normalize_env_name(api_key_env)
        api_key_envs = [
            self._normalize_env_name(item)
            for item in list(
                profile_payload.get(
                    "api_key_envs",
                    self.openai_config.get("api_key_envs", []),
                )
                or []
            )
            if self._normalize_env_name(item)
        ]
        if api_key_env and api_key_env not in api_key_envs:
            api_key_envs.insert(0, api_key_env)
        if not api_key_envs:
            api_key_envs = self._default_api_key_envs(provider)

        api_key_source = f"openai.profiles.{profile_name}.api_key" if api_key else ""
        if not api_key:
            for env_name in api_key_envs:
                env_value = str(os.environ.get(env_name, "") or "").strip()
                if env_value:
                    api_key = env_value
                    api_key_source = env_name
                    break

        if not api_key:
            raise ConfigurationError(
                f"API key not found for profile '{profile_name}' (searched {api_key_envs})"
            )

        temperature_raw = profile_payload.get(
            "temperature",
            self.openai_config.get("temperature", 0.7 if profile_kind == "text" else 0.0),
        )
        try:
            temperature = float(temperature_raw)
        except (TypeError, ValueError):
            temperature = 0.7 if profile_kind == "text" else 0.0

        timeout_raw = profile_payload.get("timeout_s", self.openai_config.get("timeout_s", DEFAULT_TIMEOUT_S))
        try:
            timeout_s = max(float(timeout_raw), 1.0)
        except (TypeError, ValueError):
            timeout_s = DEFAULT_TIMEOUT_S

        max_tokens_raw = profile_payload.get("max_tokens", self.openai_config.get("max_tokens", 0))
        try:
            max_tokens = max(int(max_tokens_raw), 0)
        except (TypeError, ValueError):
            max_tokens = 0

        completion_budget_raw = profile_payload.get(
            "completion_budget_tokens",
            self.openai_config.get("completion_budget_tokens", max_tokens),
        )
        try:
            completion_budget_tokens = max(int(completion_budget_raw), 0)
        except (TypeError, ValueError):
            completion_budget_tokens = max_tokens

        reasoning_profile = self._normalize_reasoning_profile(
            profile_payload.get(
                "reasoning_profile",
                self.openai_config.get("reasoning_profile", DEFAULT_REASONING_PROFILE),
            )
        )
        thinking_mode = self._normalize_thinking_mode(
            profile_payload.get(
                "thinking_mode",
                self.openai_config.get("thinking_mode", DEFAULT_THINKING_MODE),
            )
        )
        strict_json_thinking_mode = self._normalize_optional_thinking_mode(
            profile_payload.get(
                "strict_json_thinking_mode",
                self.openai_config.get("strict_json_thinking_mode", ""),
            )
        )
        reasoning_budget_raw = profile_payload.get(
            "reasoning_budget_tokens",
            self.openai_config.get("reasoning_budget_tokens", 0),
        )
        try:
            reasoning_budget_tokens = max(int(reasoning_budget_raw), 0)
        except (TypeError, ValueError):
            reasoning_budget_tokens = 0

        provider_options = dict(self.openai_config.get("provider_options", {}) or {})
        provider_options.update(dict(profile_payload.get("provider_options", {}) or {}))

        capabilities = dict(profile_payload.get("capabilities", {}) or {})
        if profile_kind == "embedding":
            capabilities.setdefault("embeddings", True)

        return LLMProviderProfile(
            name=profile_name,
            provider=provider,
            api_style=api_style,
            model=model,
            base_url=base_url,
            api_key=api_key,
            api_key_env=api_key_envs[0] if api_key_envs else "",
            api_key_envs=api_key_envs,
            api_key_source=api_key_source,
            temperature=temperature,
            timeout_s=timeout_s,
            max_tokens=max_tokens,
            completion_budget_tokens=completion_budget_tokens,
            reasoning_profile=reasoning_profile,
            thinking_mode=thinking_mode,
            strict_json_thinking_mode=strict_json_thinking_mode,
            reasoning_budget_tokens=reasoning_budget_tokens,
            provider_options=provider_options,
            capabilities=capabilities,
            native_fallback=dict(profile_payload.get("native_fallback", {}) or {}),
        )

    def _default_api_key_envs(self, provider: str) -> List[str]:
        provider_norm = str(provider or "").strip().lower()
        if provider_norm == "qwen":
            return ["DASHSCOPE_API_KEY", "OPENAI_API_KEY"]
        return ["OPENAI_API_KEY"]

    def _extract_env_placeholder(self, value: Any) -> str:
        text = str(value or "").strip()
        match = ENV_PLACEHOLDER_PATTERN.match(text)
        return str(match.group(1)).strip() if match else ""

    def _normalize_env_name(self, value: Any) -> str:
        text = str(value or "").strip()
        placeholder = self._extract_env_placeholder(text)
        return placeholder or text

    def _normalize_reasoning_profile(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"", "auto", "balanced", "default", "medium"}:
            return "balanced"
        if normalized in {"off", "none", "disabled", "low", "minimal"}:
            return "minimal"
        if normalized in {"high", "deep", "max"}:
            return "high"
        return normalized

    def _normalize_thinking_mode(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"", "auto", "default"}:
            return "auto"
        if normalized in {"on", "true", "enabled", "enable"}:
            return "enabled"
        if normalized in {"off", "false", "disabled", "disable"}:
            return "disabled"
        return normalized

    def _normalize_optional_thinking_mode(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return self._normalize_thinking_mode(text)

    def _infer_provider(self, *, explicit_provider: str, base_url: str, model: str) -> str:
        if explicit_provider:
            return explicit_provider
        url = str(base_url or "").lower()
        model_norm = str(model or "").lower()
        if "dashscope" in url or model_norm.startswith("qwen"):
            return "qwen"
        if "anthropic" in url or model_norm.startswith("claude"):
            return "anthropic"
        if "bigmodel" in url or model_norm.startswith("glm"):
            return "glm"
        if "minimax" in url or model_norm.startswith("abab") or "minimax" in model_norm:
            return "minimax"
        if "openai" in url or model_norm.startswith("gpt"):
            return "openai"
        return "openai_compatible"


class OpenAICompatibleAdapter:
    def generate_text(
        self,
        profile: LLMProviderProfile,
        messages: List[Dict[str, Any]],
        *,
        expects_json: bool = True,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        completion_budget_tokens: Optional[int] = None,
        reasoning_profile: str = "",
        thinking_mode: str = "",
        reasoning_budget_tokens: Optional[int] = None,
    ) -> LLMCallResult:
        client = OpenAI(
            api_key=profile.api_key,
            base_url=profile.base_url or None,
            timeout=profile.timeout_s,
        )
        resolved_reasoning_profile = self._resolve_reasoning_profile(
            profile.reasoning_profile,
            reasoning_profile,
        )
        resolved_thinking_mode = self._resolve_thinking_mode(
            profile.thinking_mode,
            thinking_mode,
            expects_json=expects_json,
            strict_json_profile_value=profile.strict_json_thinking_mode,
        )
        resolved_completion_budget = self._resolve_completion_budget(
            profile,
            max_tokens=max_tokens,
            completion_budget_tokens=completion_budget_tokens,
        )
        resolved_reasoning_budget = self._resolve_reasoning_budget(
            profile,
            reasoning_budget_tokens=reasoning_budget_tokens,
        )
        request_payload: Dict[str, Any] = {
            "model": profile.model,
            "messages": list(messages or []),
            "temperature": float(profile.temperature if temperature is None else temperature),
        }
        if resolved_completion_budget > 0:
            if str(profile.provider or "").strip().lower() == "openai":
                request_payload["max_completion_tokens"] = resolved_completion_budget
            else:
                request_payload["max_tokens"] = resolved_completion_budget
        if expects_json and bool(profile.capabilities.get("native_json_output", False)):
            request_payload["response_format"] = {"type": "json_object"}
        extra_body = self._build_extra_body(
            profile=profile,
            reasoning_profile=resolved_reasoning_profile,
            thinking_mode=resolved_thinking_mode,
            reasoning_budget_tokens=resolved_reasoning_budget,
        )
        if extra_body:
            request_payload["extra_body"] = extra_body
        reasoning_effort = self._build_reasoning_effort(
            profile=profile,
            reasoning_profile=resolved_reasoning_profile,
        )
        if reasoning_effort:
            request_payload["reasoning_effort"] = reasoning_effort

        try:
            response = client.chat.completions.create(**request_payload)
        except AuthenticationError as exc:
            raise LLMError(f"[auth_error] {exc}") from exc
        except NotFoundError as exc:
            raise LLMError(f"[model_error] {exc}") from exc
        except BadRequestError as exc:
            raise LLMError(f"[request_error] {exc}") from exc
        except APIConnectionError as exc:
            raise LLMError(f"[connection_error] {exc}") from exc
        except APIStatusError as exc:
            raise LLMError(f"[status_error] {exc}") from exc
        except Exception as exc:
            raise LLMError(f"[unknown_error] {exc}") from exc

        content = self._extract_chat_message_content(response, expects_json=expects_json)
        status_code = int(getattr(response, "status_code", 200) or 200)
        response_payload = response.model_dump() if hasattr(response, "model_dump") else response
        return LLMCallResult(
            content=content,
            status_code=status_code,
            provider=profile.provider,
            profile=profile.name,
            model=profile.model,
            api_style=profile.api_style,
            key_source=profile.api_key_source,
            key_source_masked=profile.key_source_masked,
            reasoning_profile=resolved_reasoning_profile,
            thinking_mode=resolved_thinking_mode,
            strict_json_thinking_mode=profile.strict_json_thinking_mode,
            completion_budget_tokens=resolved_completion_budget,
            reasoning_budget_tokens=resolved_reasoning_budget,
            request_payload=request_payload,
            response_payload=response_payload,
        )

    def _resolve_completion_budget(
        self,
        profile: LLMProviderProfile,
        *,
        max_tokens: Optional[int],
        completion_budget_tokens: Optional[int],
    ) -> int:
        if completion_budget_tokens is not None:
            try:
                return max(int(completion_budget_tokens), 0)
            except (TypeError, ValueError):
                return 0
        if max_tokens is not None:
            try:
                return max(int(max_tokens), 0)
            except (TypeError, ValueError):
                return 0
        if int(profile.completion_budget_tokens or 0) > 0:
            return int(profile.completion_budget_tokens)
        return max(int(profile.max_tokens or 0), 0)

    def _resolve_reasoning_profile(self, profile_value: str, override_value: str) -> str:
        return str(override_value or profile_value or DEFAULT_REASONING_PROFILE).strip().lower()

    def _resolve_thinking_mode(
        self,
        profile_value: str,
        override_value: str,
        *,
        expects_json: bool,
        strict_json_profile_value: str,
    ) -> str:
        if str(override_value or "").strip():
            return str(override_value).strip().lower()
        if expects_json and str(strict_json_profile_value or "").strip():
            return str(strict_json_profile_value).strip().lower()
        return str(profile_value or DEFAULT_THINKING_MODE).strip().lower()

    def _resolve_reasoning_budget(
        self,
        profile: LLMProviderProfile,
        *,
        reasoning_budget_tokens: Optional[int],
    ) -> int:
        if reasoning_budget_tokens is not None:
            try:
                return max(int(reasoning_budget_tokens), 0)
            except (TypeError, ValueError):
                return 0
        return max(int(profile.reasoning_budget_tokens or 0), 0)

    def _build_reasoning_effort(
        self,
        *,
        profile: LLMProviderProfile,
        reasoning_profile: str,
    ) -> str:
        if str(profile.provider or "").strip().lower() != "openai":
            return ""
        mapping = {
            "minimal": "low",
            "balanced": "medium",
            "high": "high",
        }
        return str(mapping.get(str(reasoning_profile or "").strip().lower(), "") or "")

    def _build_extra_body(
        self,
        *,
        profile: LLMProviderProfile,
        reasoning_profile: str,
        thinking_mode: str,
        reasoning_budget_tokens: int,
    ) -> Dict[str, Any]:
        extra_body = dict((profile.provider_options or {}).get("extra_body", {}) or {})
        provider_norm = str(profile.provider or "").strip().lower()
        if provider_norm == "qwen":
            enable_thinking = None
            if thinking_mode == "enabled":
                enable_thinking = True
            elif thinking_mode == "disabled":
                enable_thinking = False
            elif str(reasoning_profile or "").strip().lower() in {"balanced", "high"}:
                enable_thinking = True
            if enable_thinking is not None:
                extra_body["enable_thinking"] = enable_thinking
            if reasoning_budget_tokens > 0 and enable_thinking is not False:
                extra_body["thinking_budget"] = reasoning_budget_tokens
        return extra_body

    def generate_embeddings(
        self,
        profile: LLMProviderProfile,
        inputs: Iterable[str],
    ) -> LLMEmbeddingResult:
        client = OpenAI(
            api_key=profile.api_key,
            base_url=profile.base_url or None,
            timeout=profile.timeout_s,
        )
        request_payload: Dict[str, Any] = {
            "model": profile.model,
            "input": list(inputs or []),
        }
        try:
            response = client.embeddings.create(**request_payload)
        except Exception as exc:
            raise LLMError(f"[embedding_error] {exc}") from exc
        response_payload = response.model_dump() if hasattr(response, "model_dump") else response
        vectors = [
            list(getattr(item, "embedding", []) or [])
            for item in list(getattr(response, "data", []) or [])
        ]
        return LLMEmbeddingResult(
            vectors=vectors,
            provider=profile.provider,
            profile=profile.name,
            model=profile.model,
            api_style=profile.api_style,
            key_source=profile.api_key_source,
            key_source_masked=profile.key_source_masked,
            request_payload=request_payload,
            response_payload=response_payload,
        )

    def _extract_chat_message_content(
        self,
        response: Any,
        *,
        expects_json: bool = False,
    ) -> str:
        choices = list(getattr(response, "choices", []) or [])
        if not choices:
            raise LLMError("OpenAI-compatible chat completion returned no choices")
        message = getattr(choices[0], "message", None)
        content_text = self._flatten_message_text(getattr(message, "content", ""))
        if content_text.strip():
            return content_text

        if expects_json:
            reasoning_text = self._flatten_message_text(
                getattr(message, "reasoning_content", "")
            ).strip()
            if reasoning_text:
                try:
                    return extract_json_object_text(reasoning_text)
                except ValueError:
                    return reasoning_text

        return content_text

    def _flatten_message_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: List[str] = []
            for item in value:
                text = self._flatten_message_text(item)
                if text:
                    parts.append(text)
            return "\n".join(part for part in parts if str(part).strip())
        if hasattr(value, "text") and getattr(value, "text", None):
            return str(getattr(value, "text"))
        if isinstance(value, dict):
            text = value.get("text") or value.get("content") or value.get("value")
            if text:
                return self._flatten_message_text(text)
            return ""
        return str(value or "")


class OpenAIResponsesAdapter:
    def generate_text(
        self,
        profile: LLMProviderProfile,
        messages: List[Dict[str, Any]],
        *,
        expects_json: bool = True,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        completion_budget_tokens: Optional[int] = None,
        reasoning_profile: str = "",
        thinking_mode: str = "",
        reasoning_budget_tokens: Optional[int] = None,
    ) -> LLMCallResult:
        resolved_completion_budget = self._resolve_completion_budget(
            profile,
            max_tokens=max_tokens,
            completion_budget_tokens=completion_budget_tokens,
        )
        resolved_reasoning_profile = self._resolve_reasoning_profile(
            profile.reasoning_profile,
            reasoning_profile,
        )
        request_payload: Dict[str, Any] = {
            "model": profile.model,
            "input": self._build_responses_input(messages),
            "temperature": float(profile.temperature if temperature is None else temperature),
        }
        if resolved_completion_budget > 0:
            request_payload["max_output_tokens"] = resolved_completion_budget
        reasoning_effort = self._build_reasoning_effort(
            profile=profile,
            reasoning_profile=resolved_reasoning_profile,
        )
        if reasoning_effort:
            request_payload["reasoning"] = {"effort": reasoning_effort}

        headers = {
            "Authorization": f"Bearer {profile.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(
                self._resolve_responses_url(profile),
                headers=headers,
                json=request_payload,
                timeout=profile.timeout_s,
            )
        except requests.RequestException as exc:
            raise LLMError(f"[connection_error] {exc}") from exc

        status_code = int(response.status_code)
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise LLMError(
                f"[status_error] OpenAI-compatible responses endpoint returned non-JSON payload: HTTP {status_code}"
            ) from exc

        if not response.ok:
            error_payload = response_payload.get("error", response_payload)
            raise LLMError(
                f"[status_error] HTTP {status_code} - {json.dumps(error_payload, ensure_ascii=False)}"
            )

        return LLMCallResult(
            content=self._extract_responses_text(response_payload, expects_json=expects_json),
            status_code=status_code,
            provider=profile.provider,
            profile=profile.name,
            model=profile.model,
            api_style=profile.api_style,
            key_source=profile.api_key_source,
            key_source_masked=profile.key_source_masked,
            reasoning_profile=resolved_reasoning_profile,
            thinking_mode=str(thinking_mode or profile.thinking_mode or DEFAULT_THINKING_MODE).strip().lower(),
            strict_json_thinking_mode=profile.strict_json_thinking_mode,
            completion_budget_tokens=resolved_completion_budget,
            reasoning_budget_tokens=max(int(profile.reasoning_budget_tokens or 0), 0),
            request_payload=request_payload,
            response_payload=response_payload,
        )

    def _resolve_completion_budget(
        self,
        profile: LLMProviderProfile,
        *,
        max_tokens: Optional[int],
        completion_budget_tokens: Optional[int],
    ) -> int:
        if completion_budget_tokens is not None:
            try:
                return max(int(completion_budget_tokens), 0)
            except (TypeError, ValueError):
                return 0
        if max_tokens is not None:
            try:
                return max(int(max_tokens), 0)
            except (TypeError, ValueError):
                return 0
        if int(profile.completion_budget_tokens or 0) > 0:
            return int(profile.completion_budget_tokens)
        return max(int(profile.max_tokens or 0), 0)

    def _resolve_reasoning_profile(self, profile_value: str, override_value: str) -> str:
        return str(override_value or profile_value or DEFAULT_REASONING_PROFILE).strip().lower()

    def _build_reasoning_effort(
        self,
        *,
        profile: LLMProviderProfile,
        reasoning_profile: str,
    ) -> str:
        provider_norm = str(profile.provider or "").strip().lower()
        if provider_norm not in {"openai", "openai_compatible"}:
            return ""
        mapping = {
            "minimal": "low",
            "balanced": "medium",
            "high": "high",
        }
        return str(mapping.get(str(reasoning_profile or "").strip().lower(), "") or "")

    def _resolve_responses_url(self, profile: LLMProviderProfile) -> str:
        base_url = str(profile.base_url or "").rstrip("/")
        if not base_url:
            return "https://api.openai.com/v1/responses"
        if base_url.lower().endswith("/responses"):
            return base_url
        return f"{base_url}/responses"

    def _build_responses_input(self, messages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        payload: List[Dict[str, Any]] = []
        for message in list(messages or []):
            role = str(message.get("role", "user") or "user").strip().lower()
            if role == "system":
                role = "developer"
            if role not in {"developer", "user", "assistant"}:
                role = "user"
            payload.append(
                {
                    "type": "message",
                    "role": role,
                    "content": [
                        {
                            "type": "input_text",
                            "text": self._coerce_message_text(message.get("content", "")),
                        }
                    ],
                }
            )
        return payload

    def _coerce_message_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or item.get("value")
                    if text is not None:
                        parts.append(str(text))
                        continue
                parts.append(json.dumps(item, ensure_ascii=False))
            return "\n".join(parts)
        if isinstance(content, dict):
            text = content.get("text") or content.get("content") or content.get("value")
            if text is not None:
                return str(text)
            return json.dumps(content, ensure_ascii=False)
        return str(content or "")

    def _extract_responses_text(self, response_payload: Dict[str, Any], *, expects_json: bool) -> str:
        output = response_payload.get("output")
        if isinstance(output, list):
            texts: List[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "message":
                    for content_item in list(item.get("content", []) or []):
                        if not isinstance(content_item, dict):
                            continue
                        text = (
                            content_item.get("text")
                            or content_item.get("output_text")
                            or content_item.get("content")
                            or content_item.get("value")
                        )
                        if text:
                            texts.append(str(text))
            joined = "\n".join(part for part in texts if str(part).strip()).strip()
            if joined:
                return extract_json_object_text(joined) if expects_json else joined

        text = response_payload.get("output_text")
        if text:
            return extract_json_object_text(str(text)) if expects_json else str(text)

        try:
            return extract_json_object_text(json.dumps(response_payload, ensure_ascii=False))
        except ValueError:
            return str(response_payload or "")


class DashScopeNativeAdapter:
    def generate_text(
        self,
        profile: LLMProviderProfile,
        messages: List[Dict[str, Any]],
        *,
        expects_json: bool = True,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMCallResult:
        dashscope.api_key = profile.api_key
        request_payload: Dict[str, Any] = {
            "model": profile.model,
            "messages": list(messages or []),
            "result_format": "message",
            "temperature": float(profile.temperature if temperature is None else temperature),
        }
        if expects_json:
            request_payload["response_format"] = {"type": "json_object"}
        if (profile.max_tokens if max_tokens is None else int(max_tokens or 0)) > 0:
            request_payload["max_tokens"] = profile.max_tokens if max_tokens is None else int(max_tokens)

        response = dashscope.Generation.call(**request_payload)
        status_code = getattr(response, "status_code", None)
        if status_code != HTTPStatus.OK:
            raise LLMError(
                "DashScope native API 调用失败: "
                f"{getattr(response, 'code', '')} - {getattr(response, 'message', '')}"
            )
        return LLMCallResult(
            content=self._extract_message_content(response),
            status_code=int(status_code),
            provider=profile.provider,
            profile=profile.name,
            model=profile.model,
            api_style="dashscope_generation",
            key_source=profile.api_key_source,
            key_source_masked=profile.key_source_masked,
            request_payload=request_payload,
            response_payload=response,
        )

    def _extract_message_content(self, response: Any) -> str:
        content = getattr(
            getattr(response, "output", None),
            "choices",
            [type("Choice", (), {"message": type("Message", (), {"content": ""})()})()],
        )[0].message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or item.get("value") or ""
                    if text:
                        parts.append(str(text))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        if isinstance(content, dict):
            return str(content.get("text") or content.get("content") or content.get("value") or "")
        return str(content or "")


class LLMGateway:
    def __init__(
        self,
        *,
        profile_resolver: LLMProfileResolver,
        openai_adapter: Optional[OpenAICompatibleAdapter] = None,
        responses_adapter: Optional[OpenAIResponsesAdapter] = None,
        dashscope_native_adapter: Optional[DashScopeNativeAdapter] = None,
    ):
        self.profile_resolver = profile_resolver
        self.openai_adapter = openai_adapter or OpenAICompatibleAdapter()
        self.responses_adapter = responses_adapter or OpenAIResponsesAdapter()
        self.dashscope_native_adapter = dashscope_native_adapter or DashScopeNativeAdapter()

    def generate_text(
        self,
        messages: List[Dict[str, Any]],
        *,
        profile_name: str = "",
        expects_json: bool = True,
        requested_capabilities: Optional[Sequence[str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        completion_budget_tokens: Optional[int] = None,
        reasoning_profile: str = "",
        thinking_mode: str = "",
        reasoning_budget_tokens: Optional[int] = None,
    ) -> LLMCallResult:
        profile = self.profile_resolver.resolve_text_profile(profile_name)
        requested = [str(item).strip() for item in list(requested_capabilities or []) if str(item).strip()]
        if self._should_force_native(profile, requested):
            result = self.dashscope_native_adapter.generate_text(
                profile,
                messages,
                expects_json=expects_json,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            result.fallback_used = True
            result.fallback_reason = "capability_gate"
            return result

        if profile.api_style == "dashscope_generation":
            return self.dashscope_native_adapter.generate_text(
                profile,
                messages,
                expects_json=expects_json,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        if profile.api_style == "openai_responses":
            return self.responses_adapter.generate_text(
                profile,
                messages,
                expects_json=expects_json,
                temperature=temperature,
                max_tokens=max_tokens,
                completion_budget_tokens=completion_budget_tokens,
                reasoning_profile=reasoning_profile,
                thinking_mode=thinking_mode,
                reasoning_budget_tokens=reasoning_budget_tokens,
            )

        return self.openai_adapter.generate_text(
            profile,
            messages,
            expects_json=expects_json,
            temperature=temperature,
            max_tokens=max_tokens,
            completion_budget_tokens=completion_budget_tokens,
            reasoning_profile=reasoning_profile,
            thinking_mode=thinking_mode,
            reasoning_budget_tokens=reasoning_budget_tokens,
        )

    def generate_embeddings(
        self,
        inputs: Iterable[str],
        *,
        profile_name: str = "",
    ) -> LLMEmbeddingResult:
        profile = self.profile_resolver.resolve_embedding_profile(profile_name)
        if profile.api_style != "openai_embeddings":
            raise LLMError(
                f"Unsupported embedding api_style for profile '{profile.name}': {profile.api_style}"
            )
        return self.openai_adapter.generate_embeddings(profile, inputs)

    def resolve_text_profile(self, profile_name: str = "") -> LLMProviderProfile:
        return self.profile_resolver.resolve_text_profile(profile_name)

    def resolve_embedding_profile(self, profile_name: str = "") -> LLMProviderProfile:
        return self.profile_resolver.resolve_embedding_profile(profile_name)

    def _should_force_native(self, profile: LLMProviderProfile, requested_capabilities: Sequence[str]) -> bool:
        fallback_cfg = dict(profile.native_fallback or {})
        if not bool(fallback_cfg.get("enabled", False)):
            return False
        if str(profile.api_style or "").strip().lower() == "dashscope_generation":
            return False
        supported = {
            str(key).strip()
            for key, value in dict(profile.capabilities or {}).items()
            if bool(value)
        }
        required = {item for item in list(requested_capabilities or []) if item}
        return bool(required and not required.issubset(supported))


def build_legacy_gateway(
    *,
    api_key: str,
    model: str,
    temperature: float = 0.7,
    base_url: str | None = None,
    api_mode: str = "dashscope_generation",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_tokens: int = 0,
) -> LLMGateway:
    api_style = "dashscope_generation"
    if str(api_mode or "").strip().lower() not in {"", "dashscope_generation"}:
        api_style = "openai_chat"
    if str(base_url or "").strip():
        api_style = "openai_chat"
    provider = "qwen" if str(model or "").strip().lower().startswith("qwen") else "openai_compatible"
    implicit_config = {
        "legacy_text_profile_name": "legacy_default",
        "legacy_embedding_profile_name": "legacy_embedding_default",
        "model": model,
        "api_key": api_key,
        "base_url": str(base_url or ""),
        "temperature": float(temperature),
        "timeout_s": float(timeout_s),
        "max_tokens": int(max_tokens or 0),
        "provider": provider,
        "api_style": api_style,
        "embedding_model": "text-embedding-v4",
        "profiles": {
            "legacy_default": {
                "provider": provider,
                "api_style": api_style,
                "model": model,
                "base_url": str(base_url or ""),
                "api_key": api_key,
                "temperature": float(temperature),
                "timeout_s": float(timeout_s),
                "max_tokens": int(max_tokens or 0),
                "capabilities": {"native_json_output": api_style == "openai_chat"},
                "native_fallback": {"enabled": False},
            },
            "legacy_embedding_default": {
                "provider": provider,
                "api_style": "openai_embeddings",
                "model": "text-embedding-v4",
                "base_url": str(base_url or ""),
                "api_key": api_key,
                "timeout_s": float(timeout_s),
                "capabilities": {"embeddings": True},
            },
        },
    }
    return LLMGateway(profile_resolver=LLMProfileResolver(implicit_config))
