import shutil
from pathlib import Path
from typing import Any

from app.services.recorder import Recorder, RecorderError
from app.services.summarization import load_api_key


def check_readiness(config: dict[str, Any], recorder: Recorder, data_root: Path) -> list[dict[str, str]]:
    summary_config = config.get("summary", {})
    summary_enabled = bool(summary_config.get("enabled", False))
    return [
        _obs_status(recorder),
        _command_status("FFmpeg", "ffmpeg", "FFmpeg найден.", "FFmpeg не найден."),
        _command_status("Whisper", "whisper", "Whisper найден.", "Whisper не найден."),
        _summary_status(summary_enabled),
        _api_key_status(summary_config, summary_enabled),
        _endpoint_status(summary_config, summary_enabled),
        _data_folder_status(data_root),
    ]


def _obs_status(recorder: Recorder) -> dict[str, str]:
    if not recorder.enabled:
        return _status("OBS", "skipped", "OBS выключен в config.")
    try:
        recorder.check_connection()
    except RecorderError:
        return _status(
            "OBS",
            "error",
            "OBS недоступен. Проверьте, что OBS запущен и WebSocket включен.",
        )
    return _status("OBS", "ok", "OBS подключен.")


def _command_status(label: str, command: str, ok_message: str, error_message: str) -> dict[str, str]:
    if shutil.which(command):
        return _status(label, "ok", ok_message)
    return _status(label, "error", error_message)


def _summary_status(enabled: bool) -> dict[str, str]:
    if enabled:
        return _status("Summary", "ok", "Генерация итогов включена.")
    return _status("Summary", "skipped", "Генерация итогов выключена.")


def _api_key_status(summary_config: dict[str, Any], summary_enabled: bool) -> dict[str, str]:
    if not summary_enabled:
        return _status("API key", "skipped", "API key не требуется: summary выключен.")
    api_key_env = str(summary_config.get("api_key_env") or "OPENAI_API_KEY")
    env_file = summary_config.get("env_file") or ""
    if load_api_key(api_key_env, env_file):
        return _status("API key", "ok", "API key найден.")
    return _status(
        "API key",
        "error",
        "API key не найден. Проверьте переменную окружения или .env.local.",
    )


def _endpoint_status(summary_config: dict[str, Any], summary_enabled: bool) -> dict[str, str]:
    if not summary_enabled:
        return _status("Summary endpoint", "skipped", "Endpoint не требуется: summary выключен.")
    base_url = str(summary_config.get("base_url") or "").strip()
    if not base_url:
        return _status("Summary endpoint", "ok", "Используется прямой OpenAI endpoint.")
    if "proxyapi" in base_url.lower():
        return _status("Summary endpoint", "ok", "Используется ProxyAPI / custom endpoint.")
    return _status("Summary endpoint", "ok", "Используется custom endpoint.")


def _data_folder_status(data_root: Path) -> dict[str, str]:
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        probe = data_root / ".write_check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return _status("Папка данных", "error", "Папка данных недоступна для записи.")
    return _status("Папка данных", "ok", "Папка данных доступна.")


def _status(component: str, state: str, message: str) -> dict[str, str]:
    return {"component": component, "state": state, "message": message}
