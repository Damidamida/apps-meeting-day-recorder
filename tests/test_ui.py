import os
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea

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
    assert window.selected_workday_meeting_folder == storage.active_meeting_folder
    assert "Выполняется" in window.pipeline_labels["recording"].text()

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

    window.toggle_readiness_button.click()

    assert window.readiness_body.isHidden()
    assert window.readiness_card.height() == window.READINESS_CARD_COLLAPSED_HEIGHT
    assert window.toggle_readiness_button.text() == "Развернуть"
    assert window.start_workday_button.objectName() == "primaryButton"
    assert window.start_meeting_button.objectName() == "primaryButton"
    assert window.end_meeting_button.objectName() == "dangerButton"
    assert window.end_workday_button.objectName() == "dangerButton"

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

    storage.active_meeting_folder = None
    window._on_pipeline_progress("audio_done", "Поздний сигнал audio_done.")

    assert "Готово" in window.pipeline_labels["audio"].text()

    window.close()
    app.processEvents()
