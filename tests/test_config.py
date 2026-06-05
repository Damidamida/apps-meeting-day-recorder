from app.config import load_config


def test_obs_is_disabled_by_default(tmp_path) -> None:
    config = load_config(tmp_path / "missing.yaml")

    assert config["obs"]["enabled"] is False
    assert config["obs"]["websocket_host"] == "localhost"
    assert config["obs"]["websocket_port"] == 4455
    assert config["summary"]["enabled"] is False
    assert config["summary"]["provider"] == "openai"
    assert config["summary"]["api_key_env"] == "OPENAI_API_KEY"
    assert config["summary"]["base_url"] == ""
    assert config["transcription"]["backend"] == "whisper_cli"
    assert config["transcription"]["model"] == "base"
    assert config["transcription"]["compute_type"] == "int8"
    assert config["ui"]["theme"] == "light"
    assert config["ui"]["floating_theme"] == "inherit"


def test_partial_obs_config_uses_safe_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("obs:\n  enabled: true\n", encoding="utf-8")

    config = load_config(config_path)

    assert config["obs"]["enabled"] is True
    assert config["obs"]["websocket_host"] == "localhost"
    assert config["obs"]["websocket_password"] == ""


def test_partial_summary_config_uses_safe_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("summary:\n  enabled: true\n", encoding="utf-8")

    config = load_config(config_path)

    assert config["summary"]["enabled"] is True
    assert config["summary"]["provider"] == "openai"
    assert config["summary"]["model"] == "gpt-5.4-mini"
    assert config["summary"]["base_url"] == ""
    assert config["summary"]["env_file"] == ""


def test_transcription_config_supports_faster_whisper_backend(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "transcription:\n"
        "  backend: faster_whisper\n"
        "  model: small\n"
        "  device: cpu\n"
        "  compute_type: int8\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["transcription"]["backend"] == "faster_whisper"
    assert config["transcription"]["model"] == "small"
    assert config["transcription"]["device"] == "cpu"
    assert config["transcription"]["compute_type"] == "int8"


def test_unknown_transcription_backend_falls_back_to_whisper_cli(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "transcription:\n"
        "  backend: something_else\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["transcription"]["backend"] == "whisper_cli"


def test_ui_config_supports_main_and_floating_themes(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "ui:\n"
        "  theme: dark\n"
        "  floating_theme: light\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["ui"]["theme"] == "dark"
    assert config["ui"]["floating_theme"] == "light"


def test_unknown_ui_theme_values_fall_back_to_safe_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "ui:\n"
        "  theme: neon\n"
        "  floating_theme: system\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["ui"]["theme"] == "light"
    assert config["ui"]["floating_theme"] == "inherit"


def test_invalid_yaml_uses_safe_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("summary:\n  enabled: [", encoding="utf-8")

    config = load_config(config_path)

    assert config["summary"]["enabled"] is False
    assert config["_warnings"]


def test_invalid_numeric_summary_fields_use_safe_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "summary:\n"
        "  enabled: true\n"
        "  timeout_seconds: nope\n"
        "  max_chars_per_chunk: 0\n"
        "  base_url: '  https://api.proxyapi.ru/openai/v1  '\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["summary"]["timeout_seconds"] == 120
    assert config["summary"]["max_chars_per_chunk"] == 20000
    assert config["summary"]["base_url"] == "https://api.proxyapi.ru/openai/v1"
    assert len(config["_warnings"]) == 2
