import os
from pathlib import Path

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

    assert window.obs_status_value.text() == "OBS: выключен в настройках"

    window.check_obs()
    assert window.status_label.text() == "OBS: выключен в настройках"

    window.start_workday()
    assert window.start_meeting_button.isEnabled()

    window.close()
    app.processEvents()
