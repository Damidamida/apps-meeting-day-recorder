from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from app.services.first_run import default_setup_config, normalize_setup_config_dict


TRANSCRIPTION_BACKENDS = {"whisper_cli", "faster_whisper", "aitunnel"}
LOCAL_WHISPER_MODELS = {"tiny", "base", "small", "medium", "large", "turbo"}
FASTER_WHISPER_MODELS = {"tiny", "base", "small", "medium", "large-v3", "turbo"}
AITUNNEL_TRANSCRIPTION_MODELS = {
    "whisper-large-v3-turbo",
    "whisper-large-v3",
    "whisper-1",
}
DEFAULT_TRANSCRIPTION_BACKENDS: dict[str, dict[str, Any]] = {
    "whisper_cli": {
        "model": "base",
        "language": "ru",
        "whisper_command": "whisper",
    },
    "faster_whisper": {
        "model": "base",
        "language": "ru",
        "device": "cpu",
        "compute_type": "int8",
        "vad_filter": True,
    },
    "aitunnel": {
        "model": "whisper-large-v3-turbo",
        "language": "ru",
        "api_key_env": "AITUNNEL_KEY",
        "base_url": "https://api.aitunnel.ru/v1/",
        "env_file": "",
        "timeout_seconds": 300,
        "max_upload_mb": 25,
        "chunking_enabled": True,
        "chunk_duration_seconds": 300,
        "retry_attempts": 2,
        "retry_sleep_seconds": 1,
    },
}
DEFAULT_SUMMARY_TEMPLATES: dict[str, dict[str, Any]] = {
    "meeting": {
        "title": "Итоги встречи",
        "sections": [
            {
                "title": "Кратко",
                "instruction": "Сформулируй 2-4 главных вывода встречи без лишних деталей.",
            },
            {
                "title": "Обсуждалось",
                "instruction": "Перечисли основные темы обсуждения без дословного пересказа transcript.",
            },
            {
                "title": "Решения",
                "instruction": "Перечисли зафиксированные решения. Если решений нет, напиши \"Не зафиксировано\".",
            },
            {
                "title": "Задачи",
                "instruction": "Сформулируй action items, исполнителей и сроки. Если исполнитель или срок не указаны, явно отметь это.",
            },
            {
                "title": "Риски / вопросы",
                "instruction": "Перечисли открытые вопросы, риски и блокеры.",
            },
            {
                "title": "Требует проверки",
                "instruction": "Отметь неясные места и спорные выводы, которые нужно проверить вручную.",
            },
        ],
        "rules": (
            "- если данных недостаточно, пиши \"Не зафиксировано\";\n"
            "- задачи формулируй как action items;\n"
            "- сохраняй смысл технических терминов;\n"
            "- итог должен быть пригоден для ручного ревью."
        ),
    },
    "day": {
        "title": "Итоги встреч",
        "sections": [
            {
                "title": "Главное за день",
                "instruction": "Сделай короткую выжимку самого важного за рабочий день.",
            },
            {
                "title": "По встречам",
                "instruction": "Кратко перечисли, что было важно по каждой встрече.",
            },
            {
                "title": "Решения",
                "instruction": "Собери решения из всех встреч за день.",
            },
            {
                "title": "Задачи и договоренности",
                "instruction": "Собери задачи, договоренности, исполнителей и сроки из всех встреч.",
            },
            {
                "title": "Риски / вопросы",
                "instruction": "Собери открытые вопросы, риски и блокеры за день.",
            },
            {
                "title": "Что требует проверки",
                "instruction": "Отметь спорные или неполные данные, которые нужно проверить вручную.",
            },
        ],
        "rules": (
            "- используй только переданные итоги встреч;\n"
            "- если у встречи отсутствует summary, явно укажи это;\n"
            "- если в текущем черновике уже есть ручные правки, сохрани их смысл;\n"
            "- итог должен быть короткой выжимкой, а не копией всех summary подряд."
        ),
    },
}
DEFAULT_CONFIG: dict[str, Any] = {
    "storage": {
        "root": "MeetingSummaries",
    },
    "obs": {
        "websocket_host": "localhost",
        "websocket_port": 4455,
        "websocket_password": "",
    },
    "secrets": {
        "env_file": "",
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
        "retry_attempts": 2,
        "retry_sleep_seconds": 1,
        "templates": DEFAULT_SUMMARY_TEMPLATES,
    },
    "transcription": {
        "backend": "whisper_cli",
        "model": "base",
        "language": "ru",
        "device": "cpu",
        "compute_type": "int8",
        "whisper_command": "whisper",
        "vad_filter": True,
        "api_key_env": "AITUNNEL_KEY",
        "base_url": "https://api.aitunnel.ru/v1/",
        "env_file": "",
        "timeout_seconds": 300,
        "max_upload_mb": 25,
        "chunking_enabled": True,
        "chunk_duration_seconds": 300,
        "retry_attempts": 2,
        "retry_sleep_seconds": 1,
        "backends": DEFAULT_TRANSCRIPTION_BACKENDS,
    },
    "ui": {
        "theme": "light",
        "floating_theme": "inherit",
    },
    "setup": default_setup_config(),
}


