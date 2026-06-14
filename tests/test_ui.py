import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import patch

import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea, QSizePolicy, QWidget

from app.services.recorder import NoopRecorder
from app.services.storage import MetadataReadError, StorageService
from app.services.summarization import OpenAISummarizer
from app.services.transcription import AITunnelTranscriber, LocalWhisperTranscriber
from app.ui import main_window as main_window_module
from app.ui.main_window import (
    ClickableFrame,
    FloatingMeetingControl,
    MainWindow,
    RiskyActionConfirmationOverlay,
    StartMeetingOverlay,
)
from app.ui.summary_viewer import SummaryMaterialView


class CloseEventStub:
    def __init__(self) -> None:
        self.ignored = False

    def ignore(self) -> None:
        self.ignored = True


class EnabledRecorder(NoopRecorder):
    enabled = True
    status_text = "OBS: подключен"


def test_summary_material_view_starts_in_preview_mode() -> None:
    app = QApplication.instance() or QApplication([])
    view = SummaryMaterialView("Итоги встречи")
    view.set_markdown("# Итоги встречи\n\n## Кратко\n- Обсудили релиз")
    view.show()
    app.processEvents()

    assert view.mode == "preview"
    assert view.editor.isHidden()
    assert view.preview.isVisible()
    assert view.edit_button.isVisible()
    assert view.save_button.isHidden()
    assert view.cancel_button.isHidden()
    assert "Обсудили релиз" in view.preview.toPlainText()

    view.close()
    app.processEvents()


def test_summary_material_view_edit_save_and_cancel_signals() -> None:
    app = QApplication.instance() or QApplication([])
    saved: list[str] = []
    view = SummaryMaterialView("Итоги встречи")
    view.save_requested.connect(saved.append)
    view.set_markdown("# Старый итог\n")

    view.enter_edit_mode()
    view.editor.setPlainText("# Новый итог\n")
    view.save_button.click()

    assert saved == ["# Новый итог\n"]
    assert view.mode == "preview"

    view.enter_edit_mode()
    view.editor.setPlainText("# Несохраненный итог\n")
    view.cancel_button.click()

    assert view.markdown == "# Новый итог\n"
    assert view.mode == "preview"

    view.close()
    app.processEvents()


def test_summary_material_view_tracks_unsaved_changes() -> None:
    app = QApplication.instance() or QApplication([])
    view = SummaryMaterialView("Итог встречи")
    view.set_markdown("# Старый итог\n")

    assert not view.has_unsaved_changes()

    view.enter_edit_mode()
    assert not view.has_unsaved_changes()

    view.editor.setPlainText("# Несохраненный итог\n")
    assert view.has_unsaved_changes()

    view.cancel_button.click()
    assert not view.has_unsaved_changes()

    view.close()
    app.processEvents()


def test_summary_material_view_uses_primary_save_and_block_preview() -> None:
    app = QApplication.instance() or QApplication([])
    view = SummaryMaterialView("Итог встречи")

    view.set_markdown(
        "# Итоги встречи\n\n"
        "## Кратко\n\nОбсудили релиз.\n\n"
        "## Решения\n\n- Запускаем в пятницу\n"
    )
    view.enter_edit_mode()

    assert view.save_button.objectName() == "headerPrimaryButton"

    view.save_button.click()
    html = view.preview.toHtml()
    assert view.preview.property("summary_block_view") is True
    assert "Кратко" in html
    assert "Решения" in html

    view.close()
    app.processEvents()


