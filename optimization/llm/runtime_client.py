"""
Runtime-compatible LLM client for DashScope and OpenAI-compatible proxies.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import dashscope
import requests

from core.exceptions import LLMError


DEFAULT_LLM_MODEL = "qwen3-max"
DEFAULT_LLM_API_MODE = "dashscope_generation"
DEFAULT_LLM_TIMEOUT_S = 120.0


def extract_json_object_text(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced_match:
        return fenced_match.group(1)

    start = text.find("{")
    if start < 0:
        raise ValueError("JSON object start not found")

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise ValueError("JSON object end not found")


def mask_secret(secret: str) -> str:
    token = str(secret or "").strip()
    if not token:
        return ""
    if len(token) <= 10:
        return f"{token[:2]}...{token[-2:]}"
    return f"{token[:8]}...{token[-4:]}"


def resolve_llm_runtime_config(
    raw_config: Dict[str, Any] | None,
    *,
    default_model: str = DEFAULT_LLM_MODEL,
    api_key_env_names: Sequence[str] = ("DASHSCOPE_API_KEY", "OPENAI_API_KEY"),
) -> Tuple[Dict[str, Any], str]:
    config = dict(raw_config or {})

    env_model = str(os.environ.get("OPENAI_MODEL", "") or "").strip()
    if env_model:
        config["model"] = env_model
    else:
        config["model"] = str(config.get("model", "") or default_model).strip() or default_model

    env_base_url = str(os.environ.get("OPENAI_BASE_URL", "") or "").strip()
    env_responses_url = str(os.environ.get("OPENAI_RESPONSES_URL", "") or "").strip()
    if env_base_url:
        config["base_url"] = env_base_url
    else:
        config["base_url"] = str(config.get("base_url", "") or "").strip()
    if env_responses_url:
        config["responses_url"] = env_responses_url
    else:
        config["responses_url"] = str(config.get("responses_url", "") or "").strip()

    env_api_mode = str(os.environ.get("OPENAI_API_MODE", "") or "").strip()
    configured_api_mode = str(config.get("api_mode", "") or "").strip()
    inferred_api_mode = ""
    candidate_responses_ref = str(config.get("responses_url", "") or config.get("base_url", "") or "").strip()
    if candidate_responses_ref.lower().endswith("/responses"):
        inferred_api_mode = "openai_responses"
    config["api_mode"] = (
        env_api_mode
        or configured_api_mode
        or inferred_api_mode
        or DEFAULT_LLM_API_MODE
    )

    timeout_raw = os.environ.get("OPENAI_TIMEOUT_S", config.get("timeout_s", DEFAULT_LLM_TIMEOUT_S))
    try:
        timeout_s = float(timeout_raw)
    except (TypeError, ValueError):
        timeout_s = DEFAULT_LLM_TIMEOUT_S
    config["timeout_s"] = max(timeout_s, 1.0)

    try:
        config["temperature"] = float(config.get("temperature", 0.7))
    except (TypeError, ValueError):
        config["temperature"] = 0.7

    api_key = str(config.get("api_key", "") or "").strip()
    api_key_source = "config.openai.api_key" if api_key else ""
    if not api_key:
        for env_name in api_key_env_names:
            env_value = str(os.environ.get(env_name, "") or "").strip()
            if env_value:
                api_key = env_value
                api_key_source = env_name
                break
    config["api_key"] = api_key

    return config, api_key_source


@dataclass
class LLMCallResult:
    content: str
    status_code: int
    request_payload: Dict[str, Any]
    response_payload: Any


class RuntimeLLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        api_mode: str = DEFAULT_LLM_API_MODE,
        base_url: str | None = None,
        responses_url: str | None = None,
        timeout_s: float = DEFAULT_LLM_TIMEOUT_S,
    ):
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip() or DEFAULT_LLM_MODEL
        self.temperature = float(temperature)
        self.api_mode = str(api_mode or DEFAULT_LLM_API_MODE).strip().lower() or DEFAULT_LLM_API_MODE
        self.base_url = str(base_url or "").strip()
        self.responses_url = str(responses_url or "").strip()
        self.timeout_s = max(float(timeout_s), 1.0)

        if self.api_mode == "dashscope_generation":
            dashscope.api_key = self.api_key

    def generate_text(self, messages: List[Dict[str, Any]]) -> LLMCallResult:
        if self.api_mode == "dashscope_generation":
            return self._call_dashscope_generation(messages)
        if self.api_mode == "openai_responses":
            return self._call_openai_responses(messages)
        raise LLMError(f"Unsupported LLM api_mode: {self.api_mode}")

    def _call_dashscope_generation(self, messages: List[Dict[str, Any]]) -> LLMCallResult:
        request_payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        response = dashscope.Generation.call(
            model=self.model,
            messages=messages,
            result_format="message",
            temperature=self.temperature,
            response_format={"type": "json_object"},
        )
        status_code = getattr(response, "status_code", None)
        if status_code != HTTPStatus.OK:
            raise LLMError(
                "DashScope API 调用失败: "
                f"{getattr(response, 'code', '')} - {getattr(response, 'message', '')}"
            )
        return LLMCallResult(
            content=self._extract_dashscope_message_content(response),
            status_code=int(status_code),
            request_payload=request_payload,
            response_payload=response,
        )

    def _call_openai_responses(self, messages: List[Dict[str, Any]]) -> LLMCallResult:
        url = self._resolve_responses_url()
        request_payload: Dict[str, Any] = {
            "model": self.model,
            "input": self._build_responses_input(messages),
            "temperature": self.temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(
                url,
                headers=headers,
                json=request_payload,
                timeout=self.timeout_s,
            )
        except requests.RequestException as exc:
            raise LLMError(f"OpenAI-compatible proxy 请求失败: {exc}") from exc

        status_code = int(response.status_code)
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise LLMError(
                f"OpenAI-compatible proxy 返回了非 JSON 响应: HTTP {status_code}"
            ) from exc

        if not response.ok:
            error_payload = response_payload.get("error", response_payload)
            raise LLMError(
                f"OpenAI-compatible proxy 调用失败: HTTP {status_code} - "
                f"{json.dumps(error_payload, ensure_ascii=False)}"
            )

        return LLMCallResult(
            content=self._extract_openai_responses_text(response_payload),
            status_code=status_code,
            request_payload=request_payload,
            response_payload=response_payload,
        )

    def _resolve_responses_url(self) -> str:
        if self.responses_url:
            return self.responses_url
        base_url = str(self.base_url or "").rstrip("/")
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

    def _extract_dashscope_message_content(self, response: Any) -> str:
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

    def _extract_openai_responses_text(self, payload: Dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        texts: List[str] = []
        for item in list(payload.get("output", []) or []):
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "") or "").strip().lower()
            if item_type in {"output_text", "text"}:
                text = item.get("text") or item.get("output_text") or item.get("content")
                if text:
                    texts.append(str(text))
                continue
            if item_type != "message":
                continue
            for part in list(item.get("content", []) or []):
                if isinstance(part, dict):
                    text = part.get("text") or part.get("output_text") or part.get("content")
                    if text:
                        texts.append(str(text))
                elif isinstance(part, str) and part.strip():
                    texts.append(part)

        combined = "\n".join(text for text in texts if str(text).strip())
        if combined.strip():
            return combined

        raise LLMError("OpenAI-compatible proxy 响应中未找到文本输出")