def _default_config() -> dict[str, Any]:
    return {
        **DEFAULT_CONFIG,
        "storage": dict(DEFAULT_CONFIG["storage"]),
        "obs": dict(DEFAULT_CONFIG["obs"]),
        "secrets": dict(DEFAULT_CONFIG["secrets"]),
        "summary": deepcopy(DEFAULT_CONFIG["summary"]),
        "transcription": {
            **DEFAULT_CONFIG["transcription"],
            "backends": _default_transcription_backends(),
        },
        "ui": dict(DEFAULT_CONFIG["ui"]),
        "setup": deepcopy(DEFAULT_CONFIG["setup"]),
        "_warnings": [],
    }


def reset_processing_config(config: dict[str, Any]) -> dict[str, Any]:
    reset = deepcopy(config)
    defaults = _default_config()
    reset.pop("_warnings", None)
    reset["transcription"] = deepcopy(defaults["transcription"])
    reset["summary"] = deepcopy(defaults["summary"])
    return reset


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
    secrets = _section(loaded, "secrets", config)
    summary = _section(loaded, "summary", config)
    transcription = _section(loaded, "transcription", config)
    ui = _section(loaded, "ui", config)
    setup = _section(loaded, "setup", config)
    config.update(loaded)
    config["storage"] = {**DEFAULT_CONFIG["storage"], **storage}
    config["obs"] = _normalize_obs({**DEFAULT_CONFIG["obs"], **obs}, config)
    config["secrets"] = _normalize_secrets({**DEFAULT_CONFIG["secrets"], **secrets})
    config["summary"] = _normalize_summary({**DEFAULT_CONFIG["summary"], **summary}, config)
    config["transcription"] = _normalize_transcription(transcription, config)
    config["ui"] = _normalize_ui({**DEFAULT_CONFIG["ui"], **ui})
    config["setup"] = normalize_setup_config_dict({**DEFAULT_CONFIG["setup"], **setup})
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
    obs.pop("enabled", None)
    obs["websocket_host"] = str(obs.get("websocket_host") or "localhost").strip() or "localhost"
    obs["websocket_port"] = _safe_int(
        obs.get("websocket_port"),
        int(DEFAULT_CONFIG["obs"]["websocket_port"]),
        "obs.websocket_port",
        config,
    )
    obs["websocket_password"] = str(obs.get("websocket_password") or "")
    return obs


