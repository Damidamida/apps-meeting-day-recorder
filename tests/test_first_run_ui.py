import os
from pathlib import Path

import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from app.services.first_run import default_setup_config, normalize_setup_config
from app.services.recorder import NoopRecorder
from app.services.storage import StorageService
from app.ui.first_run_wizard import FirstRunWizard
from app.ui.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


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
    assert not wizard.next_button.isEnabled()
    assert wizard.aitunnel_link.openExternalLinks()
    assert wizard.aitunnel_link.text().find("https://aitunnel.ru/") != -1
    assert wizard.transcription_backend_select.itemText(0) == "AI Tunnel STT"
    assert wizard.transcription_backend_select.currentText() == "AI Tunnel STT"
    assert not wizard.summary_page.findChildren(QLineEdit)

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
    assert window.nav_buttons[3].isEnabled()
    assert window.nav_buttons[4].isEnabled()

    window.open_review()
    assert window.pages.currentWidget() is window.first_run_wizard
    window.open_archive()
    assert window.pages.currentWidget() is window.first_run_wizard
    window.start_workday()
    assert "Завершите настройку BK Scribe" in window.status_label.text()
    assert storage.workday_active is False

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
