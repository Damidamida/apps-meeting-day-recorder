import os
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import patch

import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QSizePolicy

from app.services.recorder import NoopRecorder
from app.services.storage import StorageService
from app.services.transcription import AITunnelTranscriber, LocalWhisperTranscriber
from app.ui.main_window import FloatingMeetingControl, MainWindow, StartMeetingOverlay


class EnabledRecorder(NoopRecorder):
    enabled = True
    status_text = "OBS: подключен"


def test_start_meeting_overlay_uses_prototype_style_and_validates_title() -> None:
    app = QApplication.instance() or QApplication([])
    overlay = StartMeetingOverlay(NoopRecorder())
    submitted_titles: list[str] = []
    overlay.submitted.connect(submitted_titles.append)

    assert overlay.objectName() == "meetingOverlay"
    assert overlay.card.objectName() == "meetingOverlayCard"
    assert overlay.title_input.objectName() == "meetingTitleInput"
    assert overlay.recording_status_label.text() == (
        "OBS недоступен или выключен, встреча начнется без записи"
    )
    assert overlay.recording_status_label.property("state") == "wait"

    overlay.open_for_recorder(NoopRecorder())
    overlay._accept_if_valid()

    assert not overlay.error_label.isHidden()
    assert submitted_titles == []

    overlay.title_input.setText("Синхронизация по релизу")
    overlay._accept_if_valid()

    assert submitted_titles == ["Синхронизация по релизу"]
    assert overlay.isHidden()
    overlay.close()

    enabled_overlay = StartMeetingOverlay(EnabledRecorder())

    assert enabled_overlay.recording_status_label.text() == "OBS будет запущен автоматически"
    assert enabled_overlay.recording_status_label.property("state") == "ok"
    enabled_overlay.close()
    app.processEvents()


def test_floating_control_states_validate_title_and_confirm_end() -> None:
    app = QApplication.instance() or QApplication([])
    control = FloatingMeetingControl()
    started_days: list[bool] = []
    started_meetings: list[str] = []
    ended_meetings: list[bool] = []
    control.start_workday_requested.connect(lambda: started_days.append(True))
    control.start_meeting_requested.connect(started_meetings.append)
    control.end_meeting_requested.connect(lambda: ended_meetings.append(True))

    assert control.state_label.text() == "Рабочий день не начат"
    assert control.primary_button.text() == "Начать рабочий день"

    control.primary_button.click()
    assert started_days == [True]

    control.update_state(
        workday_active=True,
        meeting_active=False,
        recorder_enabled=False,
        pipeline_running=False,
    )
    assert control.primary_button.text() == "Начать созвон"

    control.primary_button.click()
    assert not control.title_input.isHidden()

    control.primary_button.click()
    assert not control.error_label.isHidden()
    assert started_meetings == []

    control.title_input.setText("Быстрый созвон")
    control.primary_button.click()
    assert started_meetings == ["Быстрый созвон"]

    control.update_state(
        workday_active=True,
        meeting_active=True,
        recorder_enabled=True,
        pipeline_running=True,
        meeting_title="Быстрый созвон",
        elapsed_text="00:01:23",
        background_message="Фоновая обработка выполняется.",
    )
    assert control.state_label.text() == "Созвон идет"
    assert "Быстрый созвон" in control.detail_label.text()
    assert control.timer_label.text() == "00:01:23"
    assert not control.timer_label.isHidden()
    assert "Фоновая обработка выполняется." in control.background_label.text()

    control.primary_button.click()
    assert control.state_label.text() == "Завершить созвон?"
    assert ended_meetings == []

    control.secondary_button.click()
    assert control.state_label.text() == "Созвон идет"

    control.primary_button.click()
    control.primary_button.click()
    assert ended_meetings == [True]

    control.close_from_app()
    app.processEvents()


