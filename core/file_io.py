"""
Shared file I/O helpers used across the repository.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from core.exceptions import ConfigurationError


def load_yaml(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        raise ConfigurationError(f"Configuration file not found: {path}")

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML format: {exc}") from exc

    return _replace_env_vars(payload or {})


def save_yaml(data: dict[str, Any], file_path: str | Path) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def load_json(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(data: dict[str, Any], file_path: str | Path, indent: int = 2) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=indent, ensure_ascii=False),
        encoding="utf-8",
    )


def load_env(env_file: str | Path = ".env") -> None:
    path = Path(env_file)
    if path.exists():
        load_dotenv(path)
        print(f"Loaded environment variables from {path}")
    else:
        print(f"Environment file not found: {path}")


def ensure_dir(directory: str | Path) -> None:
    Path(directory).mkdir(parents=True, exist_ok=True)


def _replace_env_vars(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _replace_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_env_vars(item) for item in value]
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            return os.getenv(value[2:-1], value)
        if value.startswith("$"):
            return os.getenv(value[1:], value)
    return value


__all__ = [
    "ensure_dir",
    "load_env",
    "load_json",
    "load_yaml",
    "save_json",
    "save_yaml",
]
