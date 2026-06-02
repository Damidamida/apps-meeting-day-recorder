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


def load_config(path: Path = Path("config.yaml")) -> dict[str, Any]:
    if not path.exists():
        return DEFAULT_CONFIG.copy()

    with path.open(encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file) or {}

    storage = {**DEFAULT_CONFIG["storage"], **loaded.get("storage", {})}
    obs = {**DEFAULT_CONFIG["obs"], **loaded.get("obs", {})}
    summary = {**DEFAULT_CONFIG["summary"], **loaded.get("summary", {})}
    return {**DEFAULT_CONFIG, **loaded, "storage": storage, "obs": obs, "summary": summary}

