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
        "api_key_env": "AITUNNEL_KEY",
        "base_url": "https://api.aitunnel.ru/v1/",
        "env_file": "",
        "timeout_seconds": 120,
        "max_chars_per_chunk": 20000,
    },
    "transcription": {
        "backend": "whisper_cli",
        "model": "base",
        "language": "ru",
        "device": "cpu",
        "compute_type": "int8",
        "whisper_command": "whisper",
        "vad_filter": True,
    },
    "ui": {
        "theme": "light",
        "floating_theme": "inherit",
    },
}


def _default_config() -> dict[str, Any]:
    return {
        **DEFAULT_CONFIG,
        "storage": dict(DEFAULT_CONFIG["storage"]),
        "obs": dict(DEFAULT_CONFIG["obs"]),
        "summary": dict(DEFAULT_CONFIG["summary"]),
        "transcription": dict(DEFAULT_CONFIG["transcription"]),
        "ui": dict(DEFAULT_CONFIG["ui"]),
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
    transcription = _section(loaded, "transcription", config)
    ui = _section(loaded, "ui", config)
    config.update(loaded)
    config["storage"] = {**DEFAULT_CONFIG["storage"], **storage}
    config["obs"] = _normalize_obs({**DEFAULT_CONFIG["obs"], **obs}, config)
    config["summary"] = _normalize_summary({**DEFAULT_CONFIG["summary"], **summary}, config)
    config["transcription"] = _normalize_transcription(
        {**DEFAULT_CONFIG["transcription"], **transcription},
        config,
    )
    config["ui"] = _normalize_ui({**DEFAULT_CONFIG["ui"], **ui})
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
    default_api_key_env = str(DEFAULT_CONFIG["summary"]["api_key_env"])
    summary["api_key_env"] = (
        str(summary.get("api_key_env") or default_api_key_env).strip() or default_api_key_env
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


def _normalize_transcription(
    transcription: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    del config
    backend = str(transcription.get("backend") or "whisper_cli").strip().lower()
    if backend not in {"whisper_cli", "faster_whisper"}:
        backend = "whisper_cli"
    transcription["backend"] = backend
    transcription["model"] = str(
        transcription.get("model") or DEFAULT_CONFIG["transcription"]["model"]
    ).strip()
    transcription["language"] = str(
        transcription.get("language") or DEFAULT_CONFIG["transcription"]["language"]
    ).strip()
    transcription["device"] = str(
        transcription.get("device") or DEFAULT_CONFIG["transcription"]["device"]
    ).strip()
    transcription["compute_type"] = str(
        transcription.get("compute_type") or DEFAULT_CONFIG["transcription"]["compute_type"]
    ).strip()
    transcription["whisper_command"] = str(
        transcription.get("whisper_command")
        or DEFAULT_CONFIG["transcription"]["whisper_command"]
    ).strip()
    transcription["vad_filter"] = _safe_bool(
        transcription.get("vad_filter"),
        bool(DEFAULT_CONFIG["transcription"]["vad_filter"]),
    )
    return transcription


def _normalize_ui(ui: dict[str, Any]) -> dict[str, Any]:
    theme = str(ui.get("theme") or DEFAULT_CONFIG["ui"]["theme"]).strip().lower()
    if theme not in {"light", "dark"}:
        theme = "light"
    floating_theme = str(
        ui.get("floating_theme") or DEFAULT_CONFIG["ui"]["floating_theme"]
    ).strip().lower()
    if floating_theme not in {"inherit", "light", "dark"}:
        floating_theme = "inherit"
    ui["theme"] = theme
    ui["floating_theme"] = floating_theme
    return ui


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

