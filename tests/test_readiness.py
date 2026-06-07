from pathlib import Path
from unittest.mock import patch

from app.services.readiness import check_readiness
from app.services.recorder import NoopRecorder


class ConnectedRecorder:
    enabled = True
    status_text = "OBS: подключен"

    def check_connection(self) -> str:
        return self.status_text


def _config(**summary_overrides):
    summary = {
        "enabled": False,
        "api_key_env": "AITUNNEL_KEY",
        "base_url": "https://api.aitunnel.ru/v1/",
        "env_file": "",
    }
    summary.update(summary_overrides)
    return {
        "secrets": {"env_file": ""},
        "summary": summary,
        "transcription": {
            "backend": "whisper_cli",
            "model": "base",
            "whisper_command": "whisper",
        },
    }


def _config_with_transcription(transcription):
    config = _config()
    config["transcription"] = transcription
    return config


def _by_component(statuses):
    return {status["component"]: status for status in statuses}


def _details(status):
    return {detail["label"]: detail for detail in status["details"]}


def test_readiness_returns_user_facing_cards_with_structured_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AITUNNEL_KEY", "test-secret-value")
    config = _config(enabled=True)
    config["transcription"] = {
        "backend": "aitunnel",
        "model": "whisper-large-v3-turbo",
        "api_key_env": "AITUNNEL_KEY",
        "base_url": "https://api.aitunnel.ru/v1/",
    }

    with patch("app.services.readiness.shutil.which", return_value="/bin/ffmpeg"):
        statuses = check_readiness(config, ConnectedRecorder(), tmp_path)

    assert [status["component"] for status in statuses] == [
        "Запись разговора (OBS)",
        "Извлечение аудио (FFmpeg)",
        "Транскрипция",
        "Итоги встречи",
    ]
    transcription = _by_component(statuses)["Транскрипция"]
    assert transcription["state"] == "ok"
    assert transcription["details"] == [
        {"label": "Режим", "value": "AI Tunnel STT", "state": "neutral"},
        {"label": "Модель", "value": "Whisper Large V3 Turbo", "state": "neutral"},
        {"label": "Доступ", "value": "API key найден", "state": "neutral"},
        {
            "label": "Данные",
            "value": "Аудио отправляется во внешний сервис",
            "state": "neutral",
        },
    ]
    summary = _by_component(statuses)["Итоги встречи"]
    assert summary["details"] == [
        {"label": "Генерация", "value": "Включена", "state": "neutral"},
        {"label": "Модель", "value": "GPT 5.4 Mini", "state": "neutral"},
        {"label": "Доступ", "value": "API key найден", "state": "neutral"},
        {"label": "Данные", "value": "Отправляется только transcript", "state": "neutral"},
    ]
    assert "test-secret-value" not in str(statuses)


def test_readiness_reports_local_commands_found_and_missing(tmp_path: Path) -> None:
    def fake_which(command):
        return f"/bin/{command}" if command == "ffmpeg" else None

    with patch("app.services.readiness.shutil.which", side_effect=fake_which):
        statuses = _by_component(check_readiness(_config(), NoopRecorder(), tmp_path))

    assert statuses["Извлечение аудио (FFmpeg)"]["state"] == "ok"
    transcription = statuses["Транскрипция"]
    assert transcription["state"] == "error"
    assert transcription["message"] == "Whisper CLI не найден."
    assert _details(transcription)["Проблема"]["value"] == "Команда whisper не найдена"


def test_readiness_reports_faster_whisper_backend_available(tmp_path: Path) -> None:
    with (
        patch("app.services.readiness.shutil.which", return_value="/bin/ffmpeg"),
        patch("app.services.readiness.importlib.util.find_spec", return_value=object()),
    ):
        statuses = _by_component(
            check_readiness(
                _config_with_transcription({"backend": "faster_whisper", "model": "base"}),
                NoopRecorder(),
                tmp_path,
            )
        )

    transcription = statuses["Транскрипция"]
    assert transcription["state"] == "ok"
    assert transcription["message"] == "faster-whisper доступен."
    assert _details(transcription)["Данные"]["value"] == "Аудио остается локально"


def test_readiness_reports_faster_whisper_backend_missing(tmp_path: Path) -> None:
    with (
        patch("app.services.readiness.shutil.which", return_value="/bin/ffmpeg"),
        patch("app.services.readiness.importlib.util.find_spec", return_value=None),
    ):
        statuses = _by_component(
            check_readiness(
                _config_with_transcription({"backend": "faster_whisper", "model": "base"}),
                NoopRecorder(),
                tmp_path,
            )
        )

    transcription = statuses["Транскрипция"]
    assert transcription["state"] == "error"
    assert "faster-whisper не установлен" in transcription["message"]
    assert _details(transcription)["Проблема"]["state"] == "error"