def _wait_for_qt(app: QApplication, condition, timeout_seconds: float = 2.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    app.processEvents()
    return condition()


def _readiness_statuses(state: str = "ok") -> list[dict[str, object]]:
    return [
        {
            "component": card["component"],
            "state": state,
            "message": f"{card['component']} проверен.",
            "details": [
                {"label": label, "value": "Проверено", "state": "neutral"}
                for label in card["initial_details"]
            ],
        }
        for card in main_window_module.READINESS_CARDS
    ]


def _meeting_start_readiness_statuses(
    obs_state: str = "ok",
    ffmpeg_state: str = "ok",
    transcription_state: str = "ok",
    summary_state: str = "ok",
) -> list[dict[str, object]]:
    states = {
        "Запись разговора (OBS)": obs_state,
        "Извлечение аудио (FFmpeg)": ffmpeg_state,
        "Транскрипция": transcription_state,
        "Итоги встречи": summary_state,
    }
    messages = {
        "ok": "Готово.",
        "error": "Ошибка готовности.",
        "skipped": "Пропущено.",
    }
    return [
        {
            "component": card["component"],
            "state": states[card["component"]],
            "message": messages[states[card["component"]]],
            "details": [
                {
                    "label": label,
                    "value": messages[states[card["component"]]],
                    "state": states[card["component"]],
                }
                for label in card["initial_details"]
            ],
        }
        for card in main_window_module.READINESS_CARDS
    ]


def _create_reprocessable_meeting(
    storage: StorageService,
    tmp_path: Path,
    title: str = "Готовая встреча",
) -> Path:
    recording_path = tmp_path / f"{title}.mkv"
    recording_path.write_bytes(b"fake recording")
    return storage.create_meeting_folder(
        title,
        started_at=datetime.now().replace(hour=10, minute=0, second=0, microsecond=0),
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


def test_risky_action_confirmation_overlay_shows_exact_meeting_warning_in_app() -> None:
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.resize(900, 600)
    overlay = RiskyActionConfirmationOverlay(parent)
    overlay.apply_theme("dark")

    overlay.open_confirmation(
        "Повторить обработку встречи?",
        "Если вы вручную меняли Итог встречи, новая обработка заменит ваши изменения.",
        "Повторить обработку",
    )

    assert overlay.objectName() == "meetingOverlay"
    assert overlay.card.objectName() == "meetingOverlayCard"
    assert not overlay.isHidden()
    assert overlay.title_label.text() == "Повторить обработку встречи?"
    assert overlay.message_label.text() == (
        "Если вы вручную меняли Итог встречи, новая обработка заменит ваши изменения."
    )
    assert overlay.confirm_button.text() == "Повторить обработку"
    assert overlay.cancel_button.text() == "Отмена"
    assert overlay.cancel_button.isDefault()
    assert "QMessageBox" not in type(overlay).__name__

    overlay.cancel_button.click()
    assert overlay.isHidden()
    parent.deleteLater()
    app.processEvents()


def test_risky_action_confirmation_overlay_shows_exact_day_warning_in_app() -> None:
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.resize(900, 600)
    overlay = RiskyActionConfirmationOverlay(parent)
    overlay.apply_theme("dark")

    overlay.open_confirmation(
        "Обновить итоги дня?",
        "Если вы вручную меняли Итог дня, обновление заменит ваши изменения.",
        "Обновить итоги дня",
    )

    assert overlay.objectName() == "meetingOverlay"
    assert overlay.card.objectName() == "meetingOverlayCard"
    assert not overlay.isHidden()
    assert overlay.title_label.text() == "Обновить итоги дня?"
    assert overlay.message_label.text() == (
        "Если вы вручную меняли Итог дня, обновление заменит ваши изменения."
    )
    assert overlay.confirm_button.text() == "Обновить итоги дня"
    assert overlay.cancel_button.text() == "Отмена"
    assert overlay.cancel_button.isDefault()
    assert "#111827" in overlay.styleSheet()
    assert "QMessageBox" not in type(overlay).__name__

    overlay.cancel_button.click()
    assert overlay.isHidden()
    parent.deleteLater()
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
    window._render_readiness_statuses(
        _meeting_start_readiness_statuses(),
        recorder.status_text,
    )

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
        assert _wait_for_qt(
            app,
            lambda: window.readiness_detail_values["Итоги встречи"]["Генерация"].text()
            == "Выключена настройками",
        )
    assert (
        window.readiness_detail_values["Итоги встречи"]["Генерация"].text()
        == "Выключена настройками"
    )
    assert window.readiness_detail_values["Итоги встречи"]["API key"].text() == "Не требуется"
    assert window.pipeline_labels == {}

    window.start_workday()
    assert window.start_meeting_button.isEnabled()

    window.close()
    app.processEvents()


def test_readiness_check_runs_in_background_and_disables_repeated_start(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    started = Event()
    release = Event()
    calls: list[bool] = []

    def slow_check_readiness(config, recorder, data_root):
        del config, recorder, data_root
        calls.append(True)
        started.set()
        release.wait(0.5)
        return _readiness_statuses()

    monkeypatch.setattr(main_window_module, "check_readiness", slow_check_readiness)

    window.check_readiness()

    assert _wait_for_qt(app, started.is_set)
    assert window.check_readiness_button.text() == "Проверяется..."
    assert not window.check_readiness_button.isEnabled()
    assert "выполняется" in window.status_label.text().lower()

    window.check_readiness()

    assert len(calls) == 1

    release.set()

    assert _wait_for_qt(
        app,
        lambda: window.check_readiness_button.isEnabled()
        and window.check_readiness_button.text() == "Проверить готовность",
    )
    assert window.readiness_badges["Запись разговора (OBS)"].text() == "OK"
    assert "Проверка готовности завершена" in window.status_label.text()

    window.close()
    app.processEvents()


def test_floating_control_start_meeting_requires_readiness_check(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.floating_control.show()
    app.processEvents()

    window.floating_control.primary_button.click()
    assert storage.workday_active

    window.floating_control.primary_button.click()
    window.floating_control.title_input.setText("Созвон без проверки")
    window.floating_control.primary_button.click()

    assert not storage.meeting_active
    assert "Сначала дождитесь проверки готовности системы" in window.status_label.text()

    assert _wait_for_qt(app, lambda: not window.readiness_check_running)
    window.close()
    app.processEvents()


def test_readiness_check_auto_runs_when_main_window_is_shown(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    calls: list[Path] = []

    def fake_check_readiness(config, recorder, data_root):
        del config, recorder
        calls.append(data_root)
        return _readiness_statuses()

    monkeypatch.setattr(main_window_module, "check_readiness", fake_check_readiness)

    window = MainWindow(storage, recorder)
    window.show()

    assert _wait_for_qt(app, lambda: len(calls) == 1)
    assert calls == [tmp_path]
    assert _wait_for_qt(
        app,
        lambda: window.readiness_badges["Запись разговора (OBS)"].text() == "OK",
    )

    window.close()
    app.processEvents()


def test_settings_save_auto_runs_fresh_readiness_check(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    calls: list[Path] = []

    def fake_check_readiness(config, recorder, data_root):
        del config, recorder
        calls.append(data_root)
        return _readiness_statuses()

    monkeypatch.setattr(main_window_module, "check_readiness", fake_check_readiness)

    window = MainWindow(storage, recorder)
    window.readiness_startup_check_done = True
    window.show()
    app.processEvents()

    window.settings_storage_root_input.setText(str(tmp_path / "new-data"))
    window.save_settings()

    assert _wait_for_qt(app, lambda: len(calls) == 1)
    assert calls == [tmp_path / "new-data"]
    assert "Проверка готовности запущена автоматически" in window.settings_status_label.text()

    window.close()
    app.processEvents()


def test_start_workday_auto_runs_readiness_check(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    calls: list[Path] = []

    def fake_check_readiness(config, recorder, data_root):
        del config, recorder
        calls.append(data_root)
        return _readiness_statuses()

    monkeypatch.setattr(main_window_module, "check_readiness", fake_check_readiness)

    window = MainWindow(storage, recorder)
    window.readiness_startup_check_done = True
    window.show()
    app.processEvents()

    window.start_workday()

    assert _wait_for_qt(app, lambda: len(calls) == 1)
    assert calls == [tmp_path]
    assert "Проверка готовности" in window.status_label.text()

    window.close()
    app.processEvents()


def test_readiness_check_ignores_stale_result_after_settings_save(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)
    started = Event()
    release = Event()

    def slow_check_readiness(config, recorder, data_root):
        del config, recorder, data_root
        started.set()
        release.wait(0.5)
        return _readiness_statuses()

    monkeypatch.setattr(main_window_module, "check_readiness", slow_check_readiness)

    window.check_readiness()

    assert _wait_for_qt(app, started.is_set)

    window.settings_storage_root_input.setText(str(tmp_path / "new-data"))
    window.save_settings()
    release.set()

    assert _wait_for_qt(
        app,
        lambda: window.check_readiness_button.isEnabled()
        and window.check_readiness_button.text() == "Проверить готовность",
    )
    assert window.readiness_badges["Запись разговора (OBS)"].text() == "Не проверено"
    assert (
        window.readiness_detail_values["Запись разговора (OBS)"]["Состояние"].text()
        == "Не проверено"
    )
    assert "Настройки изменились" in window.status_label.text()
    assert "Проверка готовности" in window.settings_status_label.text()

    window.close()
    app.processEvents()


def test_main_window_blocks_close_while_readiness_check_is_running(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    started = Event()
    release = Event()

    def slow_check_readiness(config, recorder, data_root):
        del config, recorder, data_root
        started.set()
        release.wait(0.5)
        return _readiness_statuses()

    monkeypatch.setattr(main_window_module, "check_readiness", slow_check_readiness)

    window.check_readiness()

    assert _wait_for_qt(app, started.is_set)

    close_event = CloseEventStub()
    window.closeEvent(close_event)

    assert close_event.ignored
    assert "проверки готовности" in window.status_label.text().lower()

    release.set()
    assert _wait_for_qt(app, lambda: window.check_readiness_button.isEnabled())

    window.close()
    app.processEvents()


def test_readiness_check_badge_keeps_active_style_after_theme_reapply(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    started = Event()
    release = Event()

    def slow_check_readiness(config, recorder, data_root):
        del config, recorder, data_root
        started.set()
        release.wait(0.5)
        return _readiness_statuses()

    monkeypatch.setattr(main_window_module, "check_readiness", slow_check_readiness)

    window.check_readiness()

    assert _wait_for_qt(app, started.is_set)

    badge = window.readiness_badges["Запись разговора (OBS)"]
    assert badge.text() == "Проверяется"
    active_background = window._status_colors()["active"][0]

    window._apply_theme_settings()

    assert active_background in badge.styleSheet()

    release.set()
    assert _wait_for_qt(app, lambda: window.check_readiness_button.isEnabled())

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


def test_sidebar_theme_toggle_applies_and_saves_theme(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)

    assert window.theme_toggle_button.text() == "Светлая тема"
    assert window.config["ui"]["theme"] == "light"

    window.theme_toggle_button.click()
    app.processEvents()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["ui"]["theme"] == "dark"
    assert window.config["ui"]["theme"] == "dark"
    assert window.settings_theme_select.currentData() == "dark"
    assert window.theme_toggle_button.text() == "Темная тема"
    assert "#0f172a" in window.styleSheet()

    window.theme_toggle_button.click()
    app.processEvents()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["ui"]["theme"] == "light"
    assert window.config["ui"]["theme"] == "light"
    assert window.settings_theme_select.currentData() == "light"
    assert window.theme_toggle_button.text() == "Светлая тема"
    assert "#f6efe6" in window.styleSheet()

    window.close()
    app.processEvents()


def test_sidebar_theme_toggle_preserves_existing_config_without_runtime_keys(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "storage:\n"
        "  root: CustomSummaries\n"
        "ui:\n"
        "  theme: light\n"
        "  floating_theme: dark\n"
        "custom_flag: keep-me\n",
        encoding="utf-8",
    )
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)

    window.theme_toggle_button.click()
    app.processEvents()

    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert config["storage"]["root"] == "CustomSummaries"
    assert config["ui"] == {"theme": "dark", "floating_theme": "dark"}
    assert config["custom_flag"] == "keep-me"
    assert "_warnings" not in config
    assert "obs" not in config

    window.close()
    app.processEvents()


def test_sidebar_theme_toggle_refuses_to_overwrite_invalid_config(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("- broken\n", encoding="utf-8")
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)

    window.theme_toggle_button.click()
    app.processEvents()

    assert config_path.read_text(encoding="utf-8") == "- broken\n"
    assert window.config["ui"]["theme"] == "light"
    assert "Тема не изменена" in window.status_label.text()
    assert "config.yaml" in window.status_label.text()

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


def test_start_meeting_blocks_when_obs_readiness_has_error(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.start_workday()
    window._render_readiness_statuses(
        _meeting_start_readiness_statuses(obs_state="error"),
        recorder.status_text,
    )

    window.start_meeting()

    assert window.start_meeting_overlay.isHidden()
    assert "OBS недоступен" in window.status_label.text()
    assert "заблокирован" in window.status_label.text()

    window.close()
    app.processEvents()


def test_start_meeting_warns_on_processing_readiness_errors_before_start(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.start_workday()
    window._render_readiness_statuses(
        _meeting_start_readiness_statuses(
            obs_state="ok",
            ffmpeg_state="error",
            transcription_state="error",
        ),
        recorder.status_text,
    )
    confirmed_warnings: list[list[str]] = []

    def reject_warning(warnings: list[str]) -> bool:
        confirmed_warnings.append(warnings)
        return False

    monkeypatch.setattr(
        window,
        "_confirm_start_meeting_with_readiness_warnings",
        reject_warning,
    )

    window._start_meeting_with_title("Созвон с предупреждением")

    assert not storage.meeting_active
    assert confirmed_warnings
    assert any("FFmpeg" in warning for warning in confirmed_warnings[0])
    assert any("Транскрипция" in warning for warning in confirmed_warnings[0])
    assert "Созвон не начат" in window.status_label.text()

    monkeypatch.setattr(
        window,
        "_confirm_start_meeting_with_readiness_warnings",
        lambda warnings: True,
    )

    window._start_meeting_with_title("Созвон с подтверждением")

    assert storage.meeting_active
    assert storage.read_meeting_metadata(storage.active_meeting_folder)["title"] == (
        "Созвон с подтверждением"
    )

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
        "Запись разговора (OBS)",
        "Извлечение аудио (FFmpeg)",
        "Транскрипция",
        "Итоги встречи",
    }
    assert window.readiness_badges["Запись разговора (OBS)"].text() == "Не проверено"
    assert window.readiness_tiles["Запись разговора (OBS)"].minimumHeight() >= 150
    assert window.readiness_tiles["Запись разговора (OBS)"].minimumWidth() >= 300
    assert set(window.readiness_detail_values["Транскрипция"]) == {
        "Режим",
        "Модель",
        "Доступ",
        "Данные",
    }
    assert window.readiness_detail_values["Транскрипция"]["Режим"].text() == "Не проверено"
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


def test_reprocess_meeting_cancel_does_not_mark_or_enqueue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    storage.create_day_folder()
    meeting_folder = _create_reprocessable_meeting(storage, tmp_path)
    window = MainWindow(storage, recorder)
    prompts: list[tuple[str, str, str]] = []
    marked: list[Path] = []
    enqueued: list[Path] = []
    before_metadata = storage.read_meeting_metadata(meeting_folder)

    def reject_prompt(title: str, text: str, confirm_button_text: str) -> bool:
        prompts.append((title, text, confirm_button_text))
        return False

    monkeypatch.setattr(window, "_confirm_risky_action", reject_prompt)
    monkeypatch.setattr(storage, "mark_meeting_for_reprocessing", marked.append)
    monkeypatch.setattr(window, "_enqueue_meeting_processing", enqueued.append)

    window.reprocess_meeting(meeting_folder)

    assert prompts == [
        (
            "Повторить обработку встречи?",
            "Если вы вручную меняли Итог встречи, новая обработка заменит ваши изменения.",
            "Повторить обработку",
        )
    ]
    assert marked == []
    assert enqueued == []
    assert storage.read_meeting_metadata(meeting_folder) == before_metadata
    assert "отменена" in window.status_label.text()

    window.close()
    app.processEvents()


def test_reprocess_meeting_confirm_runs_existing_reprocess_flow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    storage.create_day_folder()
    meeting_folder = _create_reprocessable_meeting(storage, tmp_path)
    window = MainWindow(storage, recorder)
    enqueued: list[Path] = []

    monkeypatch.setattr(window, "_confirm_risky_action", lambda *args: True)
    monkeypatch.setattr(window, "_enqueue_meeting_processing", enqueued.append)

    window.reprocess_meeting(meeting_folder)

    metadata = storage.read_meeting_metadata(meeting_folder)
    assert metadata["processing_status"] == "pending"
    assert metadata["processing_force_reprocess"] is True
    assert enqueued == [meeting_folder]
    assert "Повторная обработка встречи добавлена в очередь" in window.status_label.text()

    window.close()
    app.processEvents()


def test_reprocess_meeting_unavailable_action_does_not_show_warning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    storage.create_day_folder()
    meeting_folder = storage.create_meeting_folder(
        "Встреча без записи",
        started_at=datetime.now().replace(hour=11, minute=0, second=0, microsecond=0),
        metadata={
            "status": "ended",
            "processing_status": "completed",
            "recording_status": "stop_failed",
            "audio_status": "skipped",
            "transcription_status": "skipped",
            "summary_status": "skipped",
        },
    )
    window = MainWindow(storage, recorder)
    prompts: list[bool] = []
    marked: list[Path] = []

    monkeypatch.setattr(window, "_confirm_risky_action", lambda *args: prompts.append(True) or True)
    monkeypatch.setattr(storage, "mark_meeting_for_reprocessing", marked.append)

    window.reprocess_meeting(meeting_folder)

    assert prompts == []
    assert marked == []
    assert "нельзя повторно обработать" in window.status_label.text()

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


def test_past_active_workday_card_is_shown_on_startup(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    setup_storage = StorageService(tmp_path, recorder)
    past_day_folder = setup_storage.start_workday(yesterday.replace(hour=8, minute=30))
    setup_storage.start_meeting("Прошлая встреча", yesterday.replace(hour=9, minute=0))
    setup_storage.finish_active_meeting_recording(yesterday.replace(hour=9, minute=30))

    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    card_text = " ".join(
        label.text() for label in window.past_workday_recovery_card.findChildren(QLabel)
    )
    assert window.past_workday_folder == past_day_folder
    assert not window.past_workday_recovery_card.isHidden()
    assert "Найден незавершенный рабочий день" in card_text
    assert "Встреч: 1" in card_text
    assert not storage.workday_active

    window.close()
    app.processEvents()


def test_past_workday_card_treats_up_to_date_summary_as_ready(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    setup_storage = StorageService(tmp_path, recorder)
    past_day_folder = setup_storage.start_workday(yesterday.replace(hour=8, minute=30))
    setup_storage.end_workday_folder(past_day_folder, now.replace(hour=18, minute=0))
    metadata = setup_storage.ensure_day_summary_metadata(past_day_folder)
    metadata["day_summary_status"] = "up_to_date"
    setup_storage._write_json(setup_storage.day_summary_metadata_path(past_day_folder), metadata)

    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.past_workday_folder = past_day_folder
    window._refresh_past_workday_recovery_card()

    assert window._past_workday_recovery_badge() == ("Итоги готовы", "ok")
    assert "итоги дня готовы" in window._past_workday_recovery_detail_text()

    window.close()
    app.processEvents()


def test_past_workday_card_handles_corrupted_meeting_metadata(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    now = datetime.now()
    yesterday = now - timedelta(days=1)

    class BrokenMeetingMetadataStorage(StorageService):
        def has_unfinished_meeting_processing(self, day_folder):
            raise MetadataReadError(
                Path(day_folder) / "meeting_metadata.json",
                Path(day_folder) / "meeting_metadata.corrupt-test.json",
            )

    setup_storage = BrokenMeetingMetadataStorage(tmp_path, recorder)
    past_day_folder = setup_storage.start_workday(yesterday.replace(hour=8, minute=30))
    setup_storage.end_workday_folder(past_day_folder, now.replace(hour=18, minute=0))

    storage = BrokenMeetingMetadataStorage(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    window.past_workday_folder = past_day_folder

    assert window._past_workday_recovery_badge() == ("Требует внимания", "error")
    assert "Metadata встречи поврежден" in window._past_workday_recovery_detail_text()

    window.close()
    app.processEvents()


def test_past_workday_card_does_not_mix_past_meetings_into_today(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    setup_storage = StorageService(tmp_path, recorder)
    past_day_folder = setup_storage.start_workday(yesterday.replace(hour=8, minute=30))
    past_meeting = setup_storage.start_meeting("Прошлая встреча", yesterday.replace(hour=9, minute=0))
    setup_storage.finish_active_meeting_recording(yesterday.replace(hour=9, minute=30))

    storage = StorageService(tmp_path, recorder)
    today_day_folder = storage.start_workday(now.replace(hour=9, minute=0))
    window = MainWindow(storage, recorder)

    assert window.past_workday_folder == past_day_folder
    assert storage.active_day_folder == today_day_folder
    assert past_meeting not in window.workday_meeting_cards
    assert list(window.workday_meeting_cards) == []

    window.close()
    app.processEvents()


def test_today_workday_can_start_when_past_workday_card_exists(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    setup_storage = StorageService(tmp_path, recorder)
    setup_storage.start_workday(yesterday.replace(hour=8, minute=30))

    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    today_date = datetime.now().date()
    window.start_workday()

    assert storage.workday_active
    assert storage.active_day_folder == tmp_path / today_date.isoformat()
    assert window.past_workday_folder == tmp_path / yesterday.date().isoformat()
    assert not window.past_workday_recovery_card.isHidden()

    window.close()
    app.processEvents()


def test_past_workday_recovery_button_ends_day_and_waits_for_meetings(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    entered = Event()
    release = Event()
    processed: list[Path] = []
    day_summary_calls: list[Path] = []

    class BlockingStorage(StorageService):
        def process_meeting_pipeline(self, meeting_folder, progress_callback=None):
            del progress_callback
            processed.append(meeting_folder)
            entered.set()
            release.wait(5)
            metadata = self.read_meeting_metadata(meeting_folder)
            metadata["processing_status"] = "completed"
            self.write_metadata(meeting_folder, metadata)
            self._sync_day_meeting_metadata(meeting_folder, metadata)
            return meeting_folder

        def process_day_summary_pipeline(self, day_folder, force=False, progress_callback=None):
            day_summary_calls.append(day_folder)
            return super().process_day_summary_pipeline(day_folder, force, progress_callback)

    now = datetime.now()
    yesterday = now - timedelta(days=1)
    setup_storage = BlockingStorage(tmp_path, recorder)
    past_day_folder = setup_storage.start_workday(yesterday.replace(hour=8, minute=30))
    pending_meeting = setup_storage.start_meeting("Нужно обработать", yesterday.replace(hour=9, minute=0))
    setup_storage.finish_active_meeting_recording(yesterday.replace(hour=9, minute=30))

    storage = BlockingStorage(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    window.recover_past_workday_button.click()

    assert _wait_for_qt(app, entered.is_set)
    assert processed == [pending_meeting]
    metadata = json.loads((past_day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    day_summary_metadata = storage.read_day_summary_metadata(past_day_folder)
    assert metadata["status"] == "ended"
    assert day_summary_metadata["day_summary_status"] == "waiting_for_meetings"
    assert window.day_summary_pending
    assert window.day_summary_day_folder == past_day_folder

    release.set()
    assert _wait_for_qt(app, lambda: day_summary_calls == [past_day_folder])

    if window.pipeline_thread is not None:
        window.pipeline_thread.quit()
        window.pipeline_thread.wait(1000)
    if window.day_summary_thread is not None:
        window.day_summary_thread.quit()
        window.day_summary_thread.wait(1000)
    window.close()
    app.processEvents()


def test_past_workday_recovery_starts_day_summary_immediately_without_pending_meetings(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    day_summary_calls: list[Path] = []

    class SummaryStorage(StorageService):
        def process_day_summary_pipeline(self, day_folder, force=False, progress_callback=None):
            day_summary_calls.append(day_folder)
            return super().process_day_summary_pipeline(day_folder, force, progress_callback)

    now = datetime.now()
    yesterday = now - timedelta(days=1)
    setup_storage = SummaryStorage(tmp_path, recorder)
    past_day_folder = setup_storage.start_workday(yesterday.replace(hour=8, minute=30))

    storage = SummaryStorage(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    window.recover_past_workday_button.click()

    assert _wait_for_qt(app, lambda: day_summary_calls == [past_day_folder])
    metadata = json.loads((past_day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "ended"
    assert not window.day_summary_pending
    assert window.past_workday_folder is None
    assert window.past_workday_recovery_card.isHidden()

    if window.day_summary_thread is not None:
        window.day_summary_thread.quit()
        window.day_summary_thread.wait(1000)
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


def test_update_day_summary_cancel_does_not_request_force_update(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.start_workday(datetime.now())
    metadata = storage.ensure_day_summary_metadata(day_folder)
    metadata["day_summary_status"] = "up_to_date"
    storage._write_json(storage.day_summary_metadata_path(day_folder), metadata)
    window = MainWindow(storage, recorder)
    prompts: list[tuple[str, str, str]] = []
    requests: list[tuple[Path, bool]] = []

    def reject_prompt(title: str, text: str, confirm_button_text: str) -> bool:
        prompts.append((title, text, confirm_button_text))
        return False

    monkeypatch.setattr(window, "_confirm_risky_action", reject_prompt)
    monkeypatch.setattr(
        window,
        "_request_day_summary_update",
        lambda day_folder, force=False: requests.append((day_folder, force)) or "",
    )

    window.update_day_summary()

    assert prompts == [
        (
            "Обновить итоги дня?",
            "Если вы вручную меняли Итог дня, обновление заменит ваши изменения.",
            "Обновить итоги дня",
        )
    ]
    assert requests == []
    assert not window.day_summary_pending
    assert not window.day_summary_running
    assert "отменено" in window.status_label.text()

    window.close()
    app.processEvents()


def test_update_day_summary_confirm_requests_force_update(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.start_workday(datetime.now())
    metadata = storage.ensure_day_summary_metadata(day_folder)
    metadata["day_summary_status"] = "up_to_date"
    storage._write_json(storage.day_summary_metadata_path(day_folder), metadata)
    window = MainWindow(storage, recorder)
    requests: list[tuple[Path, bool]] = []

    monkeypatch.setattr(window, "_confirm_risky_action", lambda *args: True)
    monkeypatch.setattr(
        window,
        "_request_day_summary_update",
        lambda day_folder, force=False: requests.append((day_folder, force)) or "",
    )

    window.update_day_summary()

    assert requests == [(day_folder, True)]

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
    assert not hasattr(window, "save_final_files_button")
    assert window.review_summary_view.mode == "preview"

    window.close()
    app.processEvents()


def test_review_summary_header_shows_material_metadata(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder(date(2026, 6, 14))
    meeting_folder = storage.create_meeting_folder(
        "План релиза",
        datetime(2026, 6, 14, 15, 30),
        {
            "status": "ended",
            "summary_status": "draft_created",
            "duration_seconds": 125,
        },
    )
    storage.save_meeting_summary(meeting_folder, "# Итоги встречи\n")
    storage.save_day_summary(day_folder, "# Итоги дня\n")
    storage.ensure_day_summary_metadata(day_folder)
    window = MainWindow(storage, recorder)

    window.open_review()
    window.load_selected_meeting(meeting_folder)

    assert window.review_summary_view.title_label.text() == "Итог встречи"
    assert "15:30" in window.review_summary_view.meta_label.text()
    assert "План релиза" in window.review_summary_view.meta_label.text()
    assert "2 мин." in window.review_summary_view.meta_label.text()

    window.review_day_summary_selected = True
    window.load_day_summary_review()

    assert window.review_summary_view.title_label.text() == "Итог дня"
    assert day_folder.name in window.review_summary_view.meta_label.text()

    window.close()
    app.processEvents()


def test_review_blocks_material_reload_with_unsaved_summary_edits(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder(date(2026, 6, 14))
    first = storage.create_meeting_folder(
        "Первая встреча",
        datetime(2026, 6, 14, 10, 0),
        {"status": "ended", "summary_status": "draft_created"},
    )
    second = storage.create_meeting_folder(
        "Вторая встреча",
        datetime(2026, 6, 14, 11, 0),
        {"status": "ended", "summary_status": "draft_created"},
    )
    storage.save_meeting_summary(first, "# Итог первой встречи\n")
    storage.save_meeting_summary(second, "# Итог второй встречи\n")
    storage.save_day_summary(day_folder, "# Итог дня\n")
    storage.ensure_day_summary_metadata(day_folder)
    window = MainWindow(storage, recorder)

    window.open_review()
    window.select_review_meeting(first)
    window.review_summary_view.enter_edit_mode()
    window.review_summary_view.editor.setPlainText("# Несохраненный итог\n")

    window.select_review_meeting(second)
    assert window.selected_review_meeting_folder == first
    assert window.review_summary_view.editor.toPlainText() == "# Несохраненный итог\n"
    assert window.review_summary_view.mode == "edit"
    assert "Сохраните" in window.review_status_label.text()

    window._refresh_after_lifecycle_change()
    assert window.review_summary_view.editor.toPlainText() == "# Несохраненный итог\n"
    assert window.review_summary_view.mode == "edit"

    window.close()
    app.processEvents()


def test_review_legacy_final_save_method_writes_single_summary_file(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    storage.create_day_folder()
    meeting_folder = storage.create_meeting_folder(
        "Ревью",
        metadata={"status": "ended", "summary_status": "draft_created"},
    )
    window = MainWindow(storage, recorder)

    window.open_review()
    window.review_summary_view.set_markdown("# Новый итог встречи\n")
    window.save_final_files()

    assert (meeting_folder / "summary.md").read_text(encoding="utf-8") == "# Новый итог встречи\n"
    assert not (meeting_folder / "summary_final.md").exists()

    window.close()
    app.processEvents()


def test_review_legacy_final_save_method_uses_live_editor_buffer(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    storage.create_day_folder()
    meeting_folder = storage.create_meeting_folder(
        "Ревью",
        metadata={"status": "ended", "summary_status": "draft_created"},
    )
    window = MainWindow(storage, recorder)

    window.open_review()
    window.review_summary_view.set_markdown("# Старый итог\n")
    window.review_summary_view.enter_edit_mode()
    window.review_summary_view.editor.setPlainText("# Несохраненный итог\n")
    window.save_final_files()

    assert (meeting_folder / "summary.md").read_text(encoding="utf-8") == "# Несохраненный итог\n"
    assert not (meeting_folder / "summary_final.md").exists()

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
    window.settings_summary_template_title_inputs["meeting"].setText("Мой формат встречи")
    meeting_section_title, meeting_section_instruction = (
        window.settings_summary_template_section_inputs["meeting"][0]
    )
    meeting_section_title.setText("Главные решения")
    meeting_section_instruction.setPlainText("Пиши только подтвержденные решения.")
    window.settings_summary_template_rules_inputs["meeting"].setPlainText("Пиши кратко.")
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
    assert config["summary"]["templates"]["meeting"]["title"] == "Мой формат встречи"
    assert config["summary"]["templates"]["meeting"]["sections"][0] == {
        "title": "Главные решения",
        "instruction": "Пиши только подтвержденные решения.",
    }
    assert config["summary"]["templates"]["meeting"]["rules"] == "Пиши кратко."
    markdown_preview = window.settings_summary_template_markdown_previews["meeting"]
    prompt_preview = window.settings_summary_template_prompt_previews["meeting"]
    assert "# Мой формат встречи" in markdown_preview.toPlainText()
    assert "## Главные решения" in markdown_preview.toPlainText()
    assert "Пиши только подтвержденные решения." in prompt_preview.toPlainText()
    assert "Пиши кратко." in prompt_preview.toPlainText()
    assert "Что писать в разделе" not in prompt_preview.toPlainText()
    assert "Без отдельной инструкции" not in prompt_preview.toPlainText()
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


def test_settings_screen_handles_config_write_error(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)
    original_replace = main_window_module.Path.replace

    def flaky_replace(self: Path, target: Path) -> Path:
        if Path(target).name == "config.yaml":
            raise OSError("disk full")
        return original_replace(self, target)

    monkeypatch.setattr(main_window_module.Path, "replace", flaky_replace)

    window.save_settings()

    assert not (tmp_path / "config.yaml").exists()
    assert not list(tmp_path.glob(".config.yaml.*.tmp"))
    assert "Настройки не сохранены" in window.settings_status_label.text()
    assert "config.yaml" in window.settings_status_label.text()
    assert "disk full" in window.settings_status_label.text()

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
    initial_whisper_cli_model = window.settings_transcription_profiles["whisper_cli"]["model"]
    initial_faster_whisper_model = window.settings_transcription_profiles["faster_whisper"]["model"]

    window.settings_transcription_backend_select.setCurrentText("aitunnel")
    window._set_combo_value(window.settings_transcription_model_select, "whisper-1")
    window.settings_transcription_timeout_input.setValue(180)

    window.settings_transcription_backend_select.setCurrentText("whisper_cli")
    app.processEvents()
    assert window._combo_value(window.settings_transcription_model_select) == initial_whisper_cli_model
    window._set_combo_value(window.settings_transcription_model_select, "small")

    window.settings_transcription_backend_select.setCurrentText("faster_whisper")
    app.processEvents()
    assert window._combo_value(window.settings_transcription_model_select) == initial_faster_whisper_model
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


def test_settings_screen_uses_custom_section_navigation(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path / "data", recorder)
    window = MainWindow(storage, recorder)

    assert not hasattr(window, "settings_tabs")
    assert list(window.settings_section_buttons) == [
        "Основное",
        "Запись",
        "Транскрипция",
        "Итоги",
    ]
    assert window.settings_section_buttons["Итоги"].isChecked()
    assert window.settings_sections.currentWidget() is window.settings_summary_section
    assert window.settings_summary_template_tabs is None
    assert list(window.settings_summary_template_buttons) == [
        "Одна встреча",
        "Итоги дня",
    ]
    assert window.settings_summary_template_buttons["Одна встреча"].isChecked()
    assert window.settings_summary_template_grids["meeting"].columnCount() == 2
    assert window.settings_summary_template_structure_panels["meeting"].objectName() == (
        "settingsTemplateStructurePanel"
    )
    assert window.settings_summary_template_side_panels["meeting"].objectName() == (
        "settingsTemplateSidePanel"
    )
    splitter = window.settings_summary_template_right_splitters["meeting"]
    assert splitter.objectName() == "settingsSummaryTemplateRightSplitter"
    assert splitter.count() == 3
    assert window.settings_summary_template_prompt_previews["meeting"].isHidden()
    markdown_preview = window.settings_summary_template_markdown_previews["meeting"]
    prompt_preview = window.settings_summary_template_prompt_previews["meeting"]
    prompt_card = window.settings_summary_template_prompt_cards["meeting"]
    assert markdown_preview.minimumHeight() >= 120
    assert prompt_preview.minimumHeight() >= 135
    assert markdown_preview.minimumHeight() != markdown_preview.maximumHeight()
    assert prompt_preview.minimumHeight() != prompt_preview.maximumHeight()
    assert prompt_card.maximumHeight() <= 110
    structure_panel_labels = [
        label.text()
        for label in window.settings_summary_template_structure_panels["meeting"].findChildren(QLabel)
    ]
    assert "Кратко" not in structure_panel_labels
    assert "Сформулируй 2-4 главных вывода встречи без лишних деталей." not in structure_panel_labels
    markdown_height_before = markdown_preview.height()
    prompt_button = window.settings_summary_template_prompt_buttons["meeting"]
    prompt_button.click()
    app.processEvents()
    assert not prompt_preview.isHidden()
    assert markdown_preview.height() == markdown_height_before
    assert prompt_card.maximumHeight() > 110

    window.settings_section_buttons["Основное"].click()

    assert window.settings_section_buttons["Основное"].isChecked()
    assert window.settings_sections.currentWidget() is window.settings_basic_section

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


def test_theme_reapply_preserves_readiness_detail_states(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    window._render_readiness_details(
        "Транскрипция",
        [
            {"label": "Режим", "value": "AI Tunnel STT"},
            {"label": "Модель", "value": "Whisper Large V3 Turbo"},
            {"label": "Проблема", "value": "API key не найден", "state": "error"},
            {"label": "Что сделать", "value": "Проверьте .env файл", "state": "error"},
        ],
    )
    error_label = window.readiness_detail_values["Транскрипция"]["Проблема"]

    assert error_label.property("readiness_state") == "error"

    window.config["ui"]["theme"] = "dark"
    window._apply_theme_settings()

    assert error_label.property("readiness_state") == "error"
    assert "font-weight: 700" in error_label.styleSheet()

    window.close()
    app.processEvents()


def test_archive_empty_state_points_to_today_workday(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

    window.open_archive()
    archive_text = "\n".join(label.text() for label in window.pages.widget(2).findChildren(QLabel))
    assert "Прошлых рабочих дней пока нет" in archive_text
    assert "Сегодняшний день находится во вкладке `Рабочий день`." in archive_text

    window.close()
    app.processEvents()


def test_archive_lists_past_days_newest_first_and_excludes_today(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    old_day = storage.create_day_folder((datetime.now() - timedelta(days=3)).date())
    recent_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    today_day = storage.create_day_folder(datetime.now().date())
    storage._write_json(old_day / "day_metadata.json", {"date": old_day.name, "status": "ended"})
    storage._write_json(recent_day / "day_metadata.json", {"date": recent_day.name, "status": "ended"})
    storage._write_json(today_day / "day_metadata.json", {"date": today_day.name, "status": "active"})

    window = MainWindow(storage, recorder)
    window.open_archive()

    day_texts = [label.text() for label in window.archive_days_list.findChildren(QLabel)]
    assert day_texts.index(recent_day.name) < day_texts.index(old_day.name)
    assert today_day.name not in day_texts

    window.close()
    app.processEvents()


def test_archive_filters_by_week_and_manual_date_range(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    old_day = storage.create_day_folder((datetime.now() - timedelta(days=20)).date())
    recent_day = storage.create_day_folder((datetime.now() - timedelta(days=2)).date())
    storage._write_json(old_day / "day_metadata.json", {"date": old_day.name, "status": "ended"})
    storage._write_json(recent_day / "day_metadata.json", {"date": recent_day.name, "status": "ended"})

    window = MainWindow(storage, recorder)
    window.open_archive()
    window.set_archive_period("week")

    day_texts = [label.text() for label in window.archive_days_list.findChildren(QLabel)]
    assert recent_day.name in day_texts
    assert old_day.name not in day_texts

    window.archive_from_input.setText(old_day.name)
    window.archive_to_input.setText(old_day.name)
    window.apply_archive_filters()

    day_texts = [label.text() for label in window.archive_days_list.findChildren(QLabel)]
    assert old_day.name in day_texts
    assert recent_day.name not in day_texts

    window.close()
    app.processEvents()


def test_archive_search_filters_days_and_shows_fixed_results(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    release_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    other_day = storage.create_day_folder((datetime.now() - timedelta(days=2)).date())
    storage._write_json(release_day / "day_metadata.json", {"date": release_day.name, "status": "ended"})
    storage._write_json(other_day / "day_metadata.json", {"date": other_day.name, "status": "ended"})
    release_meeting = storage.create_meeting_folder(
        "Планирование релиза",
        datetime.fromisoformat(f"{release_day.name}T09:30:00"),
    )
    other_meeting = storage.create_meeting_folder(
        "Обычная встреча",
        datetime.fromisoformat(f"{other_day.name}T09:30:00"),
    )
    (release_meeting / "transcript.md").write_text("Обсудили релиз и метрики", encoding="utf-8")
    (other_meeting / "transcript.md").write_text("Обсудили задачи", encoding="utf-8")

    window = MainWindow(storage, recorder)
    window.open_archive()
    window.archive_search_input.setText("релиз")
    window.apply_archive_filters()

    day_texts = [label.text() for label in window.archive_days_list.findChildren(QLabel)]
    result_texts = [label.text() for label in window.archive_results_list.findChildren(QLabel)]
    assert release_day.name in day_texts
    assert other_day.name not in day_texts
    assert any("Планирование релиза" in text or "Транскрипт" in text for text in result_texts)
    assert window.archive_results_scroll.maximumHeight() >= 190

    window.close()
    app.processEvents()


def test_archive_search_match_opens_matched_transcript(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder(
        "Планирование",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
    )
    (meeting / "transcript.md").write_text("Обсудили релиз и метрики", encoding="utf-8")

    window = MainWindow(storage, recorder)
    window.open_archive()
    window.archive_search_input.setText("релиз")
    window.apply_archive_filters()
    transcript_match = next(match for match in window.archive_matches if match.kind == "Транскрипт")

    window.open_archive_search_match(transcript_match)

    assert window.archive_editor.isReadOnly()
    assert window.archive_editor.toPlainText() == "Обсудили релиз и метрики"

    window.close()
    app.processEvents()


def test_archive_meeting_detail_shows_summary_and_readonly_transcript(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder(
        "Архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
    )
    storage.save_meeting_summary_draft(meeting, "# Итоги встречи\n")
    (meeting / "transcript.md").write_text("Текст transcript", encoding="utf-8")

    window = MainWindow(storage, recorder)
    window.open_archive()
    window.edit_archive_meeting_summary(meeting)

    assert window.archive_editor.toPlainText() == "# Итоги встречи\n"
    assert not window.archive_editor.isReadOnly()

    window.show_archive_transcript(meeting)

    assert window.archive_editor.toPlainText() == "Текст transcript"
    assert window.archive_editor.isReadOnly()

    window.close()
    app.processEvents()


def test_archive_editor_survives_multiple_detail_rebuilds(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder(
        "Архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
    )
    storage.save_meeting_summary_draft(meeting, "# Итоги встречи\n")

    window = MainWindow(storage, recorder)
    window.open_archive()
    window.edit_archive_meeting_summary(meeting)
    app.processEvents()
    window.refresh_archive()
    app.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    window.edit_archive_meeting_summary(meeting)

    assert window.archive_editor.toPlainText() == "# Итоги встречи\n"

    window.close()
    app.processEvents()


def test_archive_saves_meeting_and_day_summary_single_files(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder(
        "Архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
    )

    window = MainWindow(storage, recorder)
    window.open_archive()
    window.edit_archive_meeting_summary(meeting)
    window.archive_summary_view.enter_edit_mode()
    window.archive_editor.setPlainText("# Новый итог встречи\n")
    window.save_archive_draft()

    assert (meeting / "summary.md").read_text(encoding="utf-8") == "# Новый итог встречи\n"
    assert not (meeting / "summary_final.md").exists()

    window.edit_archive_day_summary(day_folder)
    window.archive_summary_view.enter_edit_mode()
    window.archive_editor.setPlainText("# Новый итог дня\n")
    window.save_archive_draft()

    assert (day_folder / "00_day_summary.md").read_text(encoding="utf-8") == "# Новый итог дня\n"
    assert not (day_folder / "00_day_summary_final.md").exists()

    window.close()
    app.processEvents()


def test_archive_blocks_material_navigation_with_unsaved_summary_edits(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    first = storage.create_meeting_folder(
        "Первая архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
        {"status": "ended", "summary_status": "draft_created"},
    )
    second = storage.create_meeting_folder(
        "Вторая архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T10:30:00"),
        {"status": "ended", "summary_status": "draft_created"},
    )
    storage.save_meeting_summary(first, "# Итог первой встречи\n")
    storage.save_meeting_summary(second, "# Итог второй встречи\n")
    (first / "transcript.md").write_text("# Транскрипт первой встречи\n", encoding="utf-8")
    storage.save_day_summary(day_folder, "# Итог дня\n")
    storage.ensure_day_summary_metadata(day_folder)
    window = MainWindow(storage, recorder)

    window.open_archive()
    window.open_archive_meeting_summary(first)
    window.archive_summary_view.enter_edit_mode()
    window.archive_summary_view.editor.setPlainText("# Несохраненный итог\n")

    window.open_archive_meeting_summary(second)
    assert window.archive_open_material == ("meeting_summary", first)
    assert window.archive_summary_view.editor.toPlainText() == "# Несохраненный итог\n"
    assert window.archive_summary_view.mode == "edit"
    assert "Сохраните" in window.status_label.text()

    window.open_archive_meeting_transcript(first)
    assert window.archive_open_material == ("meeting_summary", first)
    assert window.archive_summary_view.editor.toPlainText() == "# Несохраненный итог\n"

    window.refresh_archive()
    assert window.archive_summary_view.editor.toPlainText() == "# Несохраненный итог\n"
    assert window.archive_summary_view.mode == "edit"

    window.close()
    app.processEvents()


def test_archive_page_contains_expected_controls(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    past_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(past_day / "day_metadata.json", {"date": past_day.name, "status": "ended"})

    window = MainWindow(storage, recorder)
    window.open_archive()
    archive_text = "\n".join(label.text() for label in window.pages.widget(2).findChildren(QLabel))
    buttons = "\n".join(button.text() for button in window.pages.widget(2).findChildren(QPushButton))

    assert "Архив" in archive_text
    assert "Прошлые дни" in archive_text
    assert "Итог дня" in archive_text
    assert "Неделя" in buttons
    assert "Месяц" in buttons
    assert "Все" in buttons  # noqa: RUF001

    window.close()
    app.processEvents()


def test_archive_layout_keeps_readable_day_column_and_dark_scroll_areas(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    past_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(past_day / "day_metadata.json", {"date": past_day.name, "status": "ended"})
    window = MainWindow(storage, recorder)
    window.config["ui"]["theme"] = "dark"
    window._apply_app_style()
    window.resize(1100, 720)

    window.open_archive()
    app.processEvents()

    assert window.archive_splitter.widget(0).minimumWidth() >= 280
    assert window.archive_splitter.sizes()[0] >= 280
    assert window.archive_days_scroll.objectName() == "archiveDaysScroll"
    assert window.archive_days_scroll.viewport().objectName() == "archiveScrollViewport"
    assert window.archive_days_list.objectName() == "archiveDaysList"
    assert window.archive_results_scroll.viewport().objectName() == "archiveScrollViewport"
    assert window.archive_results_list.objectName() == "archiveResultsList"
    assert window.archive_days_list.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    assert window.archive_results_list.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    assert "#111827" in window.styleSheet()

    window.close()
    app.processEvents()


def test_archive_day_cards_are_compact_clickable_and_selected(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    first_day = storage.create_day_folder((datetime.now() - timedelta(days=2)).date())
    second_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(first_day / "day_metadata.json", {"date": first_day.name, "status": "ended"})
    storage._write_json(second_day / "day_metadata.json", {"date": second_day.name, "status": "ended"})

    window = MainWindow(storage, recorder)
    window.open_archive()

    day_cards = window.archive_days_list.findChildren(ClickableFrame, "archiveDayCard")
    assert len(day_cards) == 2
    assert all(card.maximumHeight() <= 76 for card in day_cards)
    assert not window.archive_days_list.findChildren(QPushButton, "archiveDayCard")
    assert all(button.text() != "Открыть" for button in window.archive_days_list.findChildren(QPushButton))
    assert any(card.property("selected") is True for card in day_cards)
    assert any(card.property("selected") is False for card in day_cards)

    unselected = next(card for card in day_cards if card.property("selected") is False)
    unselected.clicked.emit()
    app.processEvents()

    assert window.selected_archive_day_folder == unselected.property("day_folder")
    refreshed_cards = window.archive_days_list.findChildren(ClickableFrame, "archiveDayCard")
    assert sum(1 for card in refreshed_cards if card.property("selected") is True) == 1

    window.close()
    app.processEvents()


def test_archive_search_no_matches_stays_at_top_with_dark_empty_result(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    past_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(past_day / "day_metadata.json", {"date": past_day.name, "status": "ended"})

    window = MainWindow(storage, recorder)
    window.config["ui"]["theme"] = "dark"
    window._apply_app_style()
    window.open_archive()
    assert window.archive_search_card.maximumHeight() <= 180

    window.archive_search_input.setText("ничего-не-найдено")
    window.apply_archive_filters()

    result_text = "\n".join(label.text() for label in window.archive_results_list.findChildren(QLabel))
    assert not window.archive_results_scroll.isHidden()
    assert window.archive_empty_state.isHidden()
    assert "Совпадений не найдено" in result_text
    assert window.archive_header.objectName() == "archiveHeader"
    assert window.archive_header.maximumHeight() <= 96
    assert window.archive_header.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
    assert window.archive_search_card.objectName() == "archiveSearchCard"
    assert window.archive_search_card.maximumHeight() <= 360
    assert window.archive_search_card.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
    assert window.archive_no_matches_spacer.objectName() == "archiveNoMatchesSpacer"
    assert not window.archive_no_matches_spacer.isHidden()
    assert window.archive_no_matches_spacer.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
    assert window.archive_no_matches_spacer.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    assert window.archive_results_list.objectName() == "archiveResultsList"
    assert window.archive_results_list.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    assert window.archive_results_scroll.maximumHeight() >= 190

    window.close()
    app.processEvents()


def test_archive_visible_text_is_russian_and_actions_are_specific(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    storage.save_day_summary_draft(day_folder, "# Итог дня\n")
    meeting = storage.create_meeting_folder(
        "Архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
    )
    storage.save_meeting_summary_draft(meeting, "# Итог встречи\n")
    (meeting / "transcript.md").write_text("Текст транскрипта", encoding="utf-8")

    window = MainWindow(storage, recorder)
    window.open_archive()
    page = window.pages.widget(2)
    visible_text = "\n".join(
        [label.text() for label in page.findChildren(QLabel)]
        + [button.text() for button in page.findChildren(QPushButton)]
        + [window.archive_search_input.placeholderText()]
    )

    assert "транскрипт" in visible_text.casefold()
    assert "transcript" not in visible_text.casefold()
    assert "Редактировать" in visible_text
    assert "Обновить итоги дня" in visible_text
    assert "Редактировать итог дня" not in visible_text
    assert "Редактировать итог встречи" not in visible_text
    assert "Редактировать итоги" not in visible_text
    assert "Сформировать итоги дня" not in visible_text

    window.open_archive_meeting_summary(meeting)
    visible_text = "\n".join(
        [label.text() for label in page.findChildren(QLabel)]
        + [button.text() for button in page.findChildren(QPushButton)]
    )
    assert "Просмотреть транскрипт" in visible_text

    window.close()
    app.processEvents()


def test_archive_detail_cards_are_clickable_accordion_with_header_actions(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    storage.save_day_summary(day_folder, "# Итог дня\n")
    meeting = storage.create_meeting_folder(
        "Архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
        {"status": "ended", "summary_status": "draft_created"},
    )
    storage.save_meeting_summary(meeting, "# Итог встречи\n")

    window = MainWindow(storage, recorder)
    window.open_archive()

    detail_cards = window.archive_detail_layout.parentWidget().findChildren(ClickableFrame, "archiveDetailCard")
    day_card = next(card for card in detail_cards if card.property("material_kind") == "day_summary")
    meeting_card = next(card for card in detail_cards if card.property("material_kind") == "meeting_summary")

    assert day_card.property("open") is True
    assert meeting_card.property("open") is False
    assert not meeting_card.findChildren(QPushButton)

    meeting_card.clicked.emit()
    app.processEvents()
    detail_cards = window.archive_detail_layout.parentWidget().findChildren(ClickableFrame, "archiveDetailCard")
    day_card = next(card for card in detail_cards if card.property("material_kind") == "day_summary")
    meeting_card = next(card for card in detail_cards if card.property("material_kind") == "meeting_summary")
    meeting_buttons = [button.text() for button in meeting_card.findChildren(QPushButton)]

    assert day_card.property("open") is False
    assert meeting_card.property("open") is True
    assert "Редактировать" in meeting_buttons
    assert "Просмотреть транскрипт" in meeting_buttons
    assert "Редактировать итог встречи" not in meeting_buttons
    assert not day_card.findChildren(QPushButton)

    window.close()
    app.processEvents()


def test_archive_transcript_action_toggles_back_to_summary(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder(
        "Архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
        {"status": "ended", "summary_status": "draft_created"},
    )
    storage.save_meeting_summary(meeting, "# Итог встречи\n")
    (meeting / "transcript.md").write_text("Текст транскрипта", encoding="utf-8")

    window = MainWindow(storage, recorder)
    window.open_archive()
    window.open_archive_meeting_summary(meeting)
    page = window.pages.widget(2)

    transcript_button = next(button for button in page.findChildren(QPushButton) if button.text() == "Просмотреть транскрипт")
    transcript_button.click()
    app.processEvents()

    assert window.archive_open_material == ("meeting_transcript", meeting)
    assert window.archive_transcript_view.isReadOnly()
    assert window.archive_transcript_view.toPlainText() == "Текст транскрипта"
    assert any(button.text() == "Показать итог" for button in page.findChildren(QPushButton))
    assert not any(button.text() == "Просмотреть транскрипт" for button in page.findChildren(QPushButton))

    summary_button = next(button for button in page.findChildren(QPushButton) if button.text() == "Показать итог")
    summary_button.click()
    app.processEvents()

    assert window.archive_open_material == ("meeting_summary", meeting)
    assert not window.archive_summary_view.preview.isHidden()

    window.close()
    app.processEvents()


def test_summary_review_and_archive_ui_no_longer_shows_draft_or_final_words(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder(date.today())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder("Проверка", datetime.fromisoformat(f"{day_folder.name}T10:00:00"))
    storage.save_meeting_summary(meeting, "# Итог\n")
    storage.save_day_summary(day_folder, "# Итог дня\n")

    archive_day = storage.create_day_folder(date.today() - timedelta(days=1))
    storage._write_json(archive_day / "day_metadata.json", {"date": archive_day.name, "status": "ended"})
    storage.save_day_summary(archive_day, "# Архивный итог дня\n")

    window = MainWindow(storage, recorder)
    window.open_review()
    window.open_archive()

    pages = [window.pages.widget(1), window.pages.widget(2)]
    visible_text = "\n".join(
        text
        for page in pages
        for text in (
            [label.text() for label in page.findChildren(QLabel)]
            + [button.text() for button in page.findChildren(QPushButton)]
        )
    ).casefold()

    assert "чернов" not in visible_text
    assert "финал" not in visible_text
    assert "draft" not in visible_text
    assert "final" not in visible_text

    window.close()
    app.processEvents()


def test_archive_filters_have_active_state_and_date_masks(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    past_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(past_day / "day_metadata.json", {"date": past_day.name, "status": "ended"})

    window = MainWindow(storage, recorder)
    window.open_archive()

    period_buttons = [window.archive_week_button, window.archive_month_button, window.archive_all_button]
    assert all(button.isCheckable() for button in period_buttons)
    assert window.archive_all_button.isChecked()
    assert len({button.minimumWidth() for button in period_buttons}) == 1
    assert window.archive_from_input.inputMask().startswith("0000-00-00")
    assert window.archive_to_input.inputMask().startswith("0000-00-00")

    window.set_archive_period("week")
    assert window.archive_week_button.isChecked()
    assert not window.archive_month_button.isChecked()
    assert not window.archive_all_button.isChecked()

    window.archive_from_input.setText("9999-99-99")
    window.apply_archive_filters()
    assert window.archive_days

    window.close()
    app.processEvents()


def test_archive_search_results_are_compact_and_keep_action_near_text(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder(
        "Очень длинная встреча про релиз продукта и несколько направлений",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
    )
    storage.save_meeting_summary_draft(
        meeting,
        "# Итоги\n\n" + "Длинный markdown перед совпадением. " * 12 + "релиз " + "длинное продолжение. " * 12,
    )

    window = MainWindow(storage, recorder)
    window.resize(900, 620)
    window.open_archive()
    window.archive_search_input.setText("релиз")
    window.apply_archive_filters()

    result_cards = window.archive_results_list.findChildren(QWidget, "archiveSearchResult")
    snippet_labels = window.archive_results_list.findChildren(QLabel, "archiveSearchSnippet")
    open_buttons = [
        button
        for button in window.archive_results_list.findChildren(QPushButton)
        if button.text() == "Открыть"
    ]

    assert result_cards
    assert snippet_labels
    assert all(label.maximumHeight() <= 48 for label in snippet_labels)
    plain_texts = [label.property("plain_text") for label in snippet_labels]
    assert all(isinstance(text, str) for text in plain_texts)
    assert all(len(text) <= 96 for text in plain_texts)
    assert all(button.maximumWidth() <= 96 for button in open_buttons)
    assert window.archive_results_scroll.maximumHeight() >= 190
    assert any(label.textFormat() == Qt.TextFormat.RichText for label in snippet_labels)
    assert any("archiveSearchHighlight" in label.text() for label in snippet_labels)
    assert any(">релиз<" in label.text().casefold() for label in snippet_labels)

    window.close()
    app.processEvents()


def test_archive_detail_cards_have_inner_padding(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    storage.save_day_summary_draft(day_folder, "# Итог дня\n")
    meeting = storage.create_meeting_folder(
        "Архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
    )
    storage.save_meeting_summary_draft(meeting, "# Итог встречи\n")

    window = MainWindow(storage, recorder)
    window.open_archive()

    detail_cards = window.archive_detail_layout.parentWidget().findChildren(QWidget, "archiveDetailCard")
    assert detail_cards
    assert window.archive_detail_layout.contentsMargins().top() >= 24
    assert all(card.layout().contentsMargins().left() >= 18 for card in detail_cards)
    assert all(card.layout().spacing() >= 10 for card in detail_cards)

    window.close()
    app.processEvents()


def test_archive_search_detail_shows_only_relevant_meetings(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    release_meeting = storage.create_meeting_folder(
        "Планирование релиза",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
    )
    unrelated_meeting = storage.create_meeting_folder(
        "Обычная синхронизация",
        datetime.fromisoformat(f"{day_folder.name}T10:30:00"),
    )
    storage.save_meeting_summary_draft(release_meeting, "Обсудили релиз продукта")
    storage.save_meeting_summary_draft(unrelated_meeting, "Обсудили операционные вопросы")

    window = MainWindow(storage, recorder)
    window.open_archive()
    window.archive_search_input.setText("релиз")
    window.apply_archive_filters()

    detail_text = "\n".join(label.text() for label in window.archive_detail_layout.parentWidget().findChildren(QLabel))
    assert "Планирование релиза" in detail_text
    assert "Обычная синхронизация" not in detail_text

    window.close()
    app.processEvents()


def test_archive_finish_active_past_day_recovers_meetings_and_requests_day_summary(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    past_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(past_day / "day_metadata.json", {"date": past_day.name, "status": "active"})
    meeting = storage.create_meeting_folder(
        "Прошлая встреча",
        datetime.fromisoformat(f"{past_day.name}T09:30:00"),
    )
    metadata = storage.read_meeting_metadata(meeting)
    metadata.update({"status": "ended", "processing_status": "running"})
    storage.write_metadata(meeting, metadata)
    requested: list[Path] = []
    enqueued: list[Path] = []

    window = MainWindow(storage, recorder)
    window._request_day_summary_update = lambda day_folder, force=False: requested.append(day_folder) or "ok"
    window._enqueue_meeting_processing = lambda meeting_folder: enqueued.append(meeting_folder)
    window.past_workday_folder = past_day
    window.finish_archive_workday(past_day)

    day_metadata = storage.read_day_metadata(past_day)
    meeting_metadata = storage.read_meeting_metadata(meeting)
    assert day_metadata["status"] == "ended"
    assert meeting_metadata["processing_status"] == "pending"
    assert enqueued == [meeting]
    assert requested == [past_day]
    assert window.past_workday_folder is None

    window.close()
    app.processEvents()


def test_archive_finish_past_day_handles_metadata_recovery_error(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    past_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(past_day / "day_metadata.json", {"date": past_day.name, "status": "active"})
    error = MetadataReadError(past_day / "meeting_metadata.json", past_day / "meeting_metadata.corrupt.json")

    window = MainWindow(storage, recorder)
    window.storage.recover_interrupted_meeting_processing = lambda day_folder: (_ for _ in ()).throw(error)
    window.finish_archive_workday(past_day)

    assert "Прошлый рабочий день требует внимания" in window.status_label.text()
    assert "backup" in window.status_label.text()

    window.close()
    app.processEvents()


def test_archive_reprocess_meeting_uses_existing_queue(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    past_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(past_day / "day_metadata.json", {"date": past_day.name, "status": "ended"})
    meeting = storage.create_meeting_folder(
        "Прошлая встреча",
        datetime.fromisoformat(f"{past_day.name}T09:30:00"),
    )
    metadata = storage.read_meeting_metadata(meeting)
    metadata.update(
        {
            "status": "ended",
            "processing_status": "completed",
            "summary_status": "draft_created",
            "recording_path": str(meeting / "recording.mkv"),
        }
    )
    (meeting / "recording.mkv").write_text("recording", encoding="utf-8")
    storage.write_metadata(meeting, metadata)
    enqueued: list[Path] = []

    window = MainWindow(storage, recorder)
    prompts: list[tuple[str, str, str]] = []
    window._enqueue_meeting_processing = lambda meeting_folder: enqueued.append(meeting_folder)
    monkeypatch.setattr(
        window.risky_action_confirmation_overlay,
        "confirm_action",
        lambda title, text, confirm_button_text: prompts.append((title, text, confirm_button_text)) or True,
    )
    window.archive_reprocess_meeting(meeting)

    assert storage.read_meeting_metadata(meeting)["processing_status"] == "pending"
    assert enqueued == [meeting]
    assert prompts == [
        (
            "Повторить обработку встречи?",
            "Если вы вручную меняли Итог встречи, новая обработка заменит ваши изменения.",
            "Повторить обработку",
        )
    ]

    window.close()
    app.processEvents()


def test_archive_day_summary_update_cancel_uses_common_warning(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    past_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(past_day / "day_metadata.json", {"date": past_day.name, "status": "ended"})
    window = MainWindow(storage, recorder)
    prompts: list[tuple[str, str, str]] = []
    requests: list[tuple[Path, bool]] = []

    monkeypatch.setattr(
        window.risky_action_confirmation_overlay,
        "confirm_action",
        lambda title, text, confirm_button_text: prompts.append((title, text, confirm_button_text)) and False,
    )
    monkeypatch.setattr(
        window,
        "_request_day_summary_update",
        lambda day_folder, force=False: requests.append((day_folder, force)) or "",
    )

    window.request_archive_day_summary_update(past_day)

    assert prompts == [
        (
            "Обновить итоги дня?",
            "Если вы вручную меняли Итог дня, обновление заменит ваши изменения.",
            "Обновить итоги дня",
        )
    ]
    assert requests == []
    assert "отменено" in window.status_label.text()

    window.close()
    app.processEvents()


def test_archive_day_summary_update_confirm_requests_force_update(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    past_day = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(past_day / "day_metadata.json", {"date": past_day.name, "status": "ended"})
    window = MainWindow(storage, recorder)
    requests: list[tuple[Path, bool]] = []

    monkeypatch.setattr(window.risky_action_confirmation_overlay, "confirm_action", lambda *args: True)
    monkeypatch.setattr(
        window,
        "_request_day_summary_update",
        lambda day_folder, force=False: requests.append((day_folder, force)) or "",
    )

    window.request_archive_day_summary_update(past_day)

    assert requests == [(past_day, True)]

    window.close()
    app.processEvents()


def test_archive_refreshes_after_lifecycle_change_when_visible(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)
    calls: list[str] = []

    window.open_archive()
    window.refresh_archive = lambda: calls.append("archive")
    window._refresh_after_lifecycle_change()

    assert calls == ["archive"]

    window.close()
    app.processEvents()


def test_archive_lifecycle_refresh_keeps_active_editor(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder(
        "Архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
    )
    storage.save_meeting_summary_draft(meeting, "# Старые итоги\n")
    window = MainWindow(storage, recorder)
    calls: list[str] = []

    window.open_archive()
    window.edit_archive_meeting_summary(meeting)
    window.archive_summary_view.enter_edit_mode()
    window.archive_editor.setPlainText("# Несохраненные правки\n")
    window.refresh_archive = lambda: calls.append("archive")
    window._refresh_after_lifecycle_change()

    assert calls == []
    assert window.archive_editor.toPlainText() == "# Несохраненные правки\n"

    window.close()
    app.processEvents()


def test_archive_lifecycle_refresh_runs_in_summary_preview_mode(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    day_folder = storage.create_day_folder((datetime.now() - timedelta(days=1)).date())
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    meeting = storage.create_meeting_folder(
        "Архивная встреча",
        datetime.fromisoformat(f"{day_folder.name}T09:30:00"),
    )
    storage.save_meeting_summary(meeting, "# Итоги\n")
    window = MainWindow(storage, recorder)
    calls: list[str] = []

    window.open_archive()
    window.open_archive_meeting_summary(meeting)
    window.refresh_archive = lambda: calls.append("archive")
    window._refresh_after_lifecycle_change()

    assert calls == ["archive"]

    window.close()
    app.processEvents()


def test_help_page_explains_local_flow(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    recorder = NoopRecorder()
    storage = StorageService(tmp_path, recorder)
    window = MainWindow(storage, recorder)

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
