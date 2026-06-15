import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import yaml
from openai import AuthenticationError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QWidget

import app.ui.main_window as main_window_module
import app.ui.first_run_wizard as first_run_wizard_module
from app.services.first_run import default_setup_config, mark_step_ok, normalize_setup_config
from app.services.recorder import NoopRecorder, RecorderError
from app.services.storage import StorageService
from app.ui.first_run_wizard import FirstRunWizard
from app.ui.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_for_qt(app: QApplication, condition, timeout_seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    app.processEvents()
    return condition()


class DelayedFailingRecorder:
    enabled = True

    def __init__(self, delay_seconds: float = 0.15) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0

    def check_connection(self) -> None:
        self.calls += 1
        time.sleep(self.delay_seconds)
        raise RecorderError("OBS unavailable")


class SuccessfulRecorder:
    enabled = True

    def __init__(self) -> None:
        self.calls = 0

    def check_connection(self) -> None:
        self.calls += 1


class RecordingObsRecorderFactory:
    def __init__(self, recorder, expected_password: str = "") -> None:
        self.recorder = recorder
        self.expected_password = expected_password
        self.calls = []

    def __call__(self, *, host: str, port: int, password: str):
        self.calls.append(
            {
                "host": host,
                "port": port,
                "password_matches": password == self.expected_password,
            }
        )
        return self.recorder


class DelayedAIClientFactory:
    def __init__(self, outcome: str = "ok", delay_seconds: float = 0.01) -> None:
        self.outcome = outcome
        self.delay_seconds = delay_seconds
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            models=SimpleNamespace(
                list=self._list_models,
            )
        )

    def _list_models(self):
        time.sleep(self.delay_seconds)
        if self.outcome == "auth":
            response = httpx.Response(
                401,
                request=httpx.Request("GET", "https://api.aitunnel.ru/v1/models"),
            )
            raise AuthenticationError("bad key", response=response, body=None)
        if self.outcome == "network":
            raise RuntimeError("service unavailable")
        return SimpleNamespace(data=[SimpleNamespace(id="ok")])


class DelayedSummaryClientFactory:
    def __init__(self, outcome: str = "ok", delay_seconds: float = 0.01) -> None:
        self.outcome = outcome
        self.delay_seconds = delay_seconds
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            responses=SimpleNamespace(
                create=self._create_response,
            )
        )

    def _create_response(self, **kwargs):
        del kwargs
        time.sleep(self.delay_seconds)
        if self.outcome == "network":
            raise RuntimeError("summary unavailable")
        return SimpleNamespace(output_text="Готово")


