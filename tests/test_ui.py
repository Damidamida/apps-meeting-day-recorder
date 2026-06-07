import os
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import patch

import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea, QSizePolicy

from app.services.recorder import NoopRecorder
from app.services.storage import MetadataReadError, StorageService
from app.services.summarization import OpenAISummarizer
from app.services.transcription import AITunnelTranscriber, LocalWhisperTranscriber
from app.ui import main_window as main_window_module
from app.ui.main_window import FloatingMeetingControl, MainWindow, StartMeetingOverlay


class CloseEventStub:
    def __init__(self) -> None:
        self.ignored = False

    def ignore(self) -> None:
        self.ignored = True


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

    assert window.obs_status_value.text() == "OBS: тестовый режим без записи"

    window.check_obs()
    assert window.status_label.text() == "OBS: тестовый режим без записи"

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
    assert "Без записи" in window.pipeline_labels["recording"].text()

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


def test_workday_blocks_explain_start_and_end_restrictions(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    assert "сначала начните рабочий день" in window.active_call_detail_value.text().lower()
    assert "Начните рабочий день" in window.day_status_detail_value.text()
    assert window.workday_action_button.text() == "Начать рабочий день"

    window.start_workday()

    assert window.workday_action_button.text() == "Завершить рабочий день"
    assert window.workday_action_button.isEnabled()
    assert "После завершения будут подготовлены итоги дня" in window.day_status_detail_value.text()
    assert "можно начать новую встречу" in window.active_call_detail_value.text().lower()

    window.pipeline_running = True
    window.refresh_status()
    window.refresh_buttons()

    assert window.workday_action_button.isEnabled()
    assert "Итоги дня начнутся после завершения обработки встреч" in window.day_status_detail_value.text()

    window.pipeline_running = False
    window._start_meeting_with_title("Ограничение")

    assert not window.workday_action_button.isEnabled()
    assert "Нельзя завершить рабочий день" in window.day_status_detail_value.text()
    assert "Сначала завершите встречу" in window.day_status_detail_value.text()

    window.close()
    app.processEvents()


def test_close_event_warns_and_blocks_active_meeting(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    storage.start_workday()
    storage.start_meeting("Активный созвон")
    window = MainWindow(storage, recorder)
    event = CloseEventStub()

    window.closeEvent(event)

    assert event.ignored
    assert not window.safety_close_overlay.isHidden()
    assert window.safety_close_overlay.title_label.text() == "Идет активный созвон"
    assert "Сначала завершите встречу" in window.safety_close_overlay.message_label.text()
    assert window.safety_close_overlay.secondary_button.isHidden()

    window.safety_close_overlay.primary_button.click()
    assert window.safety_close_overlay.isHidden()

    window.close()
    app.processEvents()


def test_close_event_allows_confirmed_close_with_background_processing(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.pipeline_running = True
    event = CloseEventStub()
    close_requests: list[bool] = []
    original_close = window.close
    monkeypatch.setattr(window, "close", lambda: close_requests.append(True))

    window.closeEvent(event)

    assert event.ignored
    assert not window.safety_close_overlay.isHidden()
    assert window.safety_close_overlay.title_label.text() == "Идет обработка встречи"
    assert "При следующем запуске" in window.safety_close_overlay.message_label.text()
    assert not window.safety_close_overlay.secondary_button.isHidden()

    window.safety_close_overlay.secondary_button.click()

    assert window.allow_close_with_processing
    assert close_requests == [True]

    window.pipeline_running = False
    original_close()
    app.processEvents()


def test_close_event_warns_for_day_summary_processing(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.day_summary_running = True
    event = CloseEventStub()
    close_requests: list[bool] = []
    original_close = window.close
    monkeypatch.setattr(window, "close", lambda: close_requests.append(True))

    window.closeEvent(event)

    assert event.ignored
    assert not window.safety_close_overlay.isHidden()
    assert window.safety_close_overlay.title_label.text() == "Идет обновление итогов дня"
    assert "восстановить обновление итогов дня" in window.safety_close_overlay.message_label.text()
    assert not window.safety_close_overlay.secondary_button.isHidden()

    window.safety_close_overlay.secondary_button.click()

    assert window.allow_close_with_processing
    assert close_requests == [True]

    window.day_summary_running = False
    original_close()
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


def test_meeting_badge_uses_result_status_not_ended_state(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    assert window._meeting_badge(
        {
            "status": "ended",
            "processing_status": "completed",
            "summary_status": "draft_created",
        }
    ) == ("Итоги готовы", "ok")
    assert window._meeting_badge(
        {
            "status": "ended",
            "processing_status": "completed",
            "summary_status": "disabled",
        }
    ) == ("Итоги выключены", "skip")
    assert window._meeting_badge(
        {
            "status": "ended",
            "processing_status": "completed",
            "recording_status": "disabled",
            "audio_status": "skipped",
            "transcription_status": "skipped",
            "summary_status": "disabled",
        }
    ) == ("Без записи", "error")
    assert window._meeting_badge(
        {
            "status": "ended",
            "processing_status": "completed",
            "recording_status": "stopped",
            "recording_path": str(tmp_path / "recording.mkv"),
            "audio_status": "extracted",
            "transcription_status": "failed",
            "summary_status": "skipped",
        }
    ) == ("Требует внимания", "error")
    assert window._meeting_badge(
        {
            "status": "ended",
            "processing_status": "failed",
            "processing_error": "FFmpeg crashed",
        }
    ) == ("Требует внимания", "error")

    window.close()
    app.processEvents()


def test_reprocess_button_is_hidden_for_unsafe_meeting_states(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    storage.create_day_folder()
    window = MainWindow(storage, recorder)
    recording_path = tmp_path / "recording.mkv"
    recording_path.write_bytes(b"fake recording")
    cases = [
        {
            "title": "Активная",
            "metadata": {
                "status": "active",
                "recording_status": "recording",
            },
        },
        {
            "title": "Без записи",
            "metadata": {
                "status": "ended",
                "processing_status": "completed",
                "recording_status": "stop_failed",
                "audio_status": "skipped",
                "transcription_status": "skipped",
                "summary_status": "skipped",
            },
        },
        {
            "title": "Итоги выключены",
            "metadata": {
                "status": "ended",
                "processing_status": "completed",
                "recording_status": "stopped",
                "recording_path": str(recording_path),
                "audio_status": "extracted",
                "transcription_status": "completed",
                "summary_status": "disabled",
            },
        },
        {
            "title": "В очереди",
            "metadata": {
                "status": "ended",
                "processing_status": "pending",
                "recording_status": "stopped",
                "recording_path": str(recording_path),
                "audio_status": "extracted",
                "transcription_status": "completed",
                "summary_status": "draft_created",
            },
        },
        {
            "title": "Обрабатывается",
            "metadata": {
                "status": "ended",
                "processing_status": "running",
                "recording_status": "stopped",
                "recording_path": str(recording_path),
                "audio_status": "extracted",
                "transcription_status": "completed",
                "summary_status": "draft_created",
            },
        },
        {
            "title": "Итоги готовы без файла записи",
            "metadata": {
                "status": "ended",
                "processing_status": "completed",
                "recording_status": "stopped",
                "recording_path": str(tmp_path / "missing-recording.mkv"),
                "audio_status": "extracted",
                "transcription_status": "completed",
                "summary_status": "draft_created",
            },
        },
    ]

    for index, case in enumerate(cases, start=9):
        meeting_folder = storage.create_meeting_folder(
            case["title"],
            started_at=datetime.combine(
                datetime.now().date(),
                datetime.min.time(),
            ).replace(hour=index),
            metadata=case["metadata"],
        )
        card = window._create_meeting_card(meeting_folder, expanded=True)
        button_texts = {button.text() for button in card.findChildren(QPushButton)}

        assert "Повторить обработку" not in button_texts

    window.close()
    app.processEvents()


def test_reprocess_button_is_visible_for_attention_and_ready_results_with_recording(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    storage.create_day_folder()
    window = MainWindow(storage, recorder)
    recording_path = tmp_path / "recording.mkv"
    recording_path.write_bytes(b"fake recording")
    cases = [
        {
            "title": "Ошибка транскрипции",
            "metadata": {
                "status": "ended",
                "processing_status": "completed",
                "recording_status": "stopped",
                "recording_path": str(recording_path),
                "audio_status": "extracted",
                "transcription_status": "failed",
                "summary_status": "skipped",
            },
        },
        {
            "title": "Итоги готовы",
            "metadata": {
                "status": "ended",
                "processing_status": "completed",
                "recording_status": "stopped",
                "recording_path": str(recording_path),
                "audio_status": "extracted",
                "transcription_status": "completed",
                "summary_status": "draft_created",
            },
        },
    ]

    for index, case in enumerate(cases, start=14):
        meeting_folder = storage.create_meeting_folder(
            case["title"],
            started_at=datetime.combine(
                datetime.now().date(),
                datetime.min.time(),
            ).replace(hour=index),
            metadata=case["metadata"],
        )
        card = window._create_meeting_card(meeting_folder, expanded=True)
        buttons = {
            button.text(): button for button in card.findChildren(QPushButton)
        }

        assert "Повторить обработку" in buttons
        assert buttons["Повторить обработку"].isEnabled()

    window.close()
    app.processEvents()


def test_pipeline_wait_messages_do_not_claim_ready_audio_is_missing(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    metadata = {
        "status": "ended",
        "processing_status": "pending",
        "recording_status": "stopped",
        "audio_status": "extracted",
    }

    assert window._step_message("transcription", metadata) == (
        "Ждет обработки встречи."
    )
    assert window._step_message("summary", metadata) == "Ждет transcript."

    window.close()
    app.processEvents()


def test_pending_today_meetings_are_restored_to_processing_queue_on_startup(
    tmp_path: Path,
) -> None:
    """
    Verifies that meetings with pending processing are enqueued and started by the UI on application startup.
    
    Sets up a StorageService subclass that blocks when processing a meeting, creates a workday and a meeting whose recording has finished (so processing status becomes pending), then constructs MainWindow and waits for the storage's processing entry to be invoked. Asserts that the window's pipeline_meeting_folder points to the pending meeting and that pipeline_running is true, then cleans up the blocked processing thread.
    """
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    entered = Event()
    release = Event()

    class BlockingStorage(StorageService):
        def process_meeting_pipeline(self, meeting_folder, progress_callback=None):
            del progress_callback
            entered.set()
            release.wait(5)
            return meeting_folder

    storage = BlockingStorage(tmp_path, recorder)
    storage.start_workday(datetime.now())
    pending_meeting = storage.start_meeting("Восстановить", datetime.now())
    storage.finish_active_meeting_recording(datetime.now())

    window = MainWindow(storage, recorder)
    deadline = time.time() + 2
    while not entered.is_set() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert entered.is_set()
    assert window.pipeline_meeting_folder == pending_meeting
    assert window.pipeline_running

    release.set()
    if window.pipeline_thread is not None:
        window.pipeline_thread.quit()
        window.pipeline_thread.wait(1000)
    window.close()
    app.processEvents()


def test_running_today_meetings_are_recovered_to_processing_queue_on_startup(
    tmp_path: Path,
) -> None:
    """
    Checks that a meeting with processing_status "running" is requeued and marked recovered when the application starts.
    
    Sets up a storage backend that blocks pipeline processing, creates a meeting whose metadata is marked as `running`, starts the main application window, and waits for the pipeline to begin. Asserts that the window selects the meeting for processing, `pipeline_running` becomes true, and the meeting metadata is updated with `processing_recovery_status == "recovered"` and the expected recovery reason. Cleans up the pipeline thread and closes the window.
    """
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    entered = Event()
    release = Event()

    class BlockingStorage(StorageService):
        def process_meeting_pipeline(self, meeting_folder, progress_callback=None):
            """
            Simulates processing of a meeting pipeline for tests by signaling entry and blocking until released.
            
            This test helper sets the `entered` Event to indicate the pipeline started, waits up to 5 seconds on the `release` Event, ignores the `progress_callback` argument, and then returns the provided meeting folder. It is intended for use in tests that need to observe pipeline start and control when processing continues.
            
            Parameters:
                meeting_folder: Path-like or identifier of the meeting folder to process; returned unchanged.
                progress_callback: Ignored. Present to match the production API.
            
            Returns:
                The same `meeting_folder` argument passed in.
            """
            del progress_callback
            entered.set()
            release.wait(5)
            return meeting_folder

    storage = BlockingStorage(tmp_path, recorder)
    storage.start_workday(datetime.now())
    running_meeting = storage.start_meeting("Восстановить running", datetime.now())
    storage.finish_active_meeting_recording(datetime.now())
    metadata = storage.read_meeting_metadata(running_meeting)
    metadata["processing_status"] = "running"
    storage.write_metadata(running_meeting, metadata)
    storage._sync_day_meeting_metadata(running_meeting, metadata)

    window = MainWindow(storage, recorder)
    deadline = time.time() + 2
    while not entered.is_set() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    recovered_metadata = storage.read_meeting_metadata(running_meeting)
    assert entered.is_set()
    assert window.pipeline_meeting_folder == running_meeting
    assert window.pipeline_running
    assert recovered_metadata["processing_recovery_status"] == "recovered"
    assert recovered_metadata["processing_recovery_reason"] == (
        "Обработка была прервана при прошлом запуске приложения."
    )

    release.set()
    if window.pipeline_thread is not None:
        window.pipeline_thread.quit()
        window.pipeline_thread.wait(1000)
    window.close()
    app.processEvents()


def test_running_day_summary_is_restored_on_startup(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    entered = Event()
    release = Event()

    class BlockingStorage(StorageService):
        def process_day_summary_pipeline(self, day_folder, force=False, progress_callback=None):
            del force, progress_callback
            entered.set()
            release.wait(5)
            return day_folder

    storage = BlockingStorage(tmp_path, recorder)
    day_folder = storage.start_workday(datetime.now())
    storage.end_workday(datetime.now())
    metadata = storage.ensure_day_summary_metadata(day_folder)
    metadata["day_summary_status"] = "running"
    storage._write_json(storage.day_summary_metadata_path(day_folder), metadata)

    window = MainWindow(storage, recorder)
    deadline = time.time() + 2
    while not entered.is_set() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert entered.is_set()
    assert window.day_summary_running
    assert window.day_summary_day_folder == day_folder
    assert "Восстановлено обновление итогов дня" in window.status_label.text()

    release.set()
    if window.day_summary_thread is not None:
        window.day_summary_thread.quit()
        window.day_summary_thread.wait(1000)
    app.processEvents()
    window.close()
    app.processEvents()


def test_restore_queue_keeps_corrupted_metadata_backup_message(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    entered = Event()
    release = Event()

    class BlockingStorage(StorageService):
        broken_folder_name = "10-00_Broken"

        def read_meeting_metadata(self, meeting_folder):
            if meeting_folder.name == self.broken_folder_name:
                raise MetadataReadError(
                    meeting_folder / "meeting_metadata.json",
                    meeting_folder / "meeting_metadata.corrupt-test.json",
                )
            return super().read_meeting_metadata(meeting_folder)

        def process_meeting_pipeline(self, meeting_folder, progress_callback=None):
            del progress_callback
            entered.set()
            release.wait(5)
            return meeting_folder

    storage = BlockingStorage(tmp_path, recorder)
    day_folder = storage.start_workday(datetime.now())
    window = MainWindow(storage, recorder)
    pending_meeting = storage.start_meeting("Pending", datetime.now())
    storage.finish_active_meeting_recording(datetime.now())
    broken_meeting = day_folder / BlockingStorage.broken_folder_name
    broken_meeting.mkdir()
    (broken_meeting / "meeting_metadata.json").write_text('{"status": "ended"}', encoding="utf-8")

    window._restore_today_pending_processing_queue()
    deadline = time.time() + 2
    while not entered.is_set() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    status_text = window.status_label.text()
    pipeline_meeting_folder = window.pipeline_meeting_folder
    release.set()
    if window.pipeline_thread is not None:
        window.pipeline_thread.quit()
        window.pipeline_thread.wait(1000)
    window.close()
    app.processEvents()

    assert entered.is_set()
    assert pipeline_meeting_folder == pending_meeting
    assert "backup:" in status_text
    assert "meeting_metadata.corrupt-test.json" in status_text
    assert "Восстановлена" in status_text


def test_workday_meeting_card_hides_summary_action_until_summary_is_ready(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    storage.create_day_folder()
    meeting_folder = storage.create_meeting_folder(
        "Карточка",
        metadata={
            "status": "ended",
            "processing_status": "completed",
            "summary_status": "draft_created",
        },
    )

    meeting_card = window._create_meeting_card(meeting_folder, expanded=True)
    meeting_buttons = {
        button.text()
        for button in meeting_card.findChildren(type(window.workday_action_button))
    }

    assert "Открыть папку встречи" not in meeting_buttons
    assert "Открыть папку дня" not in meeting_buttons
    assert "Открыть итоги встречи" not in meeting_buttons

    window.close()
    app.processEvents()


def test_workday_meeting_card_opens_ready_summary_in_review(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    storage.create_day_folder()
    meeting_folder = storage.create_meeting_folder(
        "Карточка",
        metadata={
            "status": "ended",
            "processing_status": "completed",
            "summary_status": "draft_created",
        },
    )
    storage.save_meeting_summary_draft(meeting_folder, "# Итоги встречи\n\nГотовый итог.\n")

    meeting_card = window._create_meeting_card(meeting_folder, expanded=True)
    meeting_buttons = {
        button.text(): button
        for button in meeting_card.findChildren(type(window.workday_action_button))
    }

    assert "Открыть папку встречи" not in meeting_buttons
    assert "Открыть папку дня" not in meeting_buttons
    assert "Открыть итоги встречи" in meeting_buttons

    meeting_buttons["Открыть итоги встречи"].click()

    assert window.pages.currentIndex() == 1
    assert window.selected_review_meeting_folder == meeting_folder
    assert not window.review_day_summary_selected
    assert window.review_tabs.currentIndex() == 0
    assert window.meeting_summary_editor.toPlainText() == "# Итоги встречи\n\nГотовый итог.\n"

    window.close()
    app.processEvents()


def test_ready_meeting_summary_action_gets_primary_emphasis(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    recording_path = tmp_path / "recording.mkv"
    recording_path.write_bytes(b"fake recording")

    storage.create_day_folder()
    meeting_folder = storage.create_meeting_folder(
        "Готовые итоги",
        metadata={
            "status": "ended",
            "processing_status": "completed",
            "recording_status": "stopped",
            "recording_path": str(recording_path),
            "audio_status": "extracted",
            "transcription_status": "completed",
            "summary_status": "draft_created",
        },
    )
    storage.save_meeting_summary_draft(meeting_folder, "# Итоги встречи\n\nГотовый итог.\n")

    meeting_card = window._create_meeting_card(meeting_folder, expanded=True)
    buttons = {
        button.text(): button
        for button in meeting_card.findChildren(type(window.workday_action_button))
    }

    assert buttons["Открыть итоги встречи"].objectName() == "primaryButton"
    assert buttons["Повторить обработку"].objectName() != "primaryButton"

    window.close()
    app.processEvents()


def test_attention_meeting_reprocess_action_keeps_primary_emphasis(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    recording_path = tmp_path / "recording.mkv"
    recording_path.write_bytes(b"fake recording")

    storage.create_day_folder()
    meeting_folder = storage.create_meeting_folder(
        "Требует внимания",
        metadata={
            "status": "ended",
            "processing_status": "completed",
            "recording_status": "stopped",
            "recording_path": str(recording_path),
            "audio_status": "extracted",
            "transcription_status": "failed",
            "summary_status": "draft_created",
        },
    )
    storage.save_meeting_summary_draft(meeting_folder, "# Итоги встречи\n\nСтарый итог.\n")

    meeting_card = window._create_meeting_card(meeting_folder, expanded=True)
    buttons = {
        button.text(): button
        for button in meeting_card.findChildren(type(window.workday_action_button))
    }

    assert buttons["Повторить обработку"].objectName() == "primaryButton"
    assert buttons["Открыть итоги встречи"].objectName() != "primaryButton"

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

    custom_storage_root = tmp_path / "MeetingSummariesCustom"
    window.settings_storage_root_input.setText(str(custom_storage_root))
    assert not hasattr(window, "settings_obs_enabled_checkbox")
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
    assert config["storage"]["root"] == str(custom_storage_root)
    assert custom_storage_root.is_dir()
    assert window.storage.root == custom_storage_root
    assert "enabled" not in config["obs"]
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


def test_settings_screen_selects_storage_folder_with_windows_dialog(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)
    selected_folder = tmp_path / "selected-data"
    selected_folder.mkdir()

    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(selected_folder),
    )

    window.settings_storage_root_browse_button.click()

    assert window.settings_storage_root_input.text() == str(selected_folder)
    assert str(selected_folder) in window.settings_status_label.text()

    window.close()
    app.processEvents()


def test_settings_screen_rejects_storage_root_that_points_to_file(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)
    file_path = tmp_path / "not-a-folder"
    file_path.write_text("content", encoding="utf-8")

    window.settings_storage_root_input.setText(str(file_path))

    window.save_settings()

    assert not (tmp_path / "config.yaml").exists()
    assert "указывает на файл" in window.settings_status_label.text()

    window.close()
    app.processEvents()


def test_settings_screen_saves_expanded_storage_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)
    expected_root = tmp_path / "home" / "MeetingSummaries"

    window.settings_storage_root_input.setText("~/MeetingSummaries")

    window.save_settings()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["storage"]["root"] == str(expected_root)
    assert expected_root.is_dir()
    assert window.storage.root == expected_root

    window.close()
    app.processEvents()


def test_settings_screen_saves_storage_root_but_keeps_active_day_root(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    started_at = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    storage.start_workday(started_at=started_at)
    old_root = storage.root
    window = MainWindow(storage, recorder)
    new_root = tmp_path / "new-data"

    window.settings_storage_root_input.setText(str(new_root))

    window.save_settings()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["storage"]["root"] == str(new_root)
    assert new_root.is_dir()
    assert window.storage.root == old_root
    window._request_day_summary_update = lambda day_folder, force=False: None
    window.end_workday()

    assert window.storage.root == new_root
    assert "после завершения рабочего дня" in window.settings_status_label.text()

    window.close()
    app.processEvents()


def test_settings_screen_clears_deferred_storage_root_when_reverted(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    started_at = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    storage.start_workday(started_at=started_at)
    old_root = storage.root
    window = MainWindow(storage, recorder)
    new_root = tmp_path / "new-data"

    window.settings_storage_root_input.setText(str(new_root))
    window.save_settings()

    assert window.storage.root == old_root
    assert window.pending_storage_root_path == new_root

    window.settings_storage_root_input.setText(str(old_root))
    window.save_settings()

    assert window.storage.root == old_root
    assert window.pending_storage_root_path is None
    window._request_day_summary_update = lambda day_folder, force=False: None
    window.end_workday()

    assert window.storage.root == old_root
    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["storage"]["root"] == str(old_root)

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


def test_summary_runtime_uses_shared_secrets_env_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env.local"
    env_file.write_text("AITUNNEL_KEY=test-secret\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "secrets": {"env_file": str(env_file)},
                "summary": {
                    "enabled": True,
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "AITUNNEL_KEY",
                    "base_url": "https://api.aitunnel.ru/v1/",
                    "env_file": "",
                    "timeout_seconds": 120,
                    "max_chars_per_chunk": 20000,
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()

    window = MainWindow(recorder=recorder)

    assert isinstance(window.storage.summarizer, OpenAISummarizer)
    assert window.storage.summarizer.config["env_file"] == str(env_file)

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
