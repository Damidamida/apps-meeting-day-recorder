import importlib.util
import shutil
from pathlib import Path
from typing import Any

from app.runtime import bundled_tool_path
from app.services.recorder import Recorder, RecorderError
from app.services.summarization import load_api_key


READINESS_CARDS: tuple[dict[str, Any], ...] = (
    {
        "component": "Запись разговора (OBS)",
        "initial_details": ("Состояние", "Проверено", "Итог"),
    },
    {
        "component": "Извлечение аудио (FFmpeg)",
        "initial_details": ("Состояние", "Что делает", "Итог"),
    },
    {
        "component": "Транскрипция",
        "initial_details": ("Режим", "Модель", "Доступ", "Данные"),
    },
    {
        "component": "Итоги встречи",
        "initial_details": ("Генерация", "Модель", "Доступ", "Данные"),
    },
)


def check_readiness(config: dict[str, Any], recorder: Recorder, data_root: Path) -> list[dict[str, Any]]:
    del data_root
    secrets_config = config.get("secrets", {})
    summary_config = config.get("summary", {})
    transcription_config = config.get("transcription", {})
    summary_enabled = bool(summary_config.get("enabled", True))
    return [
        _obs_status(recorder),
        _ffmpeg_status(),
        _transcription_status(transcription_config, secrets_config),
        _summary_status(summary_config, summary_enabled, secrets_config),
    ]


def _obs_status(recorder: Recorder) -> dict[str, Any]:
    if not recorder.enabled:
        return _status(
            "Запись разговора (OBS)",
            "skipped",
            "Запись разговора не используется в тестовом режиме.",
            [
                _detail("Состояние", "Тестовый режим без записи"),
                _detail("Проверено", "OBS не используется"),
                _detail("Итог", "Встреча сохранится без записи"),
            ],
        )
    try:
        recorder.check_connection()
    except RecorderError:
        return _status(
            "Запись разговора (OBS)",
            "error",
            "OBS недоступен. Запустите OBS и проверьте WebSocket.",
            [
                _detail("Состояние", "OBS недоступен", "error"),
                _detail("Проблема", "WebSocket не отвечает"),
                _detail("Что сделать", "Запустите OBS и проверьте WebSocket", "error"),
            ],
        )
    return _status(
        "Запись разговора (OBS)",
        "ok",
        "OBS подключен. Запись разговора доступна.",
        [
            _detail("Состояние", "OBS подключен"),
            _detail("Проверено", "WebSocket отвечает"),
            _detail("Итог", "Встречу можно записывать"),
        ],
    )


def _ffmpeg_status() -> dict[str, Any]:
    bundled_ffmpeg = bundled_tool_path("ffmpeg.exe")
    if bundled_ffmpeg.is_file():
        return _status(
            "Извлечение аудио (FFmpeg)",
            "ok",
            "FFmpeg найден в сборке BK Scribe.",
            [
                _detail("Состояние", "Bundled FFmpeg"),
                _detail("Что делает", "Извлекает audio.wav"),
                _detail("Итог", "Можно запускать транскрипцию"),
            ],
        )
    if shutil.which("ffmpeg"):
        return _status(
            "Извлечение аудио (FFmpeg)",
            "ok",
            "FFmpeg найден. audio.wav можно извлечь из записи.",
            [
                _detail("Состояние", "FFmpeg найден"),
                _detail("Что делает", "Извлекает audio.wav"),
                _detail("Итог", "Можно запускать транскрипцию"),
            ],
        )
    return _status(
        "Извлечение аудио (FFmpeg)",
        "error",
        "FFmpeg не найден. После записи не получится извлечь audio.wav.",
        [
            _detail("Состояние", "FFmpeg не найден", "error"),
            _detail("Проблема", "Команда ffmpeg недоступна"),
            _detail("Что сделать", "Установите FFmpeg и добавьте его в PATH", "error"),
        ],
    )