def test_main_window_toggles_floating_control_and_closes_it_with_app(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.show()
    app.processEvents()

    assert window.floating_control.isVisible()
    assert window.toggle_floating_button.text() == "Скрыть плавающую кнопку"

    window.toggle_floating_control()
    app.processEvents()
    assert not window.floating_control.isVisible()
    assert window.toggle_floating_button.text() == "Показать плавающую кнопку"

    window.toggle_floating_control()
    app.processEvents()
    assert window.floating_control.isVisible()

    window.floating_control.close()
    app.processEvents()
    assert not window.floating_control.isVisible()
    assert window.isVisible()

    window.close()
    app.processEvents()
    assert not window.floating_control.isVisible()


def test_floating_control_uses_main_window_lifecycle(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    window.floating_control.primary_button.click()
    assert storage.workday_active
    assert window.floating_control.primary_button.text() == "Начать созвон"

    window.floating_control.primary_button.click()
    window.floating_control.title_input.setText("Созвон из кнопки")
    window.floating_control.primary_button.click()

    assert storage.meeting_active
    metadata = storage.read_meeting_metadata(storage.active_meeting_folder)
    assert metadata["title"] == "Созвон из кнопки"
    assert window.floating_control.state_label.text() == "Созвон идет"
    assert window.floating_control.timer_label.text() == window.active_call_timer_value.text()
    assert not window.floating_control.timer_label.isHidden()

    window.floating_control.primary_button.click()
    assert storage.meeting_active
    assert window.floating_control.state_label.text() == "Завершить созвон?"

    window.floating_control.primary_button.click()
    deadline = time.time() + 2
    while window.pipeline_running and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert not storage.meeting_active
    assert window.floating_control.primary_button.text() == "Начать созвон"

    window.close()
    app.processEvents()


def test_floating_control_pipeline_progress_updates_background_text(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.pipeline_running = True

    window._on_pipeline_progress("audio_running", "Тестовый pipeline выполняется.")

    assert "Тестовый pipeline выполняется." in window.floating_control.background_label.text()

    window.pipeline_running = False
    window.close()
    app.processEvents()


def test_main_window_shows_disabled_obs_status_and_local_workflow(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.config["summary"]["enabled"] = False
    window.config["transcription"]["backend"] = "whisper_cli"

    assert window.obs_status_value.text() == "OBS: выключен в настройках"

    window.check_obs()
    assert window.status_label.text() == "OBS: выключен в настройках"

    with patch("app.services.readiness.shutil.which", return_value="/bin/tool"):
        window.check_readiness()
    assert "Генерация итогов выключена" in window.readiness_labels["Summary"].text()
    assert "API key не требуется" in window.readiness_labels["API key"].text()
    assert window.pipeline_labels == {}

    window.start_workday()
    assert window.start_meeting_button.isEnabled()

    window.close()
    app.processEvents()


def test_main_window_has_light_navigation_shell(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    assert window.nav_buttons[0].text() == "Рабочий день"
    assert window.nav_buttons[1].text() == "Ревью"
    assert window.nav_buttons[2].text() == "Архив"
    assert window.nav_buttons[3].text() == "Настройки"
    assert window.nav_buttons[4].text() == "Справка"
    assert window.nav_buttons[0].isChecked()

    window.nav_buttons[3].click()

    assert window.pages.currentIndex() == 3
    assert window.nav_buttons[3].isChecked()

    window.close()
    app.processEvents()


def test_workday_screen_shows_active_call_and_meetings_summary(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    assert window.active_call_title_value.text() == "Нет активного созвона"
    assert "Папка дня еще не создана" in window.today_meetings_value.text()

    window.start_workday()

    window._start_meeting_with_title("Планерка")

    assert "Планерка" in window.active_call_title_value.text()
    assert "Создано встреч за день: 1" in window.today_meetings_value.text()
    assert window.selected_workday_meeting_folder is None
    assert storage.active_meeting_folder in window.workday_meeting_cards

    window.workday_meeting_cards[storage.active_meeting_folder].clicked.emit()

    assert window.selected_workday_meeting_folder == storage.active_meeting_folder
    assert "Пропущено" in window.pipeline_labels["recording"].text()

    window.close()
    app.processEvents()


def test_workday_screen_uses_prototype_card_controls(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    assert isinstance(window.pages.widget(0), QScrollArea)
    assert window.pages.widget(0).widgetResizable()
    assert set(window.readiness_badges) == {
        "OBS",
        "FFmpeg",
        "Whisper",
        "Summary",
        "API key",
        "Summary endpoint",
    }
    assert window.readiness_badges["OBS"].text() == "Не проверено"
    assert window.readiness_tiles["OBS"].minimumHeight() >= 82
    assert window.readiness_tiles["OBS"].minimumWidth() >= 300
    assert window.readiness_labels["OBS"].wordWrap()
    assert window.check_readiness_button.text() == "Проверить готовность"
    assert window.check_readiness_button.objectName() == "headerPrimaryButton"
    assert window.check_readiness_button.height() <= 34
    assert window.toggle_readiness_button.text() == "Свернуть"
    assert window.toggle_readiness_button.objectName() == "headerButton"
    assert window.toggle_readiness_button.height() <= 34
    assert window.readiness_card.height() == window.READINESS_CARD_EXPANDED_HEIGHT
    assert window.readiness_body.height() == window.READINESS_GRID_HEIGHT
    assert not window.readiness_body.isHidden()
    assert window.toggle_meetings_button.text() == "Свернуть"
    assert not window.meetings_body.isHidden()

    window.toggle_readiness_button.click()
    window.toggle_meetings_button.click()

    assert window.readiness_body.isHidden()
    assert window.readiness_card.height() == window.READINESS_CARD_COLLAPSED_HEIGHT
    assert window.toggle_readiness_button.text() == "Развернуть"
    assert window.meetings_body.isHidden()
    assert window.toggle_meetings_button.text() == "Развернуть"
    assert window.day_status_card.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
    assert window.active_call_card.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
    assert window.day_status_card.minimumHeight() == window.DAY_OVERVIEW_CARD_MIN_HEIGHT
    assert window.active_call_card.minimumHeight() == window.DAY_OVERVIEW_CARD_MIN_HEIGHT
    assert window.day_status_panel.objectName() == "overviewInnerPanel"
    assert window.active_call_panel.objectName() == "overviewInnerPanel"
    assert window.day_status_badge.text() == "Не активен"
    assert window.day_folder_badge.text() == "Папка не создана"
    assert window.day_status_open_folder_button.text() == "Открыть папку дня"
    assert window.day_status_open_folder_button.isHidden()
    assert window.active_call_badge.parent() is not window.active_call_panel
    assert window.start_workday_button is window.end_workday_button
    assert window.workday_action_button.text() == "Начать рабочий день"
    assert window.workday_action_button.objectName() == "primaryButton"
    assert window.start_meeting_button.isHidden()
    assert window.end_meeting_button.objectName() == "dangerButton"

    window.start_workday()

    assert window.workday_action_button.text() == "Завершить рабочий день"
    assert window.workday_action_button.objectName() == "dangerButton"
    assert window.day_status_badge.text() == "Активен"
    assert window.day_folder_badge.text() == "Папка создана"
    assert not window.day_status_open_folder_button.isHidden()
    assert not window.start_meeting_button.isHidden()

    window.start_meeting()

    assert not window.start_meeting_overlay.isHidden()
    window.start_meeting_overlay._cancel()
    assert window.start_meeting_overlay.isHidden()
    assert not storage.meeting_active

    window._start_meeting_with_title("Карточка созвона")

    assert window.active_call_panel.objectName() == "activeCallInnerPanel"
    assert window.start_meeting_button.isHidden()
    assert not window.end_meeting_button.isHidden()

    window.close()
    app.processEvents()


def test_pipeline_steps_are_rendered_as_prototype_cards(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    window.start_workday()
    window._start_meeting_with_title("Pipeline")

    window.workday_meeting_cards[storage.active_meeting_folder].clicked.emit()

    assert set(window.pipeline_step_titles) == {
        "recording",
        "audio",
        "transcription",
        "summary",
    }
    assert window.pipeline_step_titles["recording"].text() == "OBS запись"
    assert window.pipeline_step_titles["audio"].text() == "Аудио"
    assert window.pipeline_step_titles["summary"].text() == "Итоги"
    assert window.pipeline_labels["recording"].objectName() == "statusBadge"
    assert window.pipeline_messages["recording"].objectName() == "pipelineMessage"

    window._set_pipeline_step("audio", "Выполняется", "Тестовая обработка audio.wav.", "active")

    assert window.pipeline_labels["audio"].text() == "Выполняется"
    assert window.pipeline_messages["audio"].text() == "Тестовая обработка audio.wav."
    assert window.pipeline_messages["audio"].wordWrap()

    window.close()
    app.processEvents()


def test_workday_meeting_card_contains_folder_actions_after_click(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    window.start_workday()
    window._start_meeting_with_title("Карточка")
    meeting_folder = storage.active_meeting_folder

    assert meeting_folder in window.workday_meeting_cards
    assert window.open_day_folder_button.isHidden()

    window.workday_meeting_cards[meeting_folder].clicked.emit()

    meeting_card = window.workday_meeting_cards[meeting_folder]
    meeting_buttons = {
        button.text()
        for button in meeting_card.findChildren(type(window.workday_action_button))
    }
    assert "Открыть папку встречи" in meeting_buttons
    assert "Открыть папку дня" in meeting_buttons

    window.workday_meeting_cards[meeting_folder].clicked.emit()

    assert window.selected_workday_meeting_folder is None
    assert window.pipeline_labels == {}

    window.close()
    app.processEvents()


def test_workday_meetings_are_shown_newest_first(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    storage.create_day_folder()
    first = storage.create_meeting_folder(
        "Первая",
        started_at=datetime.combine(datetime.now().date(), datetime.min.time()).replace(hour=10),
        metadata={"status": "ended"},
    )
    second = storage.create_meeting_folder(
        "Вторая",
        started_at=datetime.combine(datetime.now().date(), datetime.min.time()).replace(hour=11),
        metadata={"status": "ended"},
    )
    window = MainWindow(storage, recorder)

    assert list(window.workday_meeting_cards) == [second, first]

    window.close()
    app.processEvents()


def test_review_screen_uses_meeting_summary_transcript_and_day_summary_card(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder()
    meeting_folder = storage.create_meeting_folder(
        "Ревью",
        metadata={
            "status": "ended",
            "recording_status": "disabled",
            "audio_status": "skipped",
            "transcription_status": "completed",
            "summary_status": "draft_created",
        },
    )
    storage.save_meeting_summary_draft(meeting_folder, "# Итоги встречи\n")
    (meeting_folder / "transcript.md").write_text("# Транскрипт встречи\n", encoding="utf-8")

    window = MainWindow(storage, recorder)
    window.open_review()

    assert window.review_tabs.count() == 2
    assert window.review_tabs.tabText(0) == "Итоги встречи"
    assert window.review_tabs.tabText(1) == "Транскрипт"
    assert not hasattr(window, "tasks_editor")
    assert window.selected_review_meeting_folder == meeting_folder
    assert meeting_folder in window.review_meeting_cards
    assert window.meeting_summary_editor.toPlainText() == "# Итоги встречи\n"
    assert window.meeting_transcript_editor.isReadOnly()
    assert window.meeting_transcript_editor.toPlainText() == "# Транскрипт встречи\n"
    assert not (day_folder / "00_day_summary_draft.md").exists()

    storage.save_day_summary_draft(day_folder, "# Итоги дня\n")
    storage.ensure_day_summary_metadata(day_folder)
    window.selected_review_meeting_folder = None
    window.refresh_review()

    assert window.review_day_summary_selected
    assert window.review_tabs.tabText(0) == "Итоги встреч"
    assert window.review_tabs.tabText(1) == "Транскрипт"
    assert window.meeting_summary_editor.toPlainText() == "# Итоги дня\n"
    assert "Ревью" in window.meeting_transcript_editor.toPlainText()
    assert "Открыть транскрипт внутри приложения" in window.meeting_transcript_editor.toPlainText()
    assert window.save_final_files_button.isEnabled()

    window.close()
    app.processEvents()


def test_review_meeting_card_click_selects_whole_card(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    storage.create_day_folder()
    first = storage.create_meeting_folder(
        "Первая",
        started_at=datetime.combine(datetime.now().date(), datetime.min.time()).replace(hour=10),
        metadata={"status": "ended"},
    )
    second = storage.create_meeting_folder(
        "Вторая",
        started_at=datetime.combine(datetime.now().date(), datetime.min.time()).replace(hour=11),
        metadata={"status": "ended"},
    )
    window = MainWindow(storage, recorder)
    window.open_review()

    assert window.selected_review_meeting_folder == second
    assert list(window.review_meeting_cards) == [second, first]

    window.review_meeting_cards[first].clicked.emit()

    assert window.selected_review_meeting_folder == first

    window.close()
    app.processEvents()


def test_settings_screen_saves_local_config_yaml(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)

    window.settings_storage_root_input.setText("MeetingSummariesCustom")
    window.settings_obs_enabled_checkbox.setChecked(True)
    window.settings_obs_host_input.setText("127.0.0.1")
    window.settings_obs_port_input.setValue(4456)
    window.settings_obs_password_input.setText("secret")
    window.settings_secrets_env_file_input.setText("C:/safe/.env.local")
    window.settings_transcription_backend_select.setCurrentText("aitunnel")
    window._set_combo_value(window.settings_transcription_model_select, "whisper-large-v3")
    window.settings_transcription_timeout_input.setValue(240)
    window.settings_transcription_upload_limit_input.setValue(20)
    window.settings_transcription_backend_select.setCurrentText("faster_whisper")
    window._set_combo_value(window.settings_transcription_model_select, "medium")
    window._set_combo_value(window.settings_transcription_device_select, "cuda")
    window.settings_transcription_vad_checkbox.setChecked(False)
    window.settings_transcription_backend_select.setCurrentText("aitunnel")
    window.settings_summary_enabled_checkbox.setChecked(True)
    window._set_combo_value(window.settings_summary_model_select, "gpt-5.4-nano")
    window.settings_theme_select.setCurrentIndex(window.settings_theme_select.findData("dark"))
    window.settings_floating_theme_select.setCurrentIndex(
        window.settings_floating_theme_select.findData("dark")
    )

    window.save_settings()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["storage"]["root"] == "MeetingSummariesCustom"
    assert config["obs"]["enabled"] is True
    assert config["obs"]["websocket_host"] == "127.0.0.1"
    assert config["obs"]["websocket_port"] == 4456
    assert config["obs"]["websocket_password"] == "secret"
    assert config["secrets"]["env_file"] == "C:/safe/.env.local"
    assert config["transcription"]["backend"] == "aitunnel"
    assert config["transcription"]["backends"]["aitunnel"]["model"] == "whisper-large-v3"
    assert config["transcription"]["backends"]["aitunnel"]["timeout_seconds"] == 240
    assert config["transcription"]["backends"]["aitunnel"]["max_upload_mb"] == 20
    assert config["transcription"]["backends"]["aitunnel"]["api_key_env"] == "AITUNNEL_KEY"
    assert (
        config["transcription"]["backends"]["aitunnel"]["base_url"]
        == "https://api.aitunnel.ru/v1/"
    )
    assert config["transcription"]["backends"]["aitunnel"]["env_file"] == ""
    assert config["transcription"]["backends"]["faster_whisper"]["model"] == "medium"
    assert config["transcription"]["backends"]["faster_whisper"]["device"] == "cuda"
    assert config["transcription"]["backends"]["faster_whisper"]["compute_type"] == "float16"
    assert config["transcription"]["backends"]["faster_whisper"]["vad_filter"] is False
    assert config["transcription"]["backends"]["whisper_cli"]["model"] == "base"
    assert config["summary"]["enabled"] is True
    assert config["summary"]["model"] == "gpt-5.4-nano"
    assert config["summary"]["api_key_env"] == "AITUNNEL_KEY"
    assert config["summary"]["base_url"] == "https://api.aitunnel.ru/v1/"
    assert config["summary"]["env_file"] == ""
    assert config["ui"]["theme"] == "dark"
    assert config["ui"]["floating_theme"] == "dark"
    assert window.config["ui"]["theme"] == "dark"
    assert window.config["ui"]["floating_theme"] == "dark"
    assert "#0f172a" in window.styleSheet()
    assert "#111827" in window.floating_control.styleSheet()
    assert "Настройки сохранены" in window.settings_status_label.text()
    assert "следующие встречи" in window.settings_status_label.text().lower()
    assert isinstance(window.storage.transcriber, AITunnelTranscriber)

    window.close()
    app.processEvents()


def test_settings_screen_switches_transcription_fields_by_backend(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)

    window.settings_transcription_backend_select.setCurrentText("whisper_cli")
    app.processEvents()

    assert not window.settings_transcription_model_select.isHidden()
    assert window.settings_transcription_device_select.isHidden()
    assert window.settings_transcription_vad_checkbox.isHidden()
    assert window.settings_transcription_timeout_input.isHidden()
    assert window.settings_transcription_upload_limit_input.isHidden()

    window.settings_transcription_backend_select.setCurrentText("faster_whisper")
    app.processEvents()

    assert not window.settings_transcription_model_select.isHidden()
    assert not window.settings_transcription_device_select.isHidden()
    assert not window.settings_transcription_vad_checkbox.isHidden()
    assert window.settings_transcription_timeout_input.isHidden()

    window.settings_transcription_backend_select.setCurrentText("aitunnel")
    app.processEvents()

    assert not window.settings_transcription_model_select.isHidden()
    assert window.settings_transcription_device_select.isHidden()
    assert window.settings_transcription_vad_checkbox.isHidden()
    assert not window.settings_transcription_timeout_input.isHidden()
    assert not window.settings_transcription_upload_limit_input.isHidden()

    window.close()
    app.processEvents()


def test_settings_screen_keeps_separate_transcription_backend_profiles(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)

    window.settings_transcription_backend_select.setCurrentText("aitunnel")
    window._set_combo_value(window.settings_transcription_model_select, "whisper-1")
    window.settings_transcription_timeout_input.setValue(180)

    window.settings_transcription_backend_select.setCurrentText("whisper_cli")
    app.processEvents()
    assert window._combo_value(window.settings_transcription_model_select) == "base"
    window._set_combo_value(window.settings_transcription_model_select, "small")

    window.settings_transcription_backend_select.setCurrentText("faster_whisper")
    app.processEvents()
    assert window._combo_value(window.settings_transcription_model_select) == "base"
    window._set_combo_value(window.settings_transcription_model_select, "medium")

    window.settings_transcription_backend_select.setCurrentText("aitunnel")
    app.processEvents()
    assert window._combo_value(window.settings_transcription_model_select) == "whisper-1"
    assert window.settings_transcription_timeout_input.value() == 180

    config = window._settings_config_from_ui()
    assert config["transcription"]["backends"]["aitunnel"]["model"] == "whisper-1"
    assert config["transcription"]["backends"]["whisper_cli"]["model"] == "small"
    assert config["transcription"]["backends"]["faster_whisper"]["model"] == "medium"

    window.close()
    app.processEvents()


def test_settings_save_defers_transcription_runtime_change_while_processing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)

    window.pipeline_running = True
    window.settings_transcription_backend_select.setCurrentText("aitunnel")

    window.save_settings()

    assert isinstance(window.storage.transcriber, LocalWhisperTranscriber)
    assert "Текущая обработка завершится со старой конфигурацией" in (
        window.settings_status_label.text()
    )

    window.close()
    app.processEvents()


def test_settings_screen_uses_simplified_aitunnel_summary_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)

    window.settings_summary_enabled_checkbox.setChecked(True)
    assert not hasattr(window, "settings_summary_api_key_env_input")
    assert not hasattr(window, "settings_summary_base_url_input")
    assert not hasattr(window, "settings_summary_env_file_input")
    assert window.settings_summary_model_select.currentData() == "gpt-5.4-mini"
    assert "144 ₽/1M вход" in window.settings_summary_model_select.itemText(
        window.settings_summary_model_select.currentIndex()
    )

    window._set_combo_value(window.settings_summary_model_select, "gpt-5.4-nano")

    window.save_settings()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["summary"]["model"] == "gpt-5.4-nano"
    assert config["summary"]["api_key_env"] == "AITUNNEL_KEY"
    assert config["summary"]["base_url"] == "https://api.aitunnel.ru/v1/"
    assert config["summary"]["env_file"] == ""

    window.close()
    app.processEvents()


def test_settings_screen_supports_custom_aitunnel_summary_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)

    assert window.settings_summary_custom_model_input.isHidden()

    window._set_combo_value(window.settings_summary_model_select, "__custom__")
    app.processEvents()
    assert not window.settings_summary_custom_model_input.isHidden()
    window.settings_summary_custom_model_input.setText("deepseek-r1")

    window.save_settings()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["summary"]["model"] == "deepseek-r1"
    assert config["summary"]["api_key_env"] == "AITUNNEL_KEY"
    assert config["summary"]["base_url"] == "https://api.aitunnel.ru/v1/"

    window.close()
    app.processEvents()


def test_dark_theme_styles_scroll_page_surfaces_and_form_labels(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.config["ui"]["theme"] = "dark"

    window._apply_theme_settings()

    workday_scroll = window.pages.widget(0)
    settings_scroll = window.pages.widget(3)
    assert isinstance(workday_scroll, QScrollArea)
    assert isinstance(settings_scroll, QScrollArea)
    assert workday_scroll.widget().objectName() == "pageSurface"
    assert settings_scroll.widget().objectName() == "pageSurface"
    assert workday_scroll.viewport().objectName() == "scrollViewport"
    assert settings_scroll.viewport().objectName() == "scrollViewport"
    assert "QWidget#pageSurface" in window.styleSheet()
    assert "QWidget#scrollViewport" in window.styleSheet()
    assert "QLabel {" in window.styleSheet()

    window.close()
    app.processEvents()


def test_archive_and_help_pages_explain_placeholders_and_local_flow(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    window.nav_buttons[2].click()
    archive_text = "\n".join(label.text() for label in window.pages.widget(2).findChildren(QLabel))
    assert "Архив пока не реализован" in archive_text
    assert "read-only" in archive_text

    window.nav_buttons[4].click()
    help_text = "\n".join(label.text() for label in window.pages.widget(4).findChildren(QLabel))
    assert "Основной сценарий" in help_text
    assert "Local-first" in help_text
    assert "Аудио и видео остаются локально" in help_text

    window.close()
    app.processEvents()


def test_end_meeting_starts_background_processing_and_allows_next_meeting(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()

    class SlowStorage(StorageService):
        def __init__(self, root: Path) -> None:
            super().__init__(root, recorder)
            self.entered = Event()
            self.release = Event()
            self.day_folder = self.create_day_folder()
            self.active_day_folder = self.day_folder
            self.meeting_folder = self.day_folder / "12-00_Test"
            self.meeting_folder.mkdir()
            self.write_metadata(
                self.meeting_folder,
                {
                    "title": "Test",
                    "started_at": datetime.now().isoformat(),
                    "status": "active",
                    "recording_status": "disabled",
                },
            )
            self.active_meeting_folder = self.meeting_folder

        def load_today_state(self, now=None) -> None:
            del now

        def finish_active_meeting_recording(self, ended_at=None, progress_callback=None):
            del ended_at
            if progress_callback is not None:
                progress_callback("recording_skipped", "OBS запись не активна.")
            self.write_metadata(
                self.meeting_folder,
                {
                    "title": "Test",
                    "started_at": datetime.now().isoformat(),
                    "status": "ended",
                    "recording_status": "disabled",
                    "processing_status": "pending",
                },
            )
            self.active_meeting_folder = None
            return self.meeting_folder

        def process_meeting_pipeline(self, meeting_folder, progress_callback=None):
            assert meeting_folder == self.meeting_folder
            if progress_callback is not None:
                progress_callback("audio_running", "Тестовый pipeline выполняется.")
            self.entered.set()
            self.release.wait(5)
            self.write_metadata(
                self.meeting_folder,
                {
                    "title": "Test",
                    "started_at": datetime.now().isoformat(),
                    "status": "ended",
                    "recording_status": "disabled",
                    "audio_status": "skipped",
                    "transcription_status": "skipped",
                    "summary_status": "disabled",
                    "processing_status": "completed",
                },
            )
            return self.meeting_folder

    storage = SlowStorage(tmp_path)
    window = MainWindow(storage, recorder)

    window.end_meeting()
    deadline = time.time() + 2
    while not storage.entered.is_set() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert storage.entered.is_set()
    assert window.pipeline_running
    assert not storage.meeting_active
    assert window.start_meeting_button.isEnabled()
    assert window.end_workday_button.isEnabled()

    window._start_meeting_with_title("Second")

    assert storage.meeting_active
    second_metadata = storage.read_meeting_metadata(storage.active_meeting_folder)
    assert second_metadata["title"] == "Second"
    assert window.end_meeting_button.isEnabled()

    storage.release.set()
    deadline = time.time() + 2
    while window.pipeline_running and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert not window.pipeline_running
    window.close()
    app.processEvents()


def test_late_pipeline_progress_uses_saved_meeting_folder(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder()
    meeting_folder = day_folder / "12-00_Test"
    meeting_folder.mkdir()
    storage.write_metadata(
        meeting_folder,
        {
            "title": "Test",
            "started_at": datetime.now().isoformat(),
            "status": "ended",
            "recording_status": "disabled",
            "audio_status": "extracted",
            "transcription_status": "completed",
            "summary_status": "completed",
        },
    )
    storage.active_day_folder = day_folder
    storage.active_meeting_folder = meeting_folder
    window = MainWindow(storage, recorder)
    window.pipeline_meeting_folder = meeting_folder
    window.selected_workday_meeting_folder = meeting_folder
    window._refresh_workday_meetings()

    storage.active_meeting_folder = None
    window._on_pipeline_progress("audio_done", "Поздний сигнал audio_done.")

    assert window.pipeline_labels["audio"].text() == "Готово"
    assert window.pipeline_messages["audio"].text() == "Поздний сигнал audio_done."

    window.close()
    app.processEvents()
