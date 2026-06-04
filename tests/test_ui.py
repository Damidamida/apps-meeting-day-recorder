import os
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import patch

import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from app.services.recorder import NoopRecorder
from app.services.storage import StorageService
from app.ui.main_window import MainWindow


def test_main_window_shows_disabled_obs_status_and_local_workflow(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.config["summary"]["enabled"] = False

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

    with patch("app.ui.main_window.QInputDialog.getText", return_value=("Планерка", True)):
        window.start_meeting()

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
    assert window.start_workday_button is window.end_workday_button
    assert window.workday_action_button.text() == "Начать рабочий день"
    assert window.workday_action_button.objectName() == "primaryButton"
    assert window.start_meeting_button.isHidden()
    assert window.end_meeting_button.objectName() == "dangerButton"

    window.start_workday()

    assert window.workday_action_button.text() == "Завершить рабочий день"
    assert window.workday_action_button.objectName() == "dangerButton"
    assert not window.start_meeting_button.isHidden()

    window.close()
    app.processEvents()


def test_pipeline_steps_are_rendered_as_status_rows(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    window.start_workday()
    with patch("app.ui.main_window.QInputDialog.getText", return_value=("Pipeline", True)):
        window.start_meeting()

    window.workday_meeting_cards[storage.active_meeting_folder].clicked.emit()

    assert set(window.pipeline_step_titles) == {
        "meeting",
        "recording",
        "audio",
        "transcription",
        "summary",
        "done",
    }

    window._set_pipeline_step("audio", "Выполняется", "Тестовая обработка audio.wav.", "active")

    assert "Тестовая обработка audio.wav." in window.pipeline_labels["audio"].text()
    assert window.pipeline_labels["audio"].maximumWidth() == 900
    assert window.pipeline_labels["audio"].minimumWidth() == 420
    assert window.pipeline_labels["audio"].minimumHeight() == 28
    assert not window.pipeline_labels["audio"].wordWrap()

    window.close()
    app.processEvents()


def test_workday_meeting_card_contains_folder_actions_after_click(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    window.start_workday()
    with patch("app.ui.main_window.QInputDialog.getText", return_value=("Карточка", True)):
        window.start_meeting()
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

    window.close()
    app.processEvents()


def test_review_screen_uses_meeting_summary_transcript_and_separate_day_summary(
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
    assert not window.day_summary_editor.isEnabled()
    assert not (day_folder / "00_day_summary_draft.md").exists()

    storage.save_day_summary_draft(day_folder, "# Итоги дня\n")
    window.refresh_review()

    assert window.day_summary_editor.isEnabled()
    assert window.day_summary_editor.toPlainText() == "# Итоги дня\n"
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
        started_at=datetime(2026, 6, 4, 10, 0, 0),
        metadata={"status": "ended"},
    )
    second = storage.create_meeting_folder(
        "Вторая",
        started_at=datetime(2026, 6, 4, 11, 0, 0),
        metadata={"status": "ended"},
    )
    window = MainWindow(storage, recorder)
    window.open_review()

    assert window.selected_review_meeting_folder == first

    window.review_meeting_cards[second].clicked.emit()

    assert window.selected_review_meeting_folder == second

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
    window.settings_transcription_backend_select.setCurrentText("faster_whisper")
    window.settings_transcription_model_input.setText("small")
    window.settings_summary_enabled_checkbox.setChecked(True)
    window.settings_summary_api_key_env_input.setText("PROXYAPI_KEY")
    window.settings_summary_base_url_input.setText("https://api.proxyapi.ru/openai/v1")
    window.settings_theme_select.setCurrentText("dark_later")

    window.save_settings()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["storage"]["root"] == "MeetingSummariesCustom"
    assert config["obs"]["enabled"] is True
    assert config["obs"]["websocket_host"] == "127.0.0.1"
    assert config["obs"]["websocket_port"] == 4456
    assert config["obs"]["websocket_password"] == "secret"
    assert config["transcription"]["backend"] == "faster_whisper"
    assert config["transcription"]["model"] == "small"
    assert config["summary"]["enabled"] is True
    assert config["summary"]["api_key_env"] == "PROXYAPI_KEY"
    assert config["summary"]["base_url"] == "https://api.proxyapi.ru/openai/v1"
    assert config["ui"]["theme"] == "dark_later"
    assert "перезапустите приложение" in window.settings_status_label.text()

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
    assert not window.end_workday_button.isEnabled()

    with patch("app.ui.main_window.QInputDialog.getText", return_value=("Second", True)):
        window.start_meeting()

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

    assert "Готово" in window.pipeline_labels["audio"].text()

    window.close()
    app.processEvents()
