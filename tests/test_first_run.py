from pathlib import Path
from types import SimpleNamespace

import httpx
from openai import AuthenticationError

from app.config import load_config
from app.services.first_run import (
    CURRENT_SETUP_VERSION,
    FIRST_RUN_STEPS,
    TRANSCRIPTION_OPTIONS,
    check_aitunnel_key,
    check_summary_settings,
    check_transcription_settings,
    default_data_root,
    default_setup_config,
    normalize_setup_config,
    reset_from_step,
    should_show_wizard_on_startup,
    setup_completed,
    validate_data_root,
)


def test_setup_config_defaults_to_incomplete(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.yaml")

    assert config["setup"]["completed"] is False
    assert config["setup"]["version"] == CURRENT_SETUP_VERSION
    assert tuple(config["setup"]["steps"]) == FIRST_RUN_STEPS
    assert FIRST_RUN_STEPS == (
        "data_root",
        "obs",
        "audio",
        "aitunnel",
        "transcription",
        "summary",
        "finish",
    )


def test_future_step_is_locked_until_previous_steps_are_ok() -> None:
    state = normalize_setup_config(default_setup_config())

    assert state.current_step == "data_root"
    assert state.steps["data_root"].status == "todo"
    assert state.steps["obs"].status == "locked"


def test_reset_from_aitunnel_resets_dependent_steps() -> None:
    state = normalize_setup_config(default_setup_config())
    for step in FIRST_RUN_STEPS:
        state.steps[step] = state.steps[step].with_status("ok", "Готово")

    reset = reset_from_step(state, "aitunnel")

    assert reset.steps["data_root"].status == "ok"
    assert reset.steps["obs"].status == "ok"
    assert reset.steps["audio"].status == "ok"
    assert reset.steps["aitunnel"].status == "todo"
    assert reset.steps["transcription"].status == "locked"
    assert reset.steps["summary"].status == "locked"
    assert reset.steps["finish"].status == "locked"


def test_setup_completed_requires_all_required_steps_ok() -> None:
    state = normalize_setup_config(default_setup_config())
    assert setup_completed(state) is False

    for step in FIRST_RUN_STEPS:
        state.steps[step] = state.steps[step].with_status("ok", "Готово")

    assert setup_completed(state) is True


def test_wizard_startup_gate_uses_completed_and_version_only() -> None:
    state = normalize_setup_config(default_setup_config())
    assert should_show_wizard_on_startup(state) is True

    for step in FIRST_RUN_STEPS:
        state.steps[step] = state.steps[step].with_status("ok", "Готово")
    state.completed = True
    state.version = CURRENT_SETUP_VERSION

    assert should_show_wizard_on_startup(state) is False

    state.version = CURRENT_SETUP_VERSION - 1
    assert should_show_wizard_on_startup(state) is True


def test_flat_setup_check_flags_restore_step_state() -> None:
    state = normalize_setup_config(
        {
            "completed": True,
            "version": CURRENT_SETUP_VERSION,
            "data_root_checked": True,
            "obs_checked": True,
            "audio_checked": True,
            "aitunnel_checked": True,
            "transcription_checked": True,
            "summary_checked": True,
        }
    )

    assert state.completed is True
    assert all(step.status == "ok" for step in state.steps.values())
    assert should_show_wizard_on_startup(state) is False


def test_default_data_root_is_documents_bk_scribe() -> None:
    assert default_data_root().name == "BK Scribe"
    assert default_data_root().parent.name == "Documents"


def test_validate_data_root_creates_folder_and_removes_probe(tmp_path: Path) -> None:
    data_root = tmp_path / "BK Scribe"

    result = validate_data_root(data_root)

    assert result.ok is True
    assert data_root.is_dir()
    assert not list(data_root.glob(".bk_scribe_setup_check_*"))


def test_validate_data_root_rejects_file_path(tmp_path: Path) -> None:
    file_path = tmp_path / "not-folder"
    file_path.write_text("content", encoding="utf-8")

    result = validate_data_root(file_path)

    assert result.ok is False
    assert "указывает на файл" in result.message


def test_aitunnel_key_success_writes_env_without_leaking_key(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=value\n", encoding="utf-8")
    calls = []

    def client_factory(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            models=SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id="ok")]))
        )

    result = check_aitunnel_key(
        "test-secret-key",
        {"secrets": {"env_file": str(env_file)}},
        client_factory=client_factory,
    )

    assert result.ok is True
    assert result.message == "Ключ AI Tunnel проверен."
    assert "test-secret-key" not in result.message
    assert "OTHER=value" in env_file.read_text(encoding="utf-8")
    assert "AITUNNEL_KEY=test-secret-key" in env_file.read_text(encoding="utf-8")
    assert calls[0]["api_key"] == "test-secret-key"


def test_aitunnel_key_authentication_error_does_not_write_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"

    def client_factory(**kwargs):
        del kwargs
        response = httpx.Response(401, request=httpx.Request("GET", "https://api.aitunnel.ru/v1/models"))
        raise AuthenticationError("bad key test-secret-key", response=response, body=None)

    result = check_aitunnel_key(
        "test-secret-key",
        {"secrets": {"env_file": str(env_file)}},
        client_factory=client_factory,
    )

    assert result.ok is False
    assert result.message == "Ключ не подошел."
    assert "test-secret-key" not in result.message
    assert not env_file.exists()


def test_transcription_options_default_to_aitunnel() -> None:
    assert TRANSCRIPTION_OPTIONS[0] == ("aitunnel", "AI Tunnel STT")
    assert [label for _, label in TRANSCRIPTION_OPTIONS] == [
        "AI Tunnel STT",
        "faster-whisper",
        "Whisper CLI",
    ]


def test_aitunnel_dependent_checks_require_verified_key() -> None:
    state = normalize_setup_config(default_setup_config())
    config = {"transcription": {"backend": "aitunnel"}, "summary": {"enabled": True}}

    transcription = check_transcription_settings(config, state)
    summary = check_summary_settings(config, state)

    assert transcription.ok is False
    assert transcription.message == "Сначала проверьте ключ AI Tunnel."
    assert summary.ok is False
    assert summary.message == "Сначала проверьте ключ AI Tunnel."
