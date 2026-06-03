import os
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

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
    assert "Созвон не начат" in window.pipeline_labels["meeting"].text()

    window.start_workday()
    assert window.start_meeting_button.isEnabled()

    window.close()
    app.processEvents()


def test_end_meeting_starts_background_pipeline_and_disables_lifecycle_buttons(
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

        def end_meeting_pipeline(self, ended_at=None, progress_callback=None):
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
                },
            )
            self.active_meeting_folder = None
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
    assert not window.start_meeting_button.isEnabled()
    assert not window.end_meeting_button.isEnabled()

    storage.release.set()
    deadline = time.time() + 2
    while window.pipeline_running and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert not window.pipeline_running
    window.close()
    app.processEvents()
