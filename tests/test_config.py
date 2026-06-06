from app.config import load_config


def test_obs_is_disabled_by_default(tmp_path) -> None:
    config = load_config(tmp_path / "missing.yaml")

    assert config["obs"]["enabled"] is False
    assert config["obs"]["websocket_host"] == "localhost"
    assert config["obs"]["websocket_port"] == 4455
    assert config["summary"]["enabled"] is False
    assert config["summary"]["provider"] == "openai"
    assert config["summary"]["api_key_env"] == "AITUNNEL_KEY"
    assert config["summary"]["base_url"] == "https://api.aitunnel.ru/v1/"
    assert config["secrets"]["env_file"] == ""
    assert config["transcription"]["backend"] == "whisper_cli"
    assert config["transcription"]["model"] == "base"
    assert config["transcription"]["compute_type"] == "int8"
    assert config["transcription"]["vad_filter"] is True
    assert config["transcription"]["api_key_env"] == "AITUNNEL_KEY"
    assert config["transcription"]["base_url"] == "https://api.aitunnel.ru/v1/"
    assert config["transcription"]["timeout_seconds"] == 300
    assert config["transcription"]["max_upload_mb"] == 25
    assert config["transcription"]["backends"]["whisper_cli"]["model"] == "base"
    assert config["transcription"]["backends"]["faster_whisper"]["model"] == "base"
    assert (
        config["transcription"]["backends"]["aitunnel"]["model"]
        == "whisper-large-v3-turbo"
    )
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
    assert config["summary"]["api_key_env"] == "AITUNNEL_KEY"
    assert config["summary"]["base_url"] == "https://api.aitunnel.ru/v1/"
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
    assert config["transcription"]["vad_filter"] is True


def test_unknown_transcription_backend_falls_back_to_whisper_cli(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "transcription:\n"
        "  backend: something_else\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["transcription"]["backend"] == "whisper_cli"


def test_transcription_config_supports_aitunnel_backend(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "transcription:\n"
        "  backend: aitunnel\n"
        "  model: whisper-large-v3-turbo\n"
        "  language: ru\n"
        "  api_key_env: AITUNNEL_KEY\n"
        "  base_url: https://api.aitunnel.ru/v1/\n"
        "  env_file: C:/safe/.env.local\n"
        "  timeout_seconds: 240\n"
        "  max_upload_mb: 20\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["transcription"]["backend"] == "aitunnel"
    assert config["transcription"]["model"] == "whisper-large-v3-turbo"
    assert config["transcription"]["api_key_env"] == "AITUNNEL_KEY"
    assert config["transcription"]["base_url"] == "https://api.aitunnel.ru/v1/"
    assert config["transcription"]["env_file"] == "C:/safe/.env.local"
    assert config["transcription"]["timeout_seconds"] == 240
    assert config["transcription"]["max_upload_mb"] == 20
    assert (
        config["transcription"]["backends"]["aitunnel"]["model"]
        == "whisper-large-v3-turbo"
    )
    assert config["transcription"]["backends"]["whisper_cli"]["model"] == "base"


def test_config_allows_zero_retry_attempts(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "summary:\n"
        "  retry_attempts: 0\n"
        "transcription:\n"
        "  backend: aitunnel\n"
        "  backends:\n"
        "    aitunnel:\n"
        "      retry_attempts: 0\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["summary"]["retry_attempts"] == 0
    assert config["transcription"]["retry_attempts"] == 0
    assert config["transcription"]["backends"]["aitunnel"]["retry_attempts"] == 0


def test_transcription_config_supports_backend_profiles(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "transcription:\n"
        "  backend: aitunnel\n"
        "  backends:\n"
        "    whisper_cli:\n"
        "      model: small\n"
        "    faster_whisper:\n"
        "      model: medium\n"
        "      device: cuda\n"
        "      vad_filter: false\n"
        "    aitunnel:\n"
        "      model: whisper-large-v3\n"
        "      timeout_seconds: 240\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["transcription"]["backend"] == "aitunnel"
    assert config["transcription"]["model"] == "whisper-large-v3"
    assert config["transcription"]["timeout_seconds"] == 240
    assert config["transcription"]["backends"]["whisper_cli"]["model"] == "small"
    assert config["transcription"]["backends"]["faster_whisper"]["model"] == "medium"
    assert config["transcription"]["backends"]["faster_whisper"]["device"] == "cuda"
    assert config["transcription"]["backends"]["faster_whisper"]["compute_type"] == "float16"
    assert config["transcription"]["backends"]["faster_whisper"]["vad_filter"] is False
    assert config["transcription"]["backends"]["aitunnel"]["model"] == "whisper-large-v3"


def test_legacy_flat_transcription_config_only_updates_active_backend(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "transcription:\n"
        "  backend: aitunnel\n"
        "  model: whisper-1\n"
        "  timeout_seconds: 180\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["transcription"]["backend"] == "aitunnel"
    assert config["transcription"]["model"] == "whisper-1"
    assert config["transcription"]["backends"]["aitunnel"]["model"] == "whisper-1"
    assert config["transcription"]["backends"]["aitunnel"]["timeout_seconds"] == 180
    assert config["transcription"]["backends"]["whisper_cli"]["model"] == "base"
    assert config["transcription"]["backends"]["faster_whisper"]["model"] == "base"


def test_config_supports_shared_secrets_env_file(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "secrets:\n"
        "  env_file: C:/safe/.env.local\n"
        "summary:\n"
        "  enabled: true\n"
        "transcription:\n"
        "  backend: aitunnel\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["secrets"]["env_file"] == "C:/safe/.env.local"


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
