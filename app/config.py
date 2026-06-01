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
        "recording_output_dir": "",
    },
}


def load_config(path: Path = Path("config.yaml")) -> dict[str, Any]:
    if not path.exists():
        return DEFAULT_CONFIG.copy()

    with path.open(encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file) or {}

    storage = {**DEFAULT_CONFIG["storage"], **loaded.get("storage", {})}
    obs = {**DEFAULT_CONFIG["obs"], **loaded.get("obs", {})}
    return {**DEFAULT_CONFIG, **loaded, "storage": storage, "obs": obs}