def _write_config(path: Path, setup_completed: bool) -> None:
    setup = default_setup_config()
    if setup_completed:
        setup["completed"] = True
        setup["current_step"] = "finish"
        for step in setup["steps"].values():
            step["status"] = "ok"
            step["message"] = "Готово"
    path.write_text(
        yaml.safe_dump(
            {
                "storage": {"root": str(path.parent / "data")},
                "setup": setup,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _state_on_obs_step():
    state = normalize_setup_config(default_setup_config())
    return mark_step_ok(state, "data_root", "Готово")


def _state_on_aitunnel_step():
    state = _state_on_obs_step()
    state = mark_step_ok(state, "obs", "Готово")
    return mark_step_ok(state, "audio", "Готово")


def _state_on_summary_step():
    state = _state_on_aitunnel_step()
    state = mark_step_ok(state, "aitunnel", "Готово")
    return mark_step_ok(state, "transcription", "Готово")


def _summary_config_with_env(tmp_path: Path) -> dict:
    env_file = tmp_path / "secrets" / ".env.local"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text('AITUNNEL_KEY="test-secret-key"\n', encoding="utf-8")
    return {"secrets": {"env_file": str(env_file)}}


def test_first_run_wizard_is_full_screen_page_with_locked_future_steps() -> None:
    app = _app()
    wizard = FirstRunWizard({}, normalize_setup_config(default_setup_config()))
    wizard.show()
    app.processEvents()

    labels = [label.text() for label in wizard.findChildren(QLabel)]
    assert "Настройка BK Scribe" in labels
    assert "Готовность к работе" not in labels
    assert wizard.step_list_panel.minimumHeight() == wizard.step_content_panel.minimumHeight()
    assert wizard.step_buttons["data_root"].isEnabled()
    assert not wizard.step_buttons["obs"].isEnabled()
    button_texts = [button.text() for button in wizard.findChildren(QPushButton)]
    assert "Назад" not in button_texts
    assert "Далее" not in button_texts
    assert wizard.aitunnel_link.openExternalLinks()
    assert wizard.aitunnel_link.text().find("https://aitunnel.ru/") != -1
    assert wizard.transcription_backend_select.itemText(0) == "AI Tunnel STT"
    assert wizard.transcription_backend_select.currentText() == "AI Tunnel STT"
    assert not wizard.summary_page.findChildren(QLineEdit)

    wizard.close()


def test_first_run_wizard_layout_matches_mockup_structure() -> None:
    app = _app()
    wizard = FirstRunWizard({}, normalize_setup_config(default_setup_config()))
    wizard.resize(1280, 760)
    wizard.show()
    app.processEvents()

    assert wizard.findChild(QWidget, "firstRunWizardShell") is not None
    assert wizard.findChild(QWidget, "firstRunWizardIntro") is not None
    assert wizard.findChild(QWidget, "firstRunWizardBody") is not None
    assert wizard.findChild(QWidget, "firstRunProgressPill") is wizard.progress_label
    assert wizard.findChild(QWidget, "firstRunPanelHeader") is not None
    assert wizard.findChild(QWidget, "firstRunPanelBody") is not None
    assert wizard.findChild(QWidget, "firstRunPanelFooter") is None

    assert wizard.step_list_panel.minimumWidth() >= 300
    assert wizard.step_list_panel.maximumWidth() <= 360
    assert wizard.step_content_panel.minimumWidth() >= 620
    assert wizard.step_list_panel.minimumHeight() == wizard.step_content_panel.minimumHeight()
    assert wizard.step_buttons["data_root"].minimumWidth() >= 280
    assert 72 <= wizard.step_buttons["data_root"].minimumHeight() <= 80
    assert wizard.step_buttons["data_root"].maximumHeight() <= 84
    assert wizard.step_buttons["data_root"].objectName() == "firstRunStepCard"
    assert wizard.step_buttons["data_root"].property("active") is True
    assert wizard.step_buttons["data_root"].property("state") == "active"
    assert wizard.step_buttons["obs"].property("state") == "locked"
    assert wizard.step_buttons["obs"].property("locked") is True
    assert wizard.step_status_labels["obs"].objectName() == "firstRunStepStatusIcon"
    assert wizard.step_status_labels["obs"].isVisibleTo(wizard.step_list_panel)
    assert wizard.step_status_labels["obs"].wordWrap() is False
    assert wizard.step_status_labels["obs"].text() == "\ue72e"
    assert wizard.step_status_labels["obs"].font().family() == "Segoe MDL2 Assets"
    assert wizard.findChild(QWidget, "firstRunStepNumber") is not None
    assert wizard.findChild(QWidget, "firstRunStepTitle") is not None
    assert wizard.findChild(QWidget, "firstRunStepNote") is not None
    assert wizard.step_message_label["data_root"].wordWrap()
    assert wizard.finish_button is not None
    assert wizard.finish_button.parentWidget().objectName() == "firstRunFormCard"

    wizard.close()


def test_first_run_wizard_transcription_models_are_fixed_per_backend() -> None:
    app = _app()
    wizard = FirstRunWizard({}, _state_on_aitunnel_step())
    wizard.show()
    app.processEvents()

    assert not wizard.transcription_model_select.isEditable()
    assert wizard.transcription_model_select.lineEdit() is None
    assert [
        wizard.transcription_model_select.itemData(index)
        for index in range(wizard.transcription_model_select.count())
    ] == ["whisper-large-v3-turbo", "whisper-large-v3", "whisper-1"]
    assert wizard.transcription_model_select.currentData() == "whisper-large-v3-turbo"

    wizard.transcription_backend_select.setCurrentText("faster-whisper")
    app.processEvents()
    assert not wizard.transcription_model_select.isEditable()
    assert [
        wizard.transcription_model_select.itemData(index)
        for index in range(wizard.transcription_model_select.count())
    ] == ["tiny", "base", "small", "medium", "large-v3", "turbo"]

    wizard.transcription_backend_select.setCurrentText("Whisper CLI")
    app.processEvents()
    assert not wizard.transcription_model_select.isEditable()
    assert [
        wizard.transcription_model_select.itemData(index)
        for index in range(wizard.transcription_model_select.count())
    ] == ["tiny", "base", "small", "medium", "large", "turbo"]

    wizard.close()


def test_first_run_wizard_saves_faster_whisper_with_local_model() -> None:
    app = _app()
    wizard = FirstRunWizard({}, _state_on_aitunnel_step())
    wizard.show()
    app.processEvents()
    wizard.transcription_backend_select.setCurrentText("faster-whisper")

    assert wizard.transcription_model_select.currentData() == "base"

    with patch("app.services.first_run.importlib.util.find_spec", return_value=object()):
        wizard.check_transcription()

    assert wizard.config["transcription"]["backend"] == "faster_whisper"
    assert wizard.config["transcription"]["model"] == "base"
    assert (
        wizard.config["transcription"]["backends"]["faster_whisper"]["model"]
        == "base"
    )
    assert wizard.state.values["transcription_backend"] == "faster_whisper"
    assert wizard.state.values["transcription_model"] == "base"

    wizard.close()


def test_first_run_wizard_saves_whisper_cli_with_local_model() -> None:
    app = _app()
    wizard = FirstRunWizard({}, _state_on_aitunnel_step())
    wizard.show()
    app.processEvents()
    wizard.transcription_backend_select.setCurrentText("Whisper CLI")

    assert wizard.transcription_model_select.currentData() == "base"

    with patch("app.services.first_run.shutil.which", return_value="C:/tools/whisper.exe"):
        wizard.check_transcription()

    assert wizard.config["transcription"]["backend"] == "whisper_cli"
    assert wizard.config["transcription"]["model"] == "base"
    assert wizard.config["transcription"]["backends"]["whisper_cli"]["model"] == "base"
    assert wizard.state.values["transcription_backend"] == "whisper_cli"
    assert wizard.state.values["transcription_model"] == "base"

    wizard.close()


def test_first_run_wizard_stepper_cards_have_active_done_and_locked_states() -> None:
    app = _app()
    state = normalize_setup_config(default_setup_config())
    state = state.__class__(
        completed=state.completed,
        version=state.version,
        completed_at=state.completed_at,
        current_step="obs",
        steps={
            **state.steps,
            "data_root": state.steps["data_root"].__class__(
                key="data_root",
                title=state.steps["data_root"].title,
                status="ok",
                message="Готово",
            ),
            "obs": state.steps["obs"].__class__(
                key="obs",
                title=state.steps["obs"].title,
                status="todo",
                message="Требует проверки.",
            ),
            "audio": state.steps["audio"].__class__(
                key="audio",
                title=state.steps["audio"].title,
                status="locked",
                message="Заблокировано",
            ),
        },
        values=dict(state.values),
    )
    wizard = FirstRunWizard({}, state)
    wizard.show()
    app.processEvents()

    assert wizard.step_buttons["data_root"].property("state") == "done"
    assert wizard.step_status_labels["data_root"].text() == "\ue73e"
    assert wizard.step_buttons["obs"].property("active") is True
    assert wizard.step_buttons["obs"].property("state") == "active"
    assert wizard.step_status_labels["obs"].text() == ""
    assert wizard.step_buttons["audio"].property("state") == "locked"
    assert not wizard.step_buttons["audio"].isEnabled()
    assert wizard.step_status_labels["audio"].text() == "\ue72e"
    assert wizard.step_status_labels["audio"].font().family() == "Segoe MDL2 Assets"

    wizard.step_buttons["audio"].click()
    assert wizard.current_step == "obs"

    wizard.close()


def test_first_run_wizard_obs_step_shows_websocket_fields_with_defaults() -> None:
    app = _app()
    wizard = FirstRunWizard({}, _state_on_obs_step())
    wizard.show()
    app.processEvents()

    labels = [label.text() for label in wizard.findChildren(QLabel)]
    assert "Адрес WebSocket" in labels
    assert "Порт WebSocket" in labels
    assert "Пароль WebSocket" in labels
    assert wizard.obs_host_input.text() == "localhost"
    assert wizard.obs_port_input.value() == 4455
    assert wizard.obs_password_input.echoMode() == QLineEdit.EchoMode.Password
    assert wizard.obs_password_input.text() == ""

    wizard.close()


def test_obs_check_uses_entered_websocket_settings(monkeypatch) -> None:
    app = _app()
    password = "".join(chr(code) for code in (112, 97, 115, 115, 45, 49))
    recorder = SuccessfulRecorder()
    factory = RecordingObsRecorderFactory(recorder, expected_password=password)
    monkeypatch.setattr(first_run_wizard_module, "ObsRecorder", factory, raising=False)
    wizard = FirstRunWizard({}, _state_on_obs_step())
    wizard.obs_host_input.setText("127.0.0.1")
    wizard.obs_port_input.setValue(4456)
    wizard.obs_password_input.setText(password)
    wizard.show()
    app.processEvents()

    wizard.check_obs()

    assert _wait_for_qt(app, lambda: wizard.state.steps["obs"].status == "ok")
    assert factory.calls == [
        {
            "host": "127.0.0.1",
            "port": 4456,
            "password_matches": True,
        }
    ]

    wizard.close()


def test_obs_check_shows_checking_state_immediately_when_recorder_is_slow() -> None:
    app = _app()
    recorder = DelayedFailingRecorder()
    factory = RecordingObsRecorderFactory(recorder)
    wizard = FirstRunWizard(
        {},
        _state_on_obs_step(),
        obs_recorder_factory=factory,
    )
    wizard.show()
    app.processEvents()

    started_at = time.monotonic()
    wizard.check_obs()
    elapsed = time.monotonic() - started_at
    app.processEvents()

    assert elapsed < recorder.delay_seconds
    assert wizard.state.steps["obs"].status == "checking"
    assert wizard.step_message_label["obs"].text() == "Проверяется..."
    assert not wizard.obs_check_button.isEnabled()
    assert recorder.calls == 0 or _wait_for_qt(app, lambda: recorder.calls == 1)

    assert _wait_for_qt(app, lambda: wizard.state.steps["obs"].status == "error")
    wizard.close()


def test_obs_check_error_remains_visible_and_audio_stays_locked() -> None:
    app = _app()
    recorder = DelayedFailingRecorder(delay_seconds=0.01)
    factory = RecordingObsRecorderFactory(recorder)
    wizard = FirstRunWizard(
        {},
        _state_on_obs_step(),
        obs_recorder_factory=factory,
    )
    wizard.show()
    app.processEvents()

    wizard.check_obs()

    assert _wait_for_qt(app, lambda: wizard.state.steps["obs"].status == "error")
    assert wizard.current_step == "obs"
    assert wizard.state.steps["obs"].message == (
        "OBS не подключен. Проверьте адрес, порт, пароль и включенный WebSocket в OBS."
    )
    assert wizard.step_message_label["obs"].text() == (
        "OBS не подключен. Проверьте адрес, порт, пароль и включенный WebSocket в OBS."
    )
    assert wizard.step_buttons["obs"].property("state") == "error"
    assert wizard.step_buttons["audio"].property("state") == "locked"
    assert not wizard.step_buttons["audio"].isEnabled()
    assert wizard.obs_check_button.isEnabled()

    wizard.close()


def test_obs_check_success_opens_audio_step() -> None:
    app = _app()
    password = "".join(chr(code) for code in (112, 97, 115, 115, 45, 50))
    recorder = SuccessfulRecorder()
    factory = RecordingObsRecorderFactory(recorder, expected_password=password)
    wizard = FirstRunWizard(
        {},
        _state_on_obs_step(),
        obs_recorder_factory=factory,
    )
    wizard.obs_host_input.setText("obs.local")
    wizard.obs_port_input.setValue(4457)
    wizard.obs_password_input.setText(password)
    wizard.show()
    app.processEvents()

    wizard.check_obs()

    assert _wait_for_qt(app, lambda: wizard.state.steps["obs"].status == "ok")
    assert wizard.current_step == "audio"
    assert wizard.step_buttons["audio"].isEnabled()
    assert wizard.obs_check_button.isEnabled()
    assert wizard.config["obs"]["websocket_host"] == "obs.local"
    assert wizard.config["obs"]["websocket_port"] == 4457
    saved_password = wizard.config["obs"]["websocket_password"]
    assert len(saved_password) == len(password)
    assert all(
        ord(saved_char) == ord(expected_char)
        for saved_char, expected_char in zip(saved_password, password)
    )
    assert wizard.state.values["obs_websocket_host"] == "obs.local"
    assert wizard.state.values["obs_websocket_port"] == 4457
    assert wizard.state.values["obs_password_configured"] is True

    wizard.close()


def test_aitunnel_check_shows_checking_state_immediately_when_client_is_slow(
    tmp_path: Path,
) -> None:
    app = _app()
    factory = DelayedAIClientFactory(outcome="network", delay_seconds=0.15)
    config = {"secrets": {"env_file": str(tmp_path / "secrets" / ".env.local")}}
    wizard = FirstRunWizard(
        config,
        _state_on_aitunnel_step(),
        aitunnel_client_factory=factory,
    )
    wizard.aitunnel_key_input.setText("test-key")
    wizard.show()
    app.processEvents()

    started_at = time.monotonic()
    wizard.check_aitunnel()
    elapsed = time.monotonic() - started_at
    app.processEvents()

    assert elapsed < factory.delay_seconds
    assert wizard.state.steps["aitunnel"].status == "checking"
    assert wizard.step_message_label["aitunnel"].text() == "Проверяется ключ..."
    assert not wizard.aitunnel_check_button.isEnabled()
    assert factory.calls == [] or _wait_for_qt(app, lambda: len(factory.calls) == 1)

    assert _wait_for_qt(app, lambda: wizard.state.steps["aitunnel"].status == "error")
    wizard.close()


def test_aitunnel_empty_input_shows_error_without_client_call(tmp_path: Path) -> None:
    app = _app()
    factory = DelayedAIClientFactory()
    config = {"secrets": {"env_file": str(tmp_path / ".env")}}
    wizard = FirstRunWizard(
        config,
        _state_on_aitunnel_step(),
        aitunnel_client_factory=factory,
    )
    wizard.aitunnel_key_input.setText(" ")
    wizard.show()
    app.processEvents()

    wizard.check_aitunnel()

    assert wizard.state.steps["aitunnel"].status == "error"
    assert wizard.step_message_label["aitunnel"].text() == "Введите AI Tunnel key."
    assert factory.calls == []
    assert wizard.aitunnel_check_button.isEnabled()
    assert not (tmp_path / ".env").exists()

    wizard.close()


def test_aitunnel_invalid_key_keeps_visible_error_and_locks_transcription(
    tmp_path: Path,
) -> None:
    app = _app()
    factory = DelayedAIClientFactory(outcome="auth")
    config = {"secrets": {"env_file": str(tmp_path / ".env")}}
    wizard = FirstRunWizard(
        config,
        _state_on_aitunnel_step(),
        aitunnel_client_factory=factory,
    )
    wizard.aitunnel_key_input.setText("bad-key")
    wizard.show()
    app.processEvents()

    wizard.check_aitunnel()

    assert _wait_for_qt(app, lambda: wizard.state.steps["aitunnel"].status == "error")
    assert wizard.current_step == "aitunnel"
    assert wizard.step_message_label["aitunnel"].text() == "Ключ не подошел."
    assert wizard.step_buttons["aitunnel"].property("state") == "error"
    assert wizard.step_buttons["transcription"].property("state") == "locked"
    assert not wizard.step_buttons["transcription"].isEnabled()
    assert wizard.aitunnel_check_button.isEnabled()
    assert not (tmp_path / ".env").exists()

    wizard.close()


def test_aitunnel_service_error_keeps_visible_error(tmp_path: Path) -> None:
    app = _app()
    factory = DelayedAIClientFactory(outcome="network")
    config = {"secrets": {"env_file": str(tmp_path / ".env")}}
    wizard = FirstRunWizard(
        config,
        _state_on_aitunnel_step(),
        aitunnel_client_factory=factory,
    )
    wizard.aitunnel_key_input.setText("test-key")
    wizard.show()
    app.processEvents()

    wizard.check_aitunnel()

    assert _wait_for_qt(app, lambda: wizard.state.steps["aitunnel"].status == "error")
    assert wizard.step_message_label["aitunnel"].text() == "Сервис временно недоступен."
    assert wizard.aitunnel_check_button.isEnabled()
    assert not (tmp_path / ".env").exists()

    wizard.close()


def test_aitunnel_success_writes_env_and_opens_transcription_step(
    tmp_path: Path,
) -> None:
    app = _app()
    factory = DelayedAIClientFactory(outcome="ok")
    env_file = tmp_path / "secrets" / ".env.local"
    config = {"secrets": {"env_file": str(env_file)}}
    wizard = FirstRunWizard(
        config,
        _state_on_aitunnel_step(),
        aitunnel_client_factory=factory,
    )
    wizard.aitunnel_key_input.setText("test-secret-key")
    wizard.show()
    app.processEvents()

    wizard.check_aitunnel()

    assert _wait_for_qt(app, lambda: wizard.state.steps["aitunnel"].status == "ok")
    assert wizard.current_step == "transcription"
    assert wizard.step_buttons["transcription"].isEnabled()
    assert wizard.aitunnel_check_button.isEnabled()
    assert 'AITUNNEL_KEY="test-secret-key"' in env_file.read_text(encoding="utf-8")
    assert factory.calls[0]["api_key"] == "test-secret-key"

    wizard.close()


def test_summary_check_shows_checking_state_immediately_when_client_is_slow(
    tmp_path: Path,
) -> None:
    app = _app()
    factory = DelayedSummaryClientFactory(outcome="network", delay_seconds=0.15)
    wizard = FirstRunWizard(
        _summary_config_with_env(tmp_path),
        _state_on_summary_step(),
        summary_client_factory=factory,
    )
    wizard.show()
    app.processEvents()

    started_at = time.monotonic()
    wizard.check_summary()
    elapsed = time.monotonic() - started_at
    app.processEvents()

    assert elapsed < factory.delay_seconds
    assert wizard.state.steps["summary"].status == "checking"
    assert wizard.step_message_label["summary"].text() == "Проверяются AI-итоги..."
    assert not wizard.summary_check_button.isEnabled()
    assert factory.calls == [] or _wait_for_qt(app, lambda: len(factory.calls) == 1)

    assert _wait_for_qt(app, lambda: wizard.state.steps["summary"].status == "error")
    wizard.close()


def test_summary_check_failure_keeps_visible_error_and_finish_locked(
    tmp_path: Path,
) -> None:
    app = _app()
    factory = DelayedSummaryClientFactory(outcome="network")
    wizard = FirstRunWizard(
        _summary_config_with_env(tmp_path),
        _state_on_summary_step(),
        summary_client_factory=factory,
    )
    wizard.show()
    app.processEvents()

    wizard.check_summary()

    assert _wait_for_qt(app, lambda: wizard.state.steps["summary"].status == "error")
    assert wizard.current_step == "summary"
    assert wizard.step_message_label["summary"].text() == "Сервис временно недоступен."
    assert wizard.step_buttons["summary"].property("state") == "error"
    assert wizard.step_buttons["finish"].property("state") == "locked"
    assert not wizard.step_buttons["finish"].isEnabled()
    assert wizard.summary_check_button.isEnabled()

    wizard.close()


def test_summary_check_success_opens_finish_step(tmp_path: Path) -> None:
    app = _app()
    factory = DelayedSummaryClientFactory(outcome="ok")
    wizard = FirstRunWizard(
        _summary_config_with_env(tmp_path),
        _state_on_summary_step(),
        summary_client_factory=factory,
    )
    wizard.show()
    app.processEvents()

    wizard.check_summary()

    assert _wait_for_qt(app, lambda: wizard.state.steps["summary"].status == "ok")
    assert wizard.current_step == "finish"
    assert wizard.step_buttons["finish"].isEnabled()
    assert wizard.summary_check_button.isEnabled()

    wizard.close()


def test_setup_gate_opens_wizard_and_blocks_workday_sections(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _app()
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path / "config.yaml", setup_completed=False)
    storage = StorageService(tmp_path / "data", NoopRecorder())

    window = MainWindow(storage, NoopRecorder())
    app.processEvents()

    assert window.pages.currentWidget() is window.first_run_wizard
    assert not window.nav_buttons[0].isEnabled()
    assert not window.nav_buttons[1].isEnabled()
    assert not window.nav_buttons[2].isEnabled()
    assert not window.nav_buttons[3].isEnabled()
    assert not window.nav_buttons[4].isEnabled()

    window.nav_buttons[3].click()
    assert window.pages.currentWidget() is window.first_run_wizard
    window.nav_buttons[4].click()
    assert window.pages.currentWidget() is window.first_run_wizard

    window.open_review()
    assert window.pages.currentWidget() is window.first_run_wizard
    window.open_archive()
    assert window.pages.currentWidget() is window.first_run_wizard
    window.start_workday()
    assert "Завершите настройку BK Scribe" in window.status_label.text()
    assert storage.workday_active is False

    window.close()


def test_main_window_loads_windows_ui_font_for_cyrillic_offscreen_rendering(
    tmp_path: Path,
    monkeypatch,
) -> None:
    if not Path("C:/Windows/Fonts/segoeui.ttf").exists():
        pytest.skip("Segoe UI font file is not available on this runner")
    app = _app()
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path / "config.yaml", setup_completed=False)
    storage = StorageService(tmp_path / "data", NoopRecorder())
    monkeypatch.setattr(main_window_module, "_WINDOWS_UI_FONTS_LOADED", False)
    loaded_paths: list[str] = []
    original_add = main_window_module.QFontDatabase.addApplicationFont

    def _spy_add_application_font(path: str) -> int:
        loaded_paths.append(path)
        return original_add(path)

    monkeypatch.setattr(
        main_window_module.QFontDatabase,
        "addApplicationFont",
        _spy_add_application_font,
    )

    window = MainWindow(storage, NoopRecorder())
    app.processEvents()

    assert any(path.lower().endswith("segoeui.ttf") for path in loaded_paths)

    window.close()


def test_setup_completed_allows_navigation_and_start_workday(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _app()
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path / "config.yaml", setup_completed=True)
    storage = StorageService(tmp_path / "data", NoopRecorder())

    window = MainWindow(storage, NoopRecorder())
    app.processEvents()

    assert window.pages.currentIndex() == 0
    assert all(window.nav_buttons[index].isEnabled() for index in (0, 1, 2, 3, 4))

    window.start_workday()

    assert storage.workday_active is True
    assert "Рабочий день начат" in window.status_label.text()

    window.close()


def test_setup_completion_reloads_storage_state_and_restores_floating_control(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _app()
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path / "config.yaml", setup_completed=False)
    storage = StorageService(tmp_path / "old-data", NoopRecorder())
    window = MainWindow(storage, NoopRecorder())
    app.processEvents()
    calls = []

    def fake_load_today_state() -> None:
        calls.append("load_today_state")

    def fake_find_past_active_workday():
        calls.append("find_past_active_workday")
        return None

    window.storage.load_today_state = fake_load_today_state
    window.storage.find_past_active_workday = fake_find_past_active_workday
    window.refresh_status = lambda: calls.append("refresh_status")
    window.refresh_buttons = lambda: calls.append("refresh_buttons")
    window.show_floating_control = lambda: calls.append("show_floating_control")

    setup = default_setup_config()
    setup["completed"] = True
    setup["version"] = 1
    for step in setup["steps"].values():
        step["status"] = "ok"
        step["message"] = "Готово"

    window._on_first_run_completed(
        {
            **window.config,
            "storage": {"root": str(tmp_path / "new-data")},
            "setup": setup,
        }
    )

    assert window.storage.root == tmp_path / "new-data"
    assert calls == [
        "load_today_state",
        "find_past_active_workday",
        "refresh_status",
        "refresh_buttons",
        "show_floating_control",
    ]

    window.close()