def test_readiness_reports_summary_disabled_without_api_key(tmp_path: Path) -> None:
    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = _by_component(check_readiness(_config(enabled=False), NoopRecorder(), tmp_path))

    summary = statuses["Итоги встречи"]
    assert summary["state"] == "skipped"
    assert _details(summary)["Генерация"]["value"] == "Выключена настройками"
    assert _details(summary)["API key"]["value"] == "Не требуется"


def test_readiness_reports_summary_key_without_revealing_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AITUNNEL_KEY", "test-secret-value")
    config = _config(
        enabled=True,
        api_key_env="AITUNNEL_KEY",
        base_url="https://api.aitunnel.ru/v1/",
    )

    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = check_readiness(config, NoopRecorder(), tmp_path)

    rendered = str(statuses)
    summary = _by_component(statuses)["Итоги встречи"]
    assert _details(summary)["Доступ"]["value"] == "API key найден"
    assert "test-secret-value" not in rendered


def test_readiness_reports_aitunnel_transcription_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITUNNEL_KEY", "test-secret-value")
    config = _config_with_transcription(
        {
            "backend": "aitunnel",
            "model": "whisper-large-v3-turbo",
            "api_key_env": "AITUNNEL_KEY",
            "base_url": "https://api.aitunnel.ru/v1/",
        }
    )

    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = check_readiness(config, NoopRecorder(), tmp_path)

    rendered = str(statuses)
    transcription = _by_component(statuses)["Транскрипция"]
    assert transcription["message"] == "AI Tunnel STT настроен. API key найден."
    assert _details(transcription)["Доступ"]["value"] == "API key найден"
    assert "test-secret-value" not in rendered


def test_readiness_embeds_external_transcription_key_error_in_transcription_card(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AITUNNEL_KEY", raising=False)
    config = _config(enabled=False)
    config["transcription"] = {
        "backend": "aitunnel",
        "model": "whisper-large-v3-turbo",
        "api_key_env": "AITUNNEL_KEY",
        "base_url": "https://api.aitunnel.ru/v1/",
    }

    with patch("app.services.readiness.shutil.which", return_value="/bin/ffmpeg"):
        statuses = check_readiness(config, ConnectedRecorder(), tmp_path)

    transcription = _by_component(statuses)["Транскрипция"]
    assert transcription["state"] == "error"
    assert {
        "label": "Проблема",
        "value": "API key не найден: AITUNNEL_KEY",
        "state": "error",
    } in transcription["details"]


def test_readiness_keeps_summary_and_transcription_key_errors_separate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PROXYAPI_KEY", "summary-secret-value")
    monkeypatch.delenv("AITUNNEL_KEY", raising=False)
    config = _config(
        enabled=True,
        api_key_env="PROXYAPI_KEY",
        base_url="https://api.proxyapi.ru/openai/v1",
    )
    config["transcription"] = {
        "backend": "aitunnel",
        "model": "whisper-large-v3-turbo",
        "api_key_env": "AITUNNEL_KEY",
        "base_url": "https://api.aitunnel.ru/v1/",
    }

    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = check_readiness(config, NoopRecorder(), tmp_path)

    rendered = str(statuses)
    mapped = _by_component(statuses)
    assert mapped["Транскрипция"]["state"] == "error"
    assert mapped["Итоги встречи"]["state"] == "ok"
    assert _details(mapped["Транскрипция"])["Проблема"]["value"] == (
        "API key не найден: AITUNNEL_KEY"
    )
    assert "summary-secret-value" not in rendered


def test_readiness_uses_shared_secrets_env_file_for_external_services(
    tmp_path: Path,
    monkeypatch,
) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("AITUNNEL_KEY=shared-secret-value\n", encoding="utf-8")
    monkeypatch.delenv("AITUNNEL_KEY", raising=False)
    config = _config(enabled=True)
    config["secrets"] = {"env_file": str(env_file)}
    config["transcription"] = {
        "backend": "aitunnel",
        "model": "whisper-large-v3-turbo",
        "api_key_env": "AITUNNEL_KEY",
        "base_url": "https://api.aitunnel.ru/v1/",
    }

    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = check_readiness(config, NoopRecorder(), tmp_path)

    rendered = str(statuses)
    mapped = _by_component(statuses)
    assert mapped["Транскрипция"]["state"] == "ok"
    assert mapped["Итоги встречи"]["state"] == "ok"
    assert "shared-secret-value" not in rendered
