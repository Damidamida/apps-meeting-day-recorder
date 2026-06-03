from pathlib import Path
from unittest.mock import patch

from app.services.readiness import check_readiness
from app.services.recorder import NoopRecorder


def _config(**summary_overrides):
    summary = {
        "enabled": False,
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "",
        "env_file": "",
    }
    summary.update(summary_overrides)
    return {"summary": summary}


def _by_component(statuses):
    return {status["component"]: status for status in statuses}


def test_readiness_reports_local_commands_found_and_missing(tmp_path: Path) -> None:
    def fake_which(command):
        return f"/bin/{command}" if command == "ffmpeg" else None

    with patch("app.services.readiness.shutil.which", side_effect=fake_which):
        statuses = _by_component(check_readiness(_config(), NoopRecorder(), tmp_path))

    assert statuses["FFmpeg"]["state"] == "ok"
    assert statuses["Whisper"]["state"] == "error"


def test_readiness_reports_summary_disabled_without_api_key(tmp_path: Path) -> None:
    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = _by_component(check_readiness(_config(enabled=False), NoopRecorder(), tmp_path))

    assert statuses["Summary"]["state"] == "skipped"
    assert statuses["API key"]["message"] == "API key не требуется: summary выключен."
    assert statuses["Summary endpoint"]["state"] == "skipped"


def test_readiness_reports_summary_key_and_custom_endpoint_without_revealing_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PROXYAPI_KEY", "test-secret-value")
    config = _config(
        enabled=True,
        api_key_env="PROXYAPI_KEY",
        base_url="https://api.proxyapi.ru/openai/v1",
    )

    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = check_readiness(config, NoopRecorder(), tmp_path)

    rendered = str(statuses)
    mapped = _by_component(statuses)
    assert mapped["API key"]["message"] == "API key найден."
    assert mapped["Summary endpoint"]["message"] == "Используется ProxyAPI / custom endpoint."
    assert "test-secret-value" not in rendered


def test_readiness_reports_data_folder_available(tmp_path: Path) -> None:
    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        statuses = _by_component(check_readiness(_config(), NoopRecorder(), tmp_path))

    assert statuses["Папка данных"]["state"] == "ok"
