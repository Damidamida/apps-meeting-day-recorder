from app.config import load_config


def test_obs_is_disabled_by_default(tmp_path) -> None:
    config = load_config(tmp_path / "missing.yaml")

    assert config["obs"]["enabled"] is False
    assert config["obs"]["websocket_host"] == "localhost"
    assert config["obs"]["websocket_port"] == 4455


def test_partial_obs_config_uses_safe_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("obs:\n  enabled: true\n", encoding="utf-8")

    config = load_config(config_path)

    assert config["obs"]["enabled"] is True
    assert config["obs"]["websocket_host"] == "localhost"
    assert config["obs"]["websocket_password"] == ""
