import importlib.util
import shutil
from pathlib import Path
from typing import Any

from app.services.recorder import Recorder, RecorderError
from app.services.summarization import load_api_key


def check_readiness(config: dict[str, Any], recorder: Recorder, data_root: Path) -> list[dict[str, str]]:
    summary_config = config.get("summary", {})
    transcription_config = config.get("transcription", {})
    summary_enabled = bool(summary_config.get("enabled", False))
    external_transcription_enabled = str(
        transcription_config.get("backend") or ""
    ) == "aitunnel"
    return [
        _obs_status(recorder),
        _command_status("FFmpeg", "ffmpeg", "FFmpeg найден.", "FFmpeg не найден."),
        _transcription_status(transcription_config),
        _summary_status(summary_enabled),
        _api_key_status(
            summary_config,
            summary_enabled,
            transcription_config,
            external_transcription_enabled,
        ),
        _endpoint_status(
            summary_config,
            summary_enabled,
            transcription_config,
            external_transcription_enabled,
        ),
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


def _transcription_status(config: dict[str, Any]) -> dict[str, str]:
    backend = str(config.get("backend") or "whisper_cli")
    if backend == "aitunnel":
        base_url = str(config.get("base_url") or "").strip()
        if "aitunnel" in base_url.lower():
            return _status("Whisper", "ok", "AI Tunnel STT настроен.")
        return _status("Whisper", "ok", "Внешний STT endpoint настроен.")
    if backend == "faster_whisper":
        if importlib.util.find_spec("faster_whisper") is not None:
            return _status("Whisper", "ok", "faster-whisper доступен.")
        return _status(
            "Whisper",
            "error",
            "faster-whisper не установлен. Установите optional-зависимость или выберите whisper_cli.",
        )
    command = str(config.get("whisper_command") or "whisper")
    return _command_status("Whisper", command, "Whisper CLI найден.", "Whisper CLI не найден.")


def _summary_status(enabled: bool) -> dict[str, str]:
    if enabled:
        return _status("Summary", "ok", "Генерация итогов включена.")
    return _status("Summary", "skipped", "Генерация итогов выключена.")


def _api_key_status(
    summary_config: dict[str, Any],
    summary_enabled: bool,
    transcription_config: dict[str, Any],
    external_transcription_enabled: bool,
) -> dict[str, str]:
    required_keys: list[tuple[str, str | Path]] = []
    if summary_enabled:
        required_keys.append(
            (
                str(summary_config.get("api_key_env") or "AITUNNEL_KEY"),
                summary_config.get("env_file") or "",
            )
        )
    if external_transcription_enabled:
        required_keys.append(
            (
                str(transcription_config.get("api_key_env") or "AITUNNEL_KEY"),
                transcription_config.get("env_file") or "",
            )
        )
    if not required_keys:
        return _status(
            "API key",
            "skipped",
            "API key не требуется: summary и внешняя транскрипция выключены.",
        )

    missing = [
        api_key_env
        for api_key_env, env_file in required_keys
        if not load_api_key(api_key_env, env_file)
    ]
    if not missing:
        return _status("API key", "ok", "API key найден.")
    missing_names = ", ".join(dict.fromkeys(missing))
    return _status(
        "API key",
        "error",
        f"API key не найден: {missing_names}. Проверьте переменную окружения или .env.local.",
    )


def _endpoint_status(
    summary_config: dict[str, Any],
    summary_enabled: bool,
    transcription_config: dict[str, Any],
    external_transcription_enabled: bool,
) -> dict[str, str]:
    base_urls = []
    if summary_enabled:
        base_urls.append(str(summary_config.get("base_url") or "").strip())
    if external_transcription_enabled:
        base_urls.append(str(transcription_config.get("base_url") or "").strip())
    if not base_urls:
        return _status(
            "Summary endpoint",
            "skipped",
            "Endpoint не требуется: summary и внешняя транскрипция выключены.",
        )
    normalized = {base_url for base_url in base_urls if base_url}
    if len(normalized) > 1:
        return _status("Summary endpoint", "ok", "Используются разные AI endpoints.")
    base_url = next(iter(normalized), "")
    if not base_url:
        return _status("Summary endpoint", "ok", "Используется прямой OpenAI endpoint.")
    if "aitunnel" in base_url.lower():
        return _status("Summary endpoint", "ok", "Используется AI Tunnel endpoint.")
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