def _transcription_status(
    config: dict[str, Any],
    secrets_config: dict[str, Any],
) -> dict[str, Any]:
    backend = str(config.get("backend") or "whisper_cli")
    model = _transcription_model_label(str(config.get("model") or "base"))
    if backend == "aitunnel":
        api_key_env = str(config.get("api_key_env") or "AITUNNEL_KEY")
        env_file = config.get("env_file") or secrets_config.get("env_file") or ""
        if not load_api_key(api_key_env, env_file):
            return _status(
                "Транскрипция",
                "error",
                f"AI Tunnel STT выбран, но API key не найден: {api_key_env}.",
                [
                    _detail("Режим", "AI Tunnel STT"),
                    _detail("Модель", model),
                    _detail("Проблема", f"API key не найден: {api_key_env}", "error"),
                    _detail("Что сделать", "Проверьте .env файл или переменную окружения", "error"),
                ],
            )
        return _status(
            "Транскрипция",
            "ok",
            "AI Tunnel STT настроен. API key найден.",
            [
                _detail("Режим", "AI Tunnel STT"),
                _detail("Модель", model),
                _detail("Доступ", "API key найден"),
                _detail("Данные", "Аудио отправляется во внешний сервис"),
            ],
        )
    if backend == "faster_whisper":
        if importlib.util.find_spec("faster_whisper") is None:
            return _status(
                "Транскрипция",
                "error",
                "faster-whisper не установлен.",
                [
                    _detail("Режим", "faster-whisper"),
                    _detail("Модель", model),
                    _detail("Проблема", "Пакет faster-whisper не установлен", "error"),
                    _detail("Что сделать", "Установите optional-зависимость или выберите whisper_cli", "error"),
                ],
            )
        return _status(
            "Транскрипция",
            "ok",
            "faster-whisper доступен.",
            [
                _detail("Режим", "faster-whisper"),
                _detail("Модель", model),
                _detail("Доступ", "Пакет установлен"),
                _detail("Данные", "Аудио остается локально"),
            ],
        )
    command = str(config.get("whisper_command") or "whisper")
    if shutil.which(command):
        return _status(
            "Транскрипция",
            "ok",
            "Whisper CLI найден.",
            [
                _detail("Режим", "Whisper CLI"),
                _detail("Модель", model),
                _detail("Доступ", f"Команда {command} найдена"),
                _detail("Данные", "Аудио остается локально"),
            ],
        )
    return _status(
        "Транскрипция",
        "error",
        "Whisper CLI не найден.",
        [
            _detail("Режим", "Whisper CLI"),
            _detail("Модель", model),
            _detail("Проблема", f"Команда {command} не найдена", "error"),
            _detail("Что сделать", "Установите Whisper CLI или выберите другой backend", "error"),
        ],
    )


def _summary_status(
    config: dict[str, Any],
    enabled: bool,
    secrets_config: dict[str, Any],
) -> dict[str, Any]:
    model = _summary_model_label(str(config.get("model") or "gpt-5.4-mini"))
    if not enabled:
        return _status(
            "Итоги встречи",
            "skipped",
            "Итоги встречи выключены в настройках.",
            [
                _detail("Генерация", "Выключена настройками"),
                _detail("Модель", model),
                _detail("API key", "Не требуется"),
                _detail("Итог", "summary.md не будет создан автоматически"),
            ],
        )
    api_key_env = str(config.get("api_key_env") or "AITUNNEL_KEY")
    env_file = config.get("env_file") or secrets_config.get("env_file") or ""
    if not load_api_key(api_key_env, env_file):
        return _status(
            "Итоги встречи",
            "error",
            f"Итоги встречи включены, но API key не найден: {api_key_env}.",
            [
                _detail("Генерация", "Включена"),
                _detail("Модель", model),
                _detail("Проблема", f"API key не найден: {api_key_env}", "error"),
                _detail("Что сделать", "Проверьте .env файл или переменную окружения", "error"),
            ],
        )
    return _status(
        "Итоги встречи",
        "ok",
        "Генерация итогов включена. API key найден.",
        [
            _detail("Генерация", "Включена"),
            _detail("Модель", model),
            _detail("Доступ", "API key найден"),
            _detail("Данные", "Отправляется только transcript"),
        ],
    )


def _transcription_model_label(model: str) -> str:
    return {
        "whisper-large-v3-turbo": "Whisper Large V3 Turbo",
        "whisper-large-v3": "Whisper Large V3",
        "whisper-1": "Whisper 1",
    }.get(model, model)


def _summary_model_label(model: str) -> str:
    return {
        "gpt-5.4-mini": "GPT 5.4 Mini",
        "gpt-5.4-nano": "GPT 5.4 Nano",
    }.get(model, model)


def _detail(label: str, value: str, state: str = "neutral") -> dict[str, str]:
    return {"label": label, "value": value, "state": state}


def _status(
    component: str,
    state: str,
    message: str,
    details: list[dict[str, str]],
) -> dict[str, Any]:
    return {"component": component, "state": state, "message": message, "details": details}