def _normalize_secrets(secrets: dict[str, Any]) -> dict[str, Any]:
    secrets["env_file"] = str(secrets.get("env_file") or "").strip()
    return secrets


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
    summary["retry_attempts"] = _safe_non_negative_int(
        summary.get("retry_attempts"),
        int(DEFAULT_CONFIG["summary"]["retry_attempts"]),
        "summary.retry_attempts",
        config,
    )
    summary["retry_sleep_seconds"] = _safe_int(
        summary.get("retry_sleep_seconds"),
        int(DEFAULT_CONFIG["summary"]["retry_sleep_seconds"]),
        "summary.retry_sleep_seconds",
        config,
    )
    summary["templates"] = _normalize_summary_templates(summary.get("templates"), config)
    return summary


def _normalize_summary_templates(value: Any, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return deepcopy(DEFAULT_SUMMARY_TEMPLATES)
    return {
        "meeting": _normalize_summary_template(
            value.get("meeting"),
            DEFAULT_SUMMARY_TEMPLATES["meeting"],
            "summary.templates.meeting",
            config,
        ),
        "day": _normalize_summary_template(
            value.get("day"),
            DEFAULT_SUMMARY_TEMPLATES["day"],
            "summary.templates.day",
            config,
        ),
    }


def _normalize_summary_template(
    value: Any,
    default: dict[str, Any],
    name: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return deepcopy(default)
    title = str(value.get("title") or default["title"]).strip() or str(default["title"])
    rules = str(value.get("rules") or "").strip()
    raw_sections = value.get("sections")
    sections: list[dict[str, str]] = []
    if isinstance(raw_sections, list):
        for item in raw_sections:
            if not isinstance(item, dict):
                continue
            section_title = str(item.get("title") or "").strip()
            if not section_title:
                continue
            sections.append(
                {
                    "title": section_title,
                    "instruction": str(item.get("instruction") or "").strip(),
                }
            )
    if not sections:
        config["_warnings"].append(
            f"`{name}.sections` не содержит валидных разделов. Используется шаблон по умолчанию."
        )
        sections = deepcopy(default["sections"])
    if not rules:
        rules = str(default.get("rules") or "")
    return {"title": title, "sections": sections, "rules": rules}


def _default_transcription_backends() -> dict[str, dict[str, Any]]:
    return {
        backend: dict(profile)
        for backend, profile in DEFAULT_TRANSCRIPTION_BACKENDS.items()
    }


def _normalize_transcription(
    transcription: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    backend = str(transcription.get("backend") or "whisper_cli").strip().lower()
    if backend not in TRANSCRIPTION_BACKENDS:
        backend = "whisper_cli"
    raw_backends = transcription.get("backends")
    if not isinstance(raw_backends, dict):
        raw_backends = {}

    backends = _default_transcription_backends()
    for backend_name in TRANSCRIPTION_BACKENDS:
        raw_profile = raw_backends.get(backend_name, {})
        if raw_profile is None:
            raw_profile = {}
        if not isinstance(raw_profile, dict):
            config["_warnings"].append(
                f"`transcription.backends.{backend_name}` имеет неверный тип. "
                "Используются безопасные значения."
            )
            raw_profile = {}
        backends[backend_name] = _normalize_transcription_profile(
            backend_name,
            raw_profile,
            config,
        )

    legacy_profile = {
        key: transcription[key]
        for key in _transcription_profile_keys(backend)
        if key in transcription
    }
    if legacy_profile:
        backends[backend] = _normalize_transcription_profile(
            backend,
            {**backends[backend], **legacy_profile},
            config,
        )

    active_profile = backends[backend]
    return {
        **DEFAULT_CONFIG["transcription"],
        **active_profile,
        "backend": backend,
        "backends": backends,
    }


def _transcription_profile_keys(backend: str) -> set[str]:
    if backend == "faster_whisper":
        return {"model", "language", "device", "compute_type", "vad_filter"}
    if backend == "aitunnel":
        return {
            "model",
            "language",
            "api_key_env",
            "base_url",
            "env_file",
            "timeout_seconds",
            "max_upload_mb",
            "chunking_enabled",
            "chunk_duration_seconds",
            "retry_attempts",
            "retry_sleep_seconds",
        }
    return {"model", "language", "whisper_command"}


def _normalize_transcription_profile(
    backend: str,
    profile: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    if backend == "faster_whisper":
        device = _allowed_value(profile.get("device"), {"cpu", "cuda"}, "cpu")
        default_compute_type = "float16" if device == "cuda" else "int8"
        return {
            "model": _allowed_value(profile.get("model"), FASTER_WHISPER_MODELS, "base"),
            "language": "ru",
            "device": device,
            "compute_type": str(profile.get("compute_type") or default_compute_type).strip()
            or default_compute_type,
            "vad_filter": _safe_bool(profile.get("vad_filter"), True),
        }
    if backend == "aitunnel":
        default_api_key_env = str(DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["api_key_env"])
        return {
            "model": _allowed_value(
                profile.get("model"),
                AITUNNEL_TRANSCRIPTION_MODELS,
                "whisper-large-v3-turbo",
            ),
            "language": "ru",
            "api_key_env": (
                str(profile.get("api_key_env") or default_api_key_env).strip()
                or default_api_key_env
            ),
            "base_url": str(
                profile.get("base_url")
                or DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["base_url"]
            ).strip(),
            "env_file": str(profile.get("env_file") or "").strip(),
            "timeout_seconds": _safe_int(
                profile.get(
                    "timeout_seconds",
                    DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["timeout_seconds"],
                ),
                int(DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["timeout_seconds"]),
                "transcription.backends.aitunnel.timeout_seconds",
                config,
            ),
            "max_upload_mb": _safe_int(
                profile.get(
                    "max_upload_mb",
                    DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["max_upload_mb"],
                ),
                int(DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["max_upload_mb"]),
                "transcription.backends.aitunnel.max_upload_mb",
                config,
            ),
            "chunking_enabled": _safe_bool(
                profile.get(
                    "chunking_enabled",
                    DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["chunking_enabled"],
                ),
                bool(DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["chunking_enabled"]),
            ),
            "chunk_duration_seconds": _safe_int(
                profile.get(
                    "chunk_duration_seconds",
                    DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["chunk_duration_seconds"],
                ),
                int(DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["chunk_duration_seconds"]),
                "transcription.backends.aitunnel.chunk_duration_seconds",
                config,
            ),
            "retry_attempts": _safe_non_negative_int(
                profile.get(
                    "retry_attempts",
                    DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["retry_attempts"],
                ),
                int(DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["retry_attempts"]),
                "transcription.backends.aitunnel.retry_attempts",
                config,
            ),
            "retry_sleep_seconds": _safe_int(
                profile.get(
                    "retry_sleep_seconds",
                    DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["retry_sleep_seconds"],
                ),
                int(DEFAULT_TRANSCRIPTION_BACKENDS["aitunnel"]["retry_sleep_seconds"]),
                "transcription.backends.aitunnel.retry_sleep_seconds",
                config,
            ),
        }
    return {
        "model": _allowed_value(profile.get("model"), LOCAL_WHISPER_MODELS, "base"),
        "language": "ru",
        "whisper_command": str(profile.get("whisper_command") or "whisper").strip()
        or "whisper",
    }


def _allowed_value(value: Any, allowed: set[str], default: str) -> str:
    normalized = str(value or default).strip()
    return normalized if normalized in allowed else default



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


def _safe_non_negative_int(
    value: Any,
    default: int,
    name: str,
    config: dict[str, Any],
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        config["_warnings"].append(
            f"`{name}` имеет неверное значение. Используется безопасное значение {default}."
        )
        return default
    if parsed < 0:
        config["_warnings"].append(
            f"`{name}` не может быть меньше 0. Используется безопасное значение {default}."
        )
        return default
    return parsed

