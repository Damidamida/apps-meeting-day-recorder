from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "storage": {
        "root": "MeetingSummaries",
    },
    "obs": {
        "enabled": False,
        "websocket_host": "localhost",
        "websocket_port": 4455,
        "websocket_password": "",
    },
    "summary": {
        "enabled": False,
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "",
        "env_file": "",
        "timeout_seconds": 120,
        "max_chars_per_chunk": 20000,
    },
}


def _default_config() -> dict[str, Any]:
    return {
        **DEFAULT_CONFIG,
        "storage": dict(DEFAULT_CONFIG["storage"]),
        "obs": dict(DEFAULT_CONFIG["obs"]),
        "summary": dict(DEFAULT_CONFIG["summary"]),
        "_warnings": [],
    }


def load_config(path: Path = Path("config.yaml")) -> dict[str, Any]:
    config = _default_config()
    if not path.exists():
        return config

    try:
        with path.open(encoding="utf-8") as config_file:
            loaded = yaml.safe_load(config_file) or {}
    except yaml.YAMLError:
        config["_warnings"].append(
            "config.yaml содержит ошибку YAML. Используются безопасные настройки по умолчанию."
        )
        return config

    if not isinstance(loaded, dict):
        config["_warnings"].append(
            "config.yaml должен быть YAML-словарем. Используются безопасные настройки по умолчанию."
        )
        return config

    storage = _section(loaded, "storage", config)
    obs = _section(loaded, "obs", config)
    summary = _section(loaded, "summary", config)
    config.update(loaded)
    config["storage"] = {**DEFAULT_CONFIG["storage"], **storage}
    config["obs"] = _normalize_obs({**DEFAULT_CONFIG["obs"], **obs}, config)
    config["summary"] = _normalize_summary({**DEFAULT_CONFIG["summary"], **summary}, config)
    return config


def _section(loaded: dict[str, Any], name: str, config: dict[str, Any]) -> dict[str, Any]:
    section = loaded.get(name, {})
    if section is None:
        return {}
    if not isinstance(section, dict):
        config["_warnings"].append(
            f"Секция `{name}` в config.yaml имеет неверный тип. Используются безопасные значения."
        )
        return {}
    return section


def _normalize_obs(obs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    obs["enabled"] = _safe_bool(obs.get("enabled"), False)
    obs["websocket_host"] = str(obs.get("websocket_host") or "localhost").strip() or "localhost"
    obs["websocket_port"] = _safe_int(
        obs.get("websocket_port"),
        int(DEFAULT_CONFIG["obs"]["websocket_port"]),
        "obs.websocket_port",
        config,
    )
    obs["websocket_password"] = str(obs.get("websocket_password") or "")
    return obs


def _normalize_summary(summary: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    summary["enabled"] = _safe_bool(summary.get("enabled"), False)
    summary["provider"] = str(summary.get("provider") or "openai").strip() or "openai"
    summary["model"] = str(summary.get("model") or DEFAULT_CONFIG["summary"]["model"]).strip()
    summary["api_key_env"] = (
        str(summary.get("api_key_env") or "OPENAI_API_KEY").strip() or "OPENAI_API_KEY"
    )
    summary["base_url"] = str(summary.get("base_url") or "").strip()
    summary["env_file"] = str(summary.get("env_file") or "").strip()
    summary["timeout_seconds"] = _safe_int(
        summary.get("timeout_seconds"),
        int(DEFAULT_CONFIG["summary"]["timeout_seconds"]),
        "summary.timeout_seconds",
        config,
    )
    summary["max_chars_per_chunk"] = _safe_int(
        summary.get("max_chars_per_chunk"),
        int(DEFAULT_CONFIG["summary"]["max_chars_per_chunk"]),
        "summary.max_chars_per_chunk",
        config,
    )
    return summary


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    return default


def _safe_int(value: Any, default: int, name: str, config: dict[str, Any]) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        config["_warnings"].append(
            f"`{name}` имеет неверное значение. Используется безопасное значение {default}."
        )
        return default
    if parsed <= 0:
        config["_warnings"].append(
            f"`{name}` должно быть больше 0. Используется безопасное значение {default}."
        )
        return default
    return parsed

