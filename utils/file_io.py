"""
工具函数：文件读写
"""

import os
import json
import yaml
from typing import Dict, Any
from pathlib import Path
from dotenv import load_dotenv

from core.exceptions import ConfigurationError


def load_yaml(file_path: str) -> Dict[str, Any]:
    """
    加载YAML配置文件

    Args:
        file_path: YAML文件路径

    Returns:
        配置字典

    Raises:
        ConfigurationError: 文件不存在或格式错误
    """
    if not os.path.exists(file_path):
        raise ConfigurationError(f"Configuration file not found: {file_path}")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # 处理环境变量替换
        config = _replace_env_vars(config)

        return config

    except yaml.YAMLError as e:
        raise ConfigurationError(f"Invalid YAML format: {e}")


def save_yaml(data: Dict[str, Any], file_path: str):
    """
    保存数据为YAML文件

    Args:
        data: 要保存的数据
        file_path: 输出文件路径
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def load_json(file_path: str) -> Dict[str, Any]:
    """
    加载JSON文件

    Args:
        file_path: JSON文件路径

    Returns:
        数据字典
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(data: Dict[str, Any], file_path: str, indent: int = 2):
    """
    保存数据为JSON文件

    Args:
        data: 要保存的数据
        file_path: 输出文件路径
        indent: 缩进空格数
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


def _replace_env_vars(config: Any) -> Any:
    """
    递归替换配置中的环境变量

    支持格式：${VAR_NAME} 或 $VAR_NAME

    Args:
        config: 配置数据（可以是dict, list, str等）

    Returns:
        替换后的配置
    """
    if isinstance(config, dict):
        return {k: _replace_env_vars(v) for k, v in config.items()}
    elif isinstance(config, list):
        return [_replace_env_vars(item) for item in config]
    elif isinstance(config, str):
        # 替换 ${VAR_NAME} 格式
        if config.startswith("${") and config.endswith("}"):
            var_name = config[2:-1]
            return os.getenv(var_name, config)
        # 替换 $VAR_NAME 格式
        elif config.startswith("$"):
            var_name = config[1:]
            return os.getenv(var_name, config)
        return config
    else:
        return config


def load_env(env_file: str = ".env"):
    """
    加载环境变量文件

    Args:
        env_file: .env文件路径
    """
    if os.path.exists(env_file):
        load_dotenv(env_file)
        print(f"✓ Loaded environment variables from {env_file}")
    else:
        print(f"⚠ Environment file not found: {env_file}")


def ensure_dir(directory: str):
    """
    确保目录存在，不存在则创建

    Args:
        directory: 目录路径
    """
    os.makedirs(directory, exist_ok=True)
