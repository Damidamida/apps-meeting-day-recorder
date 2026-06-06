from pathlib import Path
from unittest.mock import patch

from app.services.readiness import check_readiness
from app.services.recorder import NoopRecorder


def _config(**summary_overrides):
    summary = {
        "enabled": False,
        "api_key_env": "AITUNNEL_KEY",
        "base_url": "https://api.aitunnel.ru/v1/",
        "env_file": "",
    }
    summary.update(summary_overrides)
    return {
        "summary": summary,
        "transcription": {
            "backend": "whisper_cli",
            "whisper_command": "whisper",
        },
    }


def _config_with_transcription(transcription):
    config = _config()
    config["transcription"] = transcription
    return config


def _by_component(statuses):
    return {status["component"]: status for status in statuses}


def test_readiness_reports_local_commands_found_and_missing(tmp_path: Path) -> None:
    def fake_which(command):
        return f"/bin/{command}" if command == "ffmpeg" else None

    with patch("app.services.readiness.shutil.which", side_effect=fake_which):
        statuses = _by_component(check_readiness(_config(), NoopRecorder(), tmp_path))

    assert statuses["FFmpeg"]["state"] == "ok"
    assert statuses["Whisper"]["state"] == "error"
    assert statuses["Whisper"]["message"] == "Whisper CLI не найден."


def test_readiness_reports_faster_whisper_backend_available(tmp_path: Path) -> None:
    with (
        patch("app.services.readiness.shutil.which", return_value="/bin/ffmpeg"),
        patch("app.services.readiness.importlib.util.find_spec", return_value=object()),
    ):
        statuses = _by_component(
            check_readiness(
                _config_with_transcription({"backend": "faster_whisper"}),
                NoopRecorder(),
                tmp_path,
            )
        )

    assert statuses["Whisper"]["state"] == "ok"
    assert statuses["Whisper"]["message"] == "faster-whisper доступен."


def test_readiness_reports_faster_whisper_backend_missing(tmp_path: Path) -> None:
    with (
        patch("app.services.readiness.shutil.which", return_value="/bin/ffmpeg"),
        patch("app.services.readiness.importlib.util.find_spec", return_value=None),
    ):
        statuses = _by_component(
            check_readiness(
                _config_with_transcription({"backend": "faster_whisper"}),
                NoopRecorder(),
                tmp_path,
            )
        )

    assert statuses["Whisper"]["state"] == "error"
    assert "faster-whisper не установлен" in statuses["Whisper"]["message"]


def test_readiness_reports_summary_disabled_without_api_key(tmp_path: Path) -> None:
    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = _by_component(check_readiness(_config(enabled=False), NoopRecorder(), tmp_path))

    assert statuses["Summary"]["state"] == "skipped"
    assert (
        statuses["API key"]["message"]
        == "API key не требуется: summary и внешняя транскрипция выключены."
    )
    assert statuses["Summary endpoint"]["state"] == "skipped"


def test_readiness_reports_summary_key_and_aitunnel_endpoint_without_revealing_secret(
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
    mapped = _by_component(statuses)
    assert mapped["API key"]["message"] == "API key найден."
    assert mapped["Summary endpoint"]["message"] == "Используется AI Tunnel endpoint."
    assert "test-secret-value" not in rendered


def test_readiness_reports_data_folder_available(tmp_path: Path) -> None:
    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = _by_component(check_readiness(_config(), NoopRecorder(), tmp_path))

    assert statuses["Папка данных"]["state"] == "ok"


def test_readiness_reports_aitunnel_transcription_key_and_endpoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITUNNEL_KEY", "test-secret-value")
    config = _config_with_transcription(
        {
            "backend": "aitunnel",
            "api_key_env": "AITUNNEL_KEY",
            "base_url": "https://api.aitunnel.ru/v1/",
        }
    )

    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = check_readiness(config, NoopRecorder(), tmp_path)

    rendered = str(statuses)
    mapped = _by_component(statuses)
    assert mapped["Whisper"]["message"] == "AI Tunnel STT настроен."
    assert mapped["API key"]["message"] == "API key найден."
    assert mapped["Summary endpoint"]["message"] == "Используется AI Tunnel endpoint."
    assert "test-secret-value" not in rendered


def test_readiness_requires_summary_and_external_transcription_keys(
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
        "api_key_env": "AITUNNEL_KEY",
        "base_url": "https://api.aitunnel.ru/v1/",
    }

    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = check_readiness(config, NoopRecorder(), tmp_path)

    rendered = str(statuses)
    mapped = _by_component(statuses)
    assert mapped["API key"]["state"] == "error"
    assert mapped["API key"]["message"] == (
        "API key не найден: AITUNNEL_KEY. Проверьте переменную окружения или .env.local."
    )
    assert mapped["Summary endpoint"]["message"] == "Используются разные AI endpoints."
    assert "summary-secret-value" not in rendered
