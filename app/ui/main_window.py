from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

import yaml

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.config import DEFAULT_CONFIG, load_config
from app.services.readiness import check_readiness
from app.services.recorder import Recorder, RecorderError, create_recorder
from app.services.storage import StorageService
from app.services.summarization import create_summarizer
from app.services.transcription import create_transcriber


WHISPER_CLI_MODEL_OPTIONS = [
    ("tiny", "tiny"),
    ("base", "base"),
    ("small", "small"),
    ("medium", "medium"),
    ("large", "large"),
    ("turbo", "turbo"),
]
FASTER_WHISPER_MODEL_OPTIONS = [
    ("tiny", "tiny"),
    ("base", "base"),
    ("small", "small"),
    ("medium", "medium"),
    ("large-v3", "large-v3"),
    ("turbo", "turbo"),
]
AITUNNEL_MODEL_OPTIONS = [
    ("Whisper Large V3 Turbo — 0.13 ₽/мин", "whisper-large-v3-turbo"),
    ("Whisper Large V3 — 0.36 ₽/мин", "whisper-large-v3"),
    ("Whisper 1 — 1.15 ₽/мин", "whisper-1"),
]
SUMMARY_MODEL_OPTIONS = [
    ("GPT 5.4 Mini — 144 ₽/1M вход · 864 ₽/1M выход", "gpt-5.4-mini"),
    ("GPT 5.4 Nano — 38.4 ₽/1M вход · 240 ₽/1M выход", "gpt-5.4-nano"),
    ("Другая модель AI Tunnel", "__custom__"),
]


class MeetingPipelineWorker(QObject):
    progress = Signal(str, str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, storage: StorageService, meeting_folder: Path) -> None:
        super().__init__()
        self.storage = storage
        self.meeting_folder = meeting_folder

    @Slot()
    def run(self) -> None:
        try:
            meeting_folder = self.storage.process_meeting_pipeline(
                self.meeting_folder,
                progress_callback=lambda event, message: self.progress.emit(event, message)
            )
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.finished.emit(str(meeting_folder))


class DaySummaryPipelineWorker(QObject):
    progress = Signal(str, str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, storage: StorageService, day_folder: Path, force: bool = False) -> None:
        super().__init__()
        self.storage = storage
        self.day_folder = day_folder
        self.force = force

    @Slot()
    def run(self) -> None:
        try:
            day_folder = self.storage.process_day_summary_pipeline(
                self.day_folder,
                force=self.force,
                progress_callback=lambda event, message: self.progress.emit(event, message),
            )
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.finished.emit(str(day_folder))


class ClickableFrame(QFrame):
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StartMeetingOverlay(QWidget):
    submitted = Signal(str)
    canceled = Signal()

    def __init__(self, recorder: Recorder, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("meetingOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = "light"
        self.hide()

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addStretch(1)

        center_row = QHBoxLayout()
        center_row.setContentsMargins(24, 24, 24, 24)
        center_row.addStretch(1)

        self.card = QFrame()
        self.card.setObjectName("meetingOverlayCard")
        self.card.setFixedWidth(560)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(58, 20, 8, 90))
        self.card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        body = QWidget()
        body.setObjectName("meetingOverlayBody")
        body_layout = QVBoxLayout()
        body_layout.setContentsMargins(24, 22, 24, 18)
        body_layout.setSpacing(10)

        title_label = QLabel("Начать встречу")
        title_label.setObjectName("overlayTitle")
        title_label.setMinimumHeight(26)

        name_label = QLabel("Название встречи")
        name_label.setObjectName("overlayLabel")
        self.title_input = QLineEdit()
        self.title_input.setObjectName("meetingTitleInput")
        self.title_input.setPlaceholderText("Например: синхронизация по релизу")
        self.title_input.returnPressed.connect(self._accept_if_valid)

        recording_label = QLabel("Запись")
        recording_label.setObjectName("overlayLabel")
        self.recording_status_label = QLabel()
        self.recording_status_label.setObjectName("overlayRecordingStatus")

        self.error_label = QLabel("Введите название встречи.")
        self.error_label.setObjectName("overlayError")
        self.error_label.setWordWrap(True)
        self.error_label.hide()

        body_layout.addWidget(title_label)
        body_layout.addSpacing(4)
        body_layout.addWidget(name_label)
        body_layout.addWidget(self.title_input)
        body_layout.addSpacing(6)
        body_layout.addWidget(recording_label)
        body_layout.addWidget(self.recording_status_label)
        body_layout.addWidget(self.error_label)
        body.setLayout(body_layout)

        footer = QWidget()
        footer.setObjectName("meetingOverlayFooter")
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(24, 14, 16, 14)
        footer_layout.setSpacing(10)
        footer_layout.addStretch(1)
        cancel_button = QPushButton("Отмена")
        cancel_button.setObjectName("dialogButton")
        cancel_button.clicked.connect(self._cancel)
        start_button = QPushButton("Начать встречу")
        start_button.setObjectName("dialogPrimaryButton")
        start_button.clicked.connect(self._accept_if_valid)
        footer_layout.addWidget(cancel_button)
        footer_layout.addWidget(start_button)
        footer.setLayout(footer_layout)

        card_layout.addWidget(body)
        card_layout.addWidget(footer)
        self.card.setLayout(card_layout)
        center_row.addWidget(self.card, 0, Qt.AlignmentFlag.AlignCenter)
        center_row.addStretch(1)
        root_layout.addLayout(center_row)
        root_layout.addStretch(1)
        self.setLayout(root_layout)
        self.apply_theme("light")
        self.update_recorder_state(recorder)

    def open_for_recorder(self, recorder: Recorder) -> None:
        self.update_recorder_state(recorder)
        self.title_input.clear()
        self.error_label.hide()
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.title_input.setFocus()

    def update_recorder_state(self, recorder: Recorder) -> None:
        state = "ok" if getattr(recorder, "enabled", False) else "wait"
        self.recording_status_label.setText(self._recording_status_text(recorder))
        self.recording_status_label.setProperty("state", state)
        self.recording_status_label.style().unpolish(self.recording_status_label)
        self.recording_status_label.style().polish(self.recording_status_label)

    def apply_theme(self, theme: str) -> None:
        self._theme = "dark" if theme == "dark" else "light"
        self.setStyleSheet(self._overlay_style(self._theme))

    @staticmethod
    def _recording_status_text(recorder: Recorder) -> str:
        if getattr(recorder, "enabled", False):
            return "OBS будет запущен автоматически"
        return "OBS недоступен или выключен, встреча начнется без записи"

    @staticmethod
    def _overlay_style(theme: str) -> str:
        colors = {
            "light": {
                "overlay": "rgba(48, 52, 60, 150)",
                "surface": "#fffdf8",
                "footer": "#f6efe6",
                "border": "#ead8c6",
                "input_border": "#d9bfa8",
                "input_focus": "#ffffff",
                "text": "#3a1408",
                "muted": "#7b4b35",
                "accent": "#ff6f1a",
                "accent_hover": "#f45a00",
                "danger": "#d9280f",
                "ok_bg": "#d7f8df",
                "ok_text": "#007a32",
                "wait_bg": "#f3e8dc",
            },
            "dark": {
                "overlay": "rgba(2, 6, 23, 180)",
                "surface": "#111827",
                "footer": "#0f172a",
                "border": "#374151",
                "input_border": "#4b5563",
                "input_focus": "#0b1220",
                "text": "#f9fafb",
                "muted": "#d1d5db",
                "accent": "#f97316",
                "accent_hover": "#ea580c",
                "danger": "#ef4444",
                "ok_bg": "#064e3b",
                "ok_text": "#bbf7d0",
                "wait_bg": "#1f2937",
            },
        }["dark" if theme == "dark" else "light"]
        return """
            QWidget#meetingOverlay {
                background: %(overlay)s;
                font-family: "Segoe UI";
                font-size: 13px;
                color: %(text)s;
            }
            QFrame#meetingOverlayCard {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
            }
            QWidget#meetingOverlayBody {
                background: %(surface)s;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            QLabel#overlayTitle {
                color: %(text)s;
                font-size: 17px;
                font-weight: 800;
            }
            QLabel#overlayLabel {
                color: %(muted)s;
                font-weight: 500;
            }
            QLineEdit#meetingTitleInput {
                background: %(surface)s;
                color: %(text)s;
                border: 1px solid %(input_border)s;
                border-radius: 6px;
                padding: 8px 10px;
                min-height: 34px;
            }
            QLineEdit#meetingTitleInput:focus {
                background: %(input_focus)s;
                border-color: %(accent)s;
            }
            QLabel#overlayRecordingStatus {
                border-radius: 11px;
                padding: 5px 10px;
                font-weight: 800;
            }
            QLabel#overlayRecordingStatus[state="ok"] {
                background: %(ok_bg)s;
                color: %(ok_text)s;
            }
            QLabel#overlayRecordingStatus[state="wait"] {
                background: %(wait_bg)s;
                color: %(muted)s;
            }
            QLabel#overlayError {
                color: %(danger)s;
                font-weight: 600;
            }
            QWidget#meetingOverlayFooter {
                background: %(footer)s;
                border-top: 1px solid %(border)s;
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
            }
            QPushButton#dialogButton {
                background: %(surface)s;
                color: %(text)s;
                border: 1px solid %(border)s;
                border-radius: 6px;
                padding: 8px 14px;
                min-height: 30px;
                font-weight: 600;
            }
            QPushButton#dialogButton:hover {
                border-color: %(accent)s;
                color: %(accent)s;
            }
            QPushButton#dialogPrimaryButton {
                background: %(accent)s;
                color: #ffffff;
                border: 1px solid %(accent)s;
                border-radius: 6px;
                padding: 8px 14px;
                min-height: 30px;
                font-weight: 800;
            }
            QPushButton#dialogPrimaryButton:hover {
                background: %(accent_hover)s;
                border-color: %(accent_hover)s;
            }
        """ % colors

    def _cancel(self) -> None:
        self.hide()
        self.canceled.emit()

    def _accept_if_valid(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            self.error_label.show()
            self.title_input.setFocus()
            return
        self.hide()
        self.submitted.emit(title)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)


class FloatingMeetingControl(QWidget):
    start_workday_requested = Signal()
    start_meeting_requested = Signal(str)
    end_meeting_requested = Signal()
    open_main_requested = Signal()
    visibility_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("floatingMeetingControl")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(280)

        self._drag_offset = None
        self._input_mode = False
        self._confirm_mode = False
        self._error_mode = False
        self._closing_from_app = False
        self._workday_active = False
        self._meeting_active = False
        self._recorder_enabled = False
        self._pipeline_running = False
        self._elapsed_text = "00:00:00"
        self._background_message = ""
        self._theme = "light"

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        self.title_label = QLabel("Быстрый созвон")
        self.title_label.setObjectName("floatingTitle")
        self.open_button = QPushButton("Открыть")
        self.open_button.setObjectName("floatingLinkButton")
        self.open_button.clicked.connect(self.open_main_requested.emit)
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("floatingCloseButton")
        self.close_button.clicked.connect(self.hide)
        header_layout.addWidget(self.title_label, 1)
        header_layout.addWidget(self.open_button)
        header_layout.addWidget(self.close_button)

        self.state_label = QLabel()
        self.state_label.setObjectName("floatingState")
        self.state_label.setWordWrap(True)
        self.detail_label = QLabel()
        self.detail_label.setObjectName("floatingDetail")
        self.detail_label.setWordWrap(True)
        self.timer_label = QLabel("00:00:00")
        self.timer_label.setObjectName("floatingTimer")
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.background_label = QLabel()
        self.background_label.setObjectName("floatingBackground")
        self.background_label.setWordWrap(True)

        self.title_input = QLineEdit()
        self.title_input.setObjectName("floatingInput")
        self.title_input.setPlaceholderText("Название встречи")
        self.title_input.returnPressed.connect(self._start_from_input)
        self.error_label = QLabel("Введите название встречи.")
        self.error_label.setObjectName("floatingError")
        self.error_label.setWordWrap(True)

        buttons_layout = QHBoxLayout()
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(8)
        self.secondary_button = QPushButton("Отмена")
        self.secondary_button.setObjectName("floatingSecondaryButton")
        self.secondary_button.clicked.connect(self._handle_secondary)
        self.primary_button = QPushButton()
        self.primary_button.clicked.connect(self._handle_primary)
        buttons_layout.addWidget(self.secondary_button)
        buttons_layout.addWidget(self.primary_button, 1)

        layout.addLayout(header_layout)
        layout.addWidget(self.state_label)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.timer_label)
        layout.addWidget(self.background_label)
        layout.addWidget(self.title_input)
        layout.addWidget(self.error_label)
        layout.addLayout(buttons_layout)
        self.setLayout(layout)
        self.apply_theme("light")
        self.update_state(
            workday_active=False,
            meeting_active=False,
            recorder_enabled=False,
            pipeline_running=False,
            background_message="",
        )

    def update_state(
        self,
        *,
        workday_active: bool,
        meeting_active: bool,
        recorder_enabled: bool,
        pipeline_running: bool,
        meeting_title: str = "",
        elapsed_text: str = "00:00:00",
        background_message: str = "",
    ) -> None:
        self._workday_active = workday_active
        self._meeting_active = meeting_active
        self._recorder_enabled = recorder_enabled
        self._pipeline_running = pipeline_running
        self._elapsed_text = elapsed_text
        self._background_message = background_message
        self._error_mode = False

        if meeting_active:
            self._input_mode = False
        else:
            self._confirm_mode = False

        if not workday_active:
            self._input_mode = False
            self._confirm_mode = False
            self._render_day_not_started()
        elif meeting_active:
            if self._confirm_mode:
                self._render_confirm_end()
            else:
                self._render_meeting_active(meeting_title)
        elif self._input_mode:
            self._render_title_input()
        else:
            self._render_ready_for_meeting()

        self._update_background_label()
        self.adjustSize()

    def close_from_app(self) -> None:
        self._closing_from_app = True
        self.close()

    def show_error(self, message: str) -> None:
        self._input_mode = False
        self._confirm_mode = False
        self._error_mode = True
        self.state_label.setText("Ошибка")
        self.detail_label.setText(message)
        self.background_label.hide()
        self.timer_label.hide()
        self.title_input.hide()
        self.error_label.hide()
        self.secondary_button.hide()
        self._set_primary_button("Открыть приложение", "floatingPrimaryButton")
        self.adjustSize()

    def _render_day_not_started(self) -> None:
        self.state_label.setText("Рабочий день не начат")
        self.detail_label.setText("Нажмите, чтобы начать рабочий день и подготовиться к созвонам.")
        self.timer_label.hide()
        self.title_input.hide()
        self.error_label.hide()
        self.secondary_button.hide()
        self._set_primary_button("Начать рабочий день", "floatingPrimaryButton")

    def _render_ready_for_meeting(self) -> None:
        self.state_label.setText("Готов к созвону")
        self.detail_label.setText("Можно быстро начать новый созвон.")
        self.timer_label.hide()
        self.title_input.hide()
        self.error_label.hide()
        self.secondary_button.hide()
        self._set_primary_button("Начать созвон", "floatingPrimaryButton")

    def _render_title_input(self) -> None:
        self.state_label.setText("Начать созвон")
        self.detail_label.setText("Введите короткое название встречи.")
        self.timer_label.hide()
        self.title_input.show()
        self.error_label.setVisible(False)
        self.secondary_button.setText("Отмена")
        self.secondary_button.show()
        self._set_primary_button("Начать", "floatingPrimaryButton")
        self.title_input.setFocus()

    def _render_meeting_active(self, meeting_title: str) -> None:
        self.state_label.setText("Созвон идет")
        details = "OBS пишет." if self._recorder_enabled else "OBS выключен или недоступен."
        if meeting_title:
            details = f"{meeting_title}\n{details}"
        self.detail_label.setText(details)
        self.timer_label.setText(self._elapsed_text)
        self.timer_label.show()
        self.title_input.hide()
        self.error_label.hide()
        self.secondary_button.hide()
        self._set_primary_button("Завершить созвон", "floatingDangerButton")

    def _render_confirm_end(self) -> None:
        self.state_label.setText("Завершить созвон?")
        self.detail_label.setText("Подтвердите завершение, чтобы избежать случайного клика.")
        self.timer_label.setText(self._elapsed_text)
        self.timer_label.show()
        self.title_input.hide()
        self.error_label.hide()
        self.secondary_button.setText("Нет")
        self.secondary_button.show()
        self._set_primary_button("Да", "floatingDangerButton")

    def _update_background_label(self) -> None:
        if self._pipeline_running:
            self.background_label.setText(
                self._background_message or "Обработка прошлой встречи выполняется в фоне."
            )
            self.background_label.show()
        else:
            self.background_label.hide()

    def _set_primary_button(self, text: str, object_name: str) -> None:
        self.primary_button.setText(text)
        if self.primary_button.objectName() != object_name:
            self.primary_button.setObjectName(object_name)
            self.primary_button.style().unpolish(self.primary_button)
            self.primary_button.style().polish(self.primary_button)

    def _handle_primary(self) -> None:
        if self._error_mode:
            self.open_main_requested.emit()
            return
        if not self._workday_active:
            self.start_workday_requested.emit()
            return
        if self._meeting_active:
            if self._confirm_mode:
                self._confirm_mode = False
                self.end_meeting_requested.emit()
            else:
                self._confirm_mode = True
                self._render_confirm_end()
                self._update_background_label()
                self.adjustSize()
            return
        if self._input_mode:
            self._start_from_input()
            return
        self._input_mode = True
        self._render_title_input()
        self._update_background_label()
        self.adjustSize()

    def _handle_secondary(self) -> None:
        if self._confirm_mode:
            self._confirm_mode = False
            self._render_meeting_active("")
        elif self._input_mode:
            self._input_mode = False
            self.title_input.clear()
            self._render_ready_for_meeting()
        self._update_background_label()
        self.adjustSize()

    def _start_from_input(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            self.error_label.show()
            return
        self.title_input.clear()
        self._input_mode = False
        self.start_meeting_requested.emit(title)

    def apply_theme(self, theme: str) -> None:
        self._theme = "dark" if theme == "dark" else "light"
        self.setStyleSheet(self._style(self._theme))

    def hideEvent(self, event) -> None:
        self.visibility_changed.emit(False)
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        self.visibility_changed.emit(True)
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        if self._closing_from_app:
            super().closeEvent(event)
            return
        event.ignore()
        self.hide()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    @staticmethod
    def _style(theme: str) -> str:
        colors = {
            "light": {
                "surface": "#fffdf8",
                "surface_alt": "#fff3e6",
                "border": "#ead8c6",
                "input_border": "#d9bfa8",
                "text": "#3a1408",
                "muted": "#7b4b35",
                "accent": "#ff6f1a",
                "danger": "#d9280f",
                "danger_text": "#991b1b",
            },
            "dark": {
                "surface": "#111827",
                "surface_alt": "#1f2937",
                "border": "#374151",
                "input_border": "#4b5563",
                "text": "#f9fafb",
                "muted": "#d1d5db",
                "accent": "#f97316",
                "danger": "#ef4444",
                "danger_text": "#fecaca",
            },
        }["dark" if theme == "dark" else "light"]
        return """
            QWidget#floatingMeetingControl {
                background: %(surface)s;
                color: %(text)s;
                border: 1px solid %(border)s;
                border-radius: 12px;
                font-family: "Segoe UI";
                font-size: 12px;
            }
            QLabel#floatingTitle {
                color: %(accent)s;
                font-weight: 800;
                font-size: 13px;
            }
            QLabel#floatingState {
                color: %(text)s;
                font-weight: 800;
                font-size: 15px;
            }
            QLabel#floatingDetail {
                color: %(muted)s;
            }
            QLabel#floatingTimer {
                color: %(danger)s;
                font-size: 22px;
                font-weight: 800;
                padding: 2px 0;
            }
            QLabel#floatingBackground {
                background: %(surface_alt)s;
                color: %(muted)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 6px 8px;
            }
            QLabel#floatingError {
                color: %(danger_text)s;
                font-weight: 700;
            }
            QLineEdit#floatingInput {
                background: %(surface)s;
                color: %(text)s;
                border: 1px solid %(input_border)s;
                border-radius: 6px;
                padding: 7px 9px;
                min-height: 30px;
            }
            QPushButton#floatingPrimaryButton {
                background: %(accent)s;
                color: #ffffff;
                border: 1px solid %(accent)s;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: 800;
            }
            QPushButton#floatingDangerButton {
                background: %(danger)s;
                color: #ffffff;
                border: 1px solid %(danger)s;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: 800;
            }
            QPushButton#floatingSecondaryButton,
            QPushButton#floatingLinkButton {
                background: %(surface)s;
                color: %(muted)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 6px 10px;
                font-weight: 700;
            }
            QPushButton#floatingCloseButton {
                background: %(surface)s;
                color: %(muted)s;
                border: 1px solid %(border)s;
                border-radius: 9px;
                min-width: 24px;
                min-height: 24px;
                max-width: 24px;
                max-height: 24px;
                font-weight: 900;
            }
        """ % colors


class MainWindow(QMainWindow):
    READINESS_CARD_EXPANDED_HEIGHT = 276
    READINESS_CARD_COLLAPSED_HEIGHT = 86
    READINESS_GRID_HEIGHT = 182
    DAY_OVERVIEW_CARD_MIN_HEIGHT = 226
    PIPELINE_STEPS = [
        ("recording", "OBS запись", "✓"),
        ("audio", "Аудио", "A"),
        ("transcription", "Транскрипция", "T"),
        ("summary", "Итоги", "Σ"),
    ]
    DAY_SUMMARY_PIPELINE_STEPS = [
        ("collect", "Сбор итогов встреч", "1"),
        ("check", "Проверка summary", "2"),
        ("generate", "Генерация итогов дня", "Σ"),
        ("links", "Ссылки на транскрипты", "T"),
    ]

    def __init__(
        self,
        storage: StorageService | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Meeting Day Recorder")
        self.resize(1100, 720)
        self.config = load_config()
        self.current_theme = self._configured_theme()
        self.nav_buttons: dict[int, QPushButton] = {}
        self.pipeline_running = False
        self.pipeline_completed = False
        self.pipeline_meeting_folder: Path | None = None
        self.floating_background_message = ""
        self._floating_control_positioned = False
        self.processing_queue: list[Path] = []
        self.pipeline_thread: QThread | None = None
        self.pipeline_worker: MeetingPipelineWorker | None = None
        self.day_summary_running = False
        self.day_summary_pending = False
        self.day_summary_force_pending = False
        self.day_summary_day_folder: Path | None = None
        self.day_summary_thread: QThread | None = None
        self.day_summary_worker: DaySummaryPipelineWorker | None = None
        self.recorder = recorder or (
            storage.recorder if storage else create_recorder(self.config["obs"])
        )
        self.storage = storage or StorageService(
            Path(self.config["storage"]["root"]),
            self.recorder,
            transcriber=create_transcriber(self._transcription_runtime_config()),
            summarizer=create_summarizer(self._summary_runtime_config()),
        )
        self.storage.load_today_state()
        self.readiness_labels: dict[str, QLabel] = {}
        self.readiness_badges: dict[str, QLabel] = {}
        self.readiness_tiles: dict[str, QWidget] = {}
        self.pipeline_labels: dict[str, QLabel] = {}
        self.pipeline_badges: dict[str, QLabel] = {}
        self.pipeline_messages: dict[str, QLabel] = {}
        self.pipeline_step_titles: dict[str, QLabel] = {}
        self.day_summary_pipeline_labels: dict[str, QLabel] = {}
        self.day_summary_pipeline_messages: dict[str, QLabel] = {}
        self.day_summary_pipeline_step_titles: dict[str, QLabel] = {}
        self.selected_workday_meeting_folder: Path | None = None
        self.workday_day_summary_expanded = False
        self.selected_review_meeting_folder: Path | None = None
        self.review_day_summary_selected = False
        self.workday_action_mode: str | None = None
        self.workday_meeting_cards: dict[Path, ClickableFrame] = {}
        self.review_meeting_cards: dict[Path, ClickableFrame] = {}
        self._apply_app_style()

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.pages.addWidget(self._create_workday_page())
        self.pages.addWidget(self._create_review_page())
        self.pages.addWidget(self._create_archive_page())
        self.pages.addWidget(self._create_settings_page())
        self.pages.addWidget(self._create_help_page())
        self.pages.currentChanged.connect(self._refresh_navigation_state)

        root_layout = QHBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._create_navigation())

        content = QWidget()
        content.setObjectName("content")
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(24, 20, 20, 20)
        content_layout.setSpacing(14)
        content_layout.addWidget(self.pages, 1)
        content.setLayout(content_layout)
        root_layout.addWidget(content, 1)

        container = QWidget()
        container.setObjectName("appRoot")
        container.setLayout(root_layout)
        self.setCentralWidget(container)
        self.start_meeting_overlay = StartMeetingOverlay(self.recorder, container)
        self.start_meeting_overlay.apply_theme(self.current_theme)
        self.start_meeting_overlay.submitted.connect(self._start_meeting_with_title)
        self.start_meeting_overlay.canceled.connect(
            lambda: self.status_label.setText("Создание встречи отменено.")
        )
        self._resize_start_meeting_overlay()
        self.floating_control = FloatingMeetingControl()
        self.floating_control.apply_theme(self._effective_floating_theme())
        self.floating_control.start_workday_requested.connect(self.start_workday)
        self.floating_control.start_meeting_requested.connect(self._start_meeting_from_floating)
        self.floating_control.end_meeting_requested.connect(self.end_meeting)
        self.floating_control.open_main_requested.connect(self._show_main_window_from_floating)
        self.floating_control.visibility_changed.connect(self._on_floating_visibility_changed)
        self._refresh_navigation_state(self.pages.currentIndex())
        self.refresh_status()
        self.refresh_buttons()
        self.active_call_timer = QTimer(self)
        self.active_call_timer.setInterval(1000)
        self.active_call_timer.timeout.connect(self._refresh_active_call_display)
        self.active_call_timer.start()
        self.show_floating_control()

    def _transcription_runtime_config(self) -> dict[str, object]:
        config = dict(self.config["transcription"])
        if not str(config.get("env_file") or "").strip():
            config["env_file"] = str(self.config.get("secrets", {}).get("env_file") or "")
        return config

    def _summary_runtime_config(self) -> dict[str, object]:
        config = dict(self.config["summary"])
        if not str(config.get("env_file") or "").strip():
            config["env_file"] = str(self.config.get("secrets", {}).get("env_file") or "")
        return config

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_start_meeting_overlay()

    def _resize_start_meeting_overlay(self) -> None:
        if not hasattr(self, "start_meeting_overlay"):
            return
        parent = self.start_meeting_overlay.parentWidget()
        if parent is not None:
            self.start_meeting_overlay.setGeometry(parent.rect())

    def show_floating_control(self) -> None:
        if not hasattr(self, "floating_control"):
            return
        self._refresh_floating_control()
        if not self._floating_control_positioned:
            screen = self.screen()
            if screen is not None:
                available = screen.availableGeometry()
                self.floating_control.adjustSize()
                self.floating_control.move(
                    max(available.left(), available.right() - self.floating_control.width() - 24),
                    available.top() + 80,
                )
            self._floating_control_positioned = True
        self.floating_control.show()
        self.floating_control.raise_()

    def hide_floating_control(self) -> None:
        if hasattr(self, "floating_control"):
            self.floating_control.hide()

    def toggle_floating_control(self) -> None:
        if not hasattr(self, "floating_control"):
            return
        if self.floating_control.isVisible():
            self.hide_floating_control()
        else:
            self.show_floating_control()

    def _on_floating_visibility_changed(self, visible: bool) -> None:
        if hasattr(self, "toggle_floating_button"):
            self.toggle_floating_button.setText(
                "Скрыть плавающую кнопку" if visible else "Показать плавающую кнопку"
            )

    def _show_main_window_from_floating(self) -> None:
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _start_meeting_from_floating(self, title: str) -> None:
        self._start_meeting_with_title(title)
        self._refresh_floating_control()

    def _refresh_floating_control(self) -> None:
        if not hasattr(self, "floating_control"):
            return
        meeting_title = ""
        elapsed_text = "00:00:00"
        if self.storage.meeting_active and self.storage.active_meeting_folder is not None:
            metadata = self.storage.read_meeting_metadata(self.storage.active_meeting_folder)
            meeting_title = str(metadata.get("title") or self.storage.active_meeting_folder.name)
            elapsed_text = self._elapsed_text(metadata.get("started_at"))
        self.floating_control.update_state(
            workday_active=self.storage.workday_active,
            meeting_active=self.storage.meeting_active,
            recorder_enabled=bool(getattr(self.recorder, "enabled", False)),
            pipeline_running=self._has_processing_work(),
            meeting_title=meeting_title,
            elapsed_text=elapsed_text,
            background_message=self.floating_background_message,
        )

    def _create_navigation(self) -> QWidget:
        navigation = QWidget()
        navigation.setObjectName("sidebar")
        navigation.setFixedWidth(230)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 18, 0, 18)
        layout.setSpacing(0)

        brand = QLabel("●  Meeting Day\n    Recorder")
        brand.setObjectName("brand")
        layout.addWidget(brand)

        self._add_nav_button(layout, 0, "Рабочий день", lambda: self.pages.setCurrentIndex(0))
        self._add_nav_button(layout, 1, "Ревью", self.open_review)
        self._add_nav_button(layout, 2, "Архив", lambda: self.pages.setCurrentIndex(2))
        self._add_nav_button(layout, 3, "Настройки", lambda: self.pages.setCurrentIndex(3))
        self._add_nav_button(layout, 4, "Справка", lambda: self.pages.setCurrentIndex(4))
        layout.addStretch()
        self.toggle_floating_button = QPushButton("Скрыть плавающую кнопку")
        self.toggle_floating_button.setObjectName("sidebarActionButton")
        self.toggle_floating_button.clicked.connect(self.toggle_floating_control)
        layout.addWidget(self.toggle_floating_button)
        navigation.setLayout(layout)
        return navigation

    def _add_nav_button(
        self,
        layout: QVBoxLayout,
        index: int,
        label: str,
        callback: Callable[[], None],
    ) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("navButton")
        button.setCheckable(True)
        button.clicked.connect(callback)
        self.nav_buttons[index] = button
        layout.addWidget(button)
        return button

    def _refresh_navigation_state(self, current_index: int) -> None:
        for index, button in self.nav_buttons.items():
            button.setChecked(index == current_index)

    def _create_page_header(self, title: str, subtitle: str) -> QWidget:
        header = QWidget()
        header.setObjectName("pageHeader")
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title_block = QWidget()
        title_layout = QVBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("pageSubtitle")
        subtitle_label.setWordWrap(True)
        title_layout.addWidget(title_label)
        title_layout.addWidget(subtitle_label)
        title_block.setLayout(title_layout)

        layout.addWidget(title_block, 1)
        header.setLayout(layout)
        return header

    @staticmethod
    def _create_placeholder_page(title: str, message: str) -> QWidget:
        page = QWidget()
        page.setObjectName("pageSurface")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout()
        layout.setSpacing(14)
        layout.setContentsMargins(0, 0, 0, 0)

        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        message_label = QLabel(message)
        message_label.setObjectName("emptyState")
        message_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(message_label)
        layout.addStretch()
        page.setLayout(layout)
        return page

    @staticmethod
    def _prepare_page_surface(page: QWidget) -> QWidget:
        page.setObjectName("pageSurface")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        return page

    @staticmethod
    def _create_page_scroll_area(object_name: str, page: QWidget) -> QScrollArea:
        scroll_area = QScrollArea()
        scroll_area.setObjectName(object_name)
        scroll_area.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll_area.viewport().setObjectName("scrollViewport")
        scroll_area.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(MainWindow._prepare_page_surface(page))
        return scroll_area

    def _configured_theme(self) -> str:
        theme = str(self.config.get("ui", {}).get("theme", "light")).strip().lower()
        return "dark" if theme == "dark" else "light"

    def _configured_floating_theme(self) -> str:
        theme = str(self.config.get("ui", {}).get("floating_theme", "inherit")).strip().lower()
        if theme not in {"inherit", "light", "dark"}:
            return "inherit"
        return theme

    def _effective_floating_theme(self) -> str:
        floating_theme = self._configured_floating_theme()
        if floating_theme == "inherit":
            return self._configured_theme()
        return floating_theme

    def _theme_palette(self) -> dict[str, str]:
        if getattr(self, "current_theme", "light") == "dark":
            return {
                "bg": "#0f172a",
                "surface": "#111827",
                "surface_alt": "#1f2937",
                "surface_soft": "#182235",
                "surface_warm": "#1f2937",
                "border": "#374151",
                "border_soft": "#263244",
                "text": "#f9fafb",
                "muted": "#d1d5db",
                "hint": "#9ca3af",
                "accent": "#f97316",
                "accent_hover": "#ea580c",
                "danger": "#ef4444",
                "danger_hover": "#dc2626",
                "disabled_bg": "#1f2937",
                "disabled_text": "#6b7280",
                "input_bg": "#0b1220",
                "input_border": "#4b5563",
                "inline_status_bg": "#111827",
                "pipeline_icon_bg": "#273244",
            }
        return {
            "bg": "#f6efe6",
            "surface": "#fffdf8",
            "surface_alt": "#fff8ef",
            "surface_soft": "#fff3e6",
            "surface_warm": "#fff3e6",
            "border": "#ead8c6",
            "border_soft": "#f1e5d8",
            "text": "#3a1408",
            "muted": "#7b4b35",
            "hint": "#8a6a58",
            "accent": "#ff6f1a",
            "accent_hover": "#f45a00",
            "danger": "#d9280f",
            "danger_hover": "#b91c1c",
            "disabled_bg": "#f3e8dc",
            "disabled_text": "#b49a89",
            "input_bg": "#fffdf8",
            "input_border": "#ead8c6",
            "inline_status_bg": "#f3f4f6",
            "pipeline_icon_bg": "#f3e8dc",
        }

    def _apply_app_style(self) -> None:
        self.current_theme = self._configured_theme()
        colors = self._theme_palette()
        self.setStyleSheet(
            """
            QMainWindow {
                background: %(bg)s;
                color: %(text)s;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QWidget#appRoot,
            QWidget#content,
            QStackedWidget#pages,
            QWidget#pageSurface,
            QWidget#scrollViewport {
                background: %(bg)s;
            }
            QLabel {
                background: transparent;
                color: %(text)s;
            }
            QWidget#sidebar {
                background: %(surface)s;
                border-right: 1px solid %(border)s;
            }
            QLabel#brand {
                color: %(accent)s;
                font-size: 18px;
                font-weight: 800;
                padding: 2px 14px 18px 14px;
                border-bottom: 1px solid %(border_soft)s;
            }
            QPushButton#navButton {
                background: transparent;
                color: %(muted)s;
                border: 0;
                border-bottom: 1px solid %(border_soft)s;
                border-radius: 0;
                padding: 14px 18px;
                text-align: left;
                font-weight: 700;
            }
            QPushButton#navButton:hover {
                background: %(surface_alt)s;
                color: %(accent)s;
            }
            QPushButton#navButton:checked {
                background: %(surface_soft)s;
                color: %(accent)s;
                border-left: 3px solid %(accent)s;
                padding-left: 15px;
            }
            QPushButton#sidebarActionButton {
                background: %(surface_alt)s;
                color: %(muted)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 8px 12px;
                margin: 12px 14px 0 14px;
                font-weight: 700;
                text-align: left;
            }
            QPushButton#sidebarActionButton:hover {
                color: %(accent)s;
                border-color: %(accent)s;
            }
            QLabel#pageTitle {
                color: %(text)s;
                font-size: 26px;
                font-weight: 800;
            }
            QLabel#pageSubtitle {
                color: %(hint)s;
            }
            QLabel#emptyState {
                background: %(surface)s;
                color: %(muted)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 18px;
            }
            QWidget#card {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
            }
            QLabel#cardTitle {
                color: %(text)s;
                font-size: 14px;
                font-weight: 800;
            }
            QLabel#sectionHint {
                color: %(hint)s;
            }
            QLabel#heroValue {
                color: %(text)s;
                font-size: 18px;
                font-weight: 800;
            }
            QFrame#overviewInnerPanel {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                min-height: 150px;
            }
            QFrame#activeCallInnerPanel {
                background: %(surface_warm)s;
                border: 1px solid %(accent)s;
                border-radius: 8px;
                min-height: 150px;
            }
            QLabel#callTimer {
                color: %(danger)s;
                font-size: 28px;
                font-weight: 800;
            }
            QFrame#meetingCard {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
            }
            QFrame#activeMeetingCard {
                background: %(surface_warm)s;
                border: 1px solid %(accent)s;
                border-radius: 8px;
            }
            QLabel#meetingHeaderLabel {
                background: transparent;
                border: 0;
                color: %(text)s;
                font-size: 14px;
                font-weight: 800;
                padding: 0;
                min-height: 22px;
            }
            QFrame#readinessTile {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                min-height: 82px;
                max-height: 82px;
                min-width: 300px;
            }
            QLabel#readinessTitle {
                color: %(text)s;
                font-weight: 800;
            }
            QLabel#readinessMessage {
                color: %(hint)s;
                min-height: 30px;
            }
            QLabel#statusBadge {
                border-radius: 10px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 800;
            }
            QLabel#pipelineStepTitle {
                color: %(text)s;
                font-weight: 800;
            }
            QFrame#pipelineStepCard {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                min-height: 58px;
            }
            QLabel#pipelineIcon {
                background: %(pipeline_icon_bg)s;
                color: %(muted)s;
                border-radius: 8px;
                font-weight: 700;
            }
            QLabel#pipelineMessage {
                color: %(hint)s;
            }
            QLabel#inlineStatus {
                background: %(inline_status_bg)s;
                color: %(text)s;
                border-radius: 6px;
                padding: 8px;
            }
            QPushButton {
                background: %(surface)s;
                color: %(text)s;
                border: 1px solid %(border)s;
                border-radius: 6px;
                padding: 8px 12px;
                min-height: 28px;
                font-weight: 600;
            }
            QPushButton:hover {
                border-color: %(accent)s;
                color: %(accent)s;
            }
            QPushButton:disabled {
                background: %(disabled_bg)s;
                color: %(disabled_text)s;
                border-color: %(border)s;
            }
            QPushButton#primaryButton {
                background: %(accent)s;
                color: #ffffff;
                border: 1px solid %(accent)s;
            }
            QPushButton#primaryButton:hover {
                background: %(accent_hover)s;
                color: #ffffff;
                border-color: %(accent_hover)s;
            }
            QPushButton#dangerButton {
                background: %(danger)s;
                color: #ffffff;
                border: 1px solid %(danger)s;
            }
            QPushButton#dangerButton:hover {
                background: %(danger_hover)s;
                color: #ffffff;
                border-color: %(danger_hover)s;
            }
            QPushButton#headerPrimaryButton {
                background: %(accent)s;
                color: #ffffff;
                border: 1px solid %(accent)s;
                border-radius: 6px;
                padding: 4px 12px;
                min-height: 24px;
                max-height: 34px;
                font-weight: 700;
            }
            QPushButton#headerPrimaryButton:hover {
                background: %(accent_hover)s;
                color: #ffffff;
                border-color: %(accent_hover)s;
            }
            QPushButton#headerButton {
                background: %(surface)s;
                color: %(muted)s;
                border: 1px solid %(border)s;
                border-radius: 6px;
                padding: 4px 12px;
                min-height: 24px;
                max-height: 34px;
                font-weight: 600;
            }
            QLineEdit,
            QComboBox,
            QSpinBox,
            QPlainTextEdit,
            QTextBrowser {
                background: %(input_bg)s;
                color: %(text)s;
                border: 1px solid %(input_border)s;
                border-radius: 8px;
                padding: 8px;
            }
            QCheckBox {
                color: %(text)s;
                spacing: 8px;
            }
            QFormLayout QLabel {
                color: %(text)s;
            }
            QScrollArea {
                background: %(bg)s;
                border: none;
            }
            QScrollArea#workdayScrollArea,
            QScrollArea#settingsScrollArea {
                background: %(bg)s;
            }
            QScrollBar:vertical {
                background: %(bg)s;
                width: 12px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: %(border)s;
                border-radius: 6px;
                min-height: 24px;
            }
            QTabWidget::pane {
                border: 1px solid %(border)s;
                border-radius: 8px;
                background: %(surface)s;
            }
            QTabBar::tab {
                background: %(disabled_bg)s;
                color: %(hint)s;
                padding: 8px 12px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: %(surface)s;
                color: %(text)s;
                font-weight: 700;
            }
            """ % colors
        )

    def _apply_theme_settings(self) -> None:
        self._apply_app_style()
        if hasattr(self, "start_meeting_overlay"):
            self.start_meeting_overlay.apply_theme(self.current_theme)
        if hasattr(self, "floating_control"):
            self.floating_control.apply_theme(self._effective_floating_theme())
        if hasattr(self, "readiness_labels"):
            for label in self.readiness_labels.values():
                self._apply_status_style(label, "wait")
        if hasattr(self, "readiness_badges"):
            for badge in self.readiness_badges.values():
                self._apply_badge_style(badge, self._badge_state_from_text(badge.text()))
        if hasattr(self, "status_label"):
            self.refresh_status()
            self.refresh_buttons()

    @staticmethod
    def _create_card(
        title: str,
        body_layout,
        header_actions: list[QWidget] | None = None,
        title_badges: list[QWidget] | None = None,
    ) -> QWidget:
        card = QWidget()
        card.setObjectName("card")
        layout = QVBoxLayout()
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(12)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        header_layout.addWidget(title_label)
        for badge in title_badges or []:
            header_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addStretch(1)
        for action in header_actions or []:
            header_layout.addWidget(action, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header_layout)
        layout.addLayout(body_layout)

        card.setLayout(layout)
        return card

    def _create_readiness_card(self, body_layout) -> QWidget:
        card = QWidget()
        card.setObjectName("card")
        card.setFixedHeight(self.READINESS_CARD_EXPANDED_HEIGHT)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout()
        layout.setContentsMargins(18, 10, 18, 16)
        layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        title_block = QWidget()
        title_layout = QVBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(4)
        title_label = QLabel("Готовность системы")
        title_label.setObjectName("cardTitle")
        subtitle_label = QLabel("Проверяется до старта дня и перед важными записями.")
        subtitle_label.setObjectName("sectionHint")
        subtitle_label.setWordWrap(True)
        title_layout.addWidget(title_label)
        title_layout.addWidget(subtitle_label)
        title_block.setLayout(title_layout)

        header_layout.addWidget(title_block, 1, Qt.AlignmentFlag.AlignTop)
        header_layout.addStretch(1)
        self.check_readiness_button = self._add_button(
            header_layout,
            "Проверить готовность",
            self.check_readiness,
            "headerPrimaryButton",
        )
        self.toggle_readiness_button = self._add_button(
            header_layout,
            "Свернуть",
            self._toggle_readiness_card,
            "headerButton",
        )
        self.check_readiness_button.setFixedHeight(34)
        self.toggle_readiness_button.setFixedHeight(34)
        header_layout.setAlignment(self.check_readiness_button, Qt.AlignmentFlag.AlignTop)
        header_layout.setAlignment(self.toggle_readiness_button, Qt.AlignmentFlag.AlignTop)

        self.readiness_body = QWidget()
        self.readiness_body.setFixedHeight(self.READINESS_GRID_HEIGHT)
        self.readiness_body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.readiness_body.setLayout(body_layout)
        self.readiness_card = card

        layout.addLayout(header_layout)
        layout.addWidget(self.readiness_body, 0, Qt.AlignmentFlag.AlignTop)
        card.setLayout(layout)
        return card

    def _toggle_readiness_card(self) -> None:
        is_collapsed = self.readiness_body.isHidden()
        self.readiness_body.setVisible(is_collapsed)
        self.readiness_card.setFixedHeight(
            self.READINESS_CARD_EXPANDED_HEIGHT
            if is_collapsed
            else self.READINESS_CARD_COLLAPSED_HEIGHT
        )
        self.toggle_readiness_button.setText("Свернуть" if is_collapsed else "Развернуть")

    def _toggle_meetings_card(self) -> None:
        is_collapsed = self.meetings_body.isHidden()
        self.meetings_body.setVisible(is_collapsed)
        self.toggle_meetings_button.setText("Свернуть" if is_collapsed else "Развернуть")

    def _create_pipeline_step_card(
        self,
        key: str,
        title: str,
        icon: str,
        labels: dict[str, QLabel] | None = None,
        messages: dict[str, QLabel] | None = None,
        titles: dict[str, QLabel] | None = None,
    ) -> QWidget:
        card = QFrame()
        card.setObjectName("pipelineStepCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setFrameShadow(QFrame.Shadow.Plain)
        layout = QHBoxLayout()
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        icon_label = QLabel(icon)
        icon_label.setObjectName("pipelineIcon")
        icon_label.setFixedSize(28, 28)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text_block = QWidget()
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("pipelineStepTitle")
        message_label = QLabel()
        message_label.setObjectName("pipelineMessage")
        message_label.setWordWrap(True)
        text_layout.addWidget(title_label)
        text_layout.addWidget(message_label)
        text_block.setLayout(text_layout)

        status_label = QLabel("Ожидает")
        status_label.setObjectName("statusBadge")
        status_label.setMinimumWidth(72)
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_badge_style(status_label, "wait")

        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(text_block, 1)
        layout.addWidget(status_label, 0, Qt.AlignmentFlag.AlignTop)
        card.setLayout(layout)

        target_titles = titles if titles is not None else self.pipeline_step_titles
        target_labels = labels if labels is not None else self.pipeline_labels
        target_messages = messages if messages is not None else self.pipeline_messages
        target_titles[key] = title_label
        target_labels[key] = status_label
        if labels is None:
            self.pipeline_badges[key] = status_label
        target_messages[key] = message_label
        return card

    def _create_readiness_tile(self, component: str) -> QWidget:
        tile = QFrame()
        tile.setObjectName("readinessTile")
        tile.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tile.setFrameShape(QFrame.Shape.StyledPanel)
        tile.setFrameShadow(QFrame.Shadow.Plain)
        tile.setFixedHeight(82)
        tile.setMinimumWidth(300)
        tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        tile_layout = QVBoxLayout()
        tile_layout.setContentsMargins(12, 10, 12, 10)
        tile_layout.setSpacing(7)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel(component)
        title_label.setObjectName("readinessTitle")
        title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        badge_label = QLabel("Не проверено")
        badge_label.setObjectName("statusBadge")
        badge_label.setMinimumWidth(32)
        badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_badge_style(badge_label, "wait")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(badge_label)

        message_label = QLabel("Нажмите «Проверить готовность».")
        message_label.setObjectName("readinessMessage")
        message_label.setWordWrap(True)
        message_label.setMinimumHeight(30)
        message_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.readiness_tiles[component] = tile
        self.readiness_badges[component] = badge_label
        self.readiness_labels[component] = message_label
        tile_layout.addLayout(header_layout)
        tile_layout.addWidget(message_label)
        tile.setLayout(tile_layout)
        return tile

    def _refresh_workday_meetings(self) -> None:
        scroll_bar = (
            self.workday_scroll_area.verticalScrollBar()
            if hasattr(self, "workday_scroll_area")
            else None
        )
        scroll_value = scroll_bar.value() if scroll_bar is not None else 0
        self._clear_layout(self.meetings_cards_layout)
        self.pipeline_labels = {}
        self.pipeline_badges = {}
        self.pipeline_messages = {}
        self.pipeline_step_titles = {}
        self.day_summary_pipeline_labels = {}
        self.day_summary_pipeline_messages = {}
        self.day_summary_pipeline_step_titles = {}
        self.workday_meeting_cards = {}
        day_folder = self.storage.get_today_day_folder()
        has_day_summary = self.storage.day_summary_exists(day_folder)
        if has_day_summary and day_folder is not None:
            self.meetings_cards_layout.addWidget(
                self._create_day_summary_workday_card(day_folder, self.workday_day_summary_expanded)
            )
        meeting_folders = self._today_meeting_folders_newest_first()
        if not meeting_folders:
            self.selected_workday_meeting_folder = None
            if scroll_bar is not None:
                QTimer.singleShot(0, lambda: scroll_bar.setValue(scroll_value))
            return

        if (
            self.selected_workday_meeting_folder is not None
            and self.selected_workday_meeting_folder not in meeting_folders
        ):
            self.selected_workday_meeting_folder = None

        for meeting_folder in meeting_folders:
            expanded = meeting_folder == self.selected_workday_meeting_folder
            self.meetings_cards_layout.addWidget(
                self._create_meeting_card(meeting_folder, expanded)
            )
        if scroll_bar is not None:
            QTimer.singleShot(0, lambda: scroll_bar.setValue(scroll_value))

    def _create_day_summary_workday_card(self, day_folder: Path, expanded: bool) -> QWidget:
        metadata = self.storage.read_day_summary_metadata(day_folder)
        card = ClickableFrame()
        card.setObjectName("activeMeetingCard" if expanded else "meetingCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setToolTip("Нажмите, чтобы раскрыть pipeline итогов дня.")
        card.clicked.connect(self.select_workday_day_summary)
        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_label = QLabel("Итоги дня")
        header_label.setObjectName("meetingHeaderLabel")
        header_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge = QLabel()
        badge.setObjectName("statusBadge")
        badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge_text, badge_state = self._day_summary_badge(metadata)
        badge.setText(badge_text)
        self._apply_badge_style(badge, badge_state)
        header_layout.addWidget(header_label, 1)
        header_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        card_layout.addLayout(header_layout)

        detail = QLabel(self._day_summary_detail_text(metadata))
        detail.setObjectName("sectionHint")
        detail.setWordWrap(True)
        detail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        card_layout.addWidget(detail)

        if expanded:
            actions_layout = QHBoxLayout()
            actions_layout.setSpacing(8)
            update_button = self._add_button(
                actions_layout,
                "Обновить итоги дня",
                self.update_day_summary,
                "primaryButton",
            )
            open_day_button = self._add_button(
                actions_layout,
                "Открыть папку дня",
                self.open_day_folder,
            )
            actions_layout.addStretch(1)
            update_button.setEnabled(
                not self.day_summary_running
                and not self.storage.has_unfinished_meeting_processing(day_folder)
            )
            open_day_button.setEnabled(True)
            card_layout.addLayout(actions_layout)

            pipeline_hint = QLabel(
                "Pipeline итогов дня: сбор summary встреч, генерация выжимки и ссылки на transcript."
            )
            pipeline_hint.setObjectName("sectionHint")
            pipeline_hint.setWordWrap(True)
            pipeline_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            card_layout.addWidget(pipeline_hint)
            pipeline_layout = QVBoxLayout()
            pipeline_layout.setSpacing(8)
            pipeline_layout.setContentsMargins(0, 0, 0, 0)
            for key, title, icon in self.DAY_SUMMARY_PIPELINE_STEPS:
                pipeline_layout.addWidget(
                    self._create_pipeline_step_card(
                        key,
                        title,
                        icon,
                        self.day_summary_pipeline_labels,
                        self.day_summary_pipeline_messages,
                        self.day_summary_pipeline_step_titles,
                    )
                )
            card_layout.addLayout(pipeline_layout)
            self._refresh_day_summary_pipeline_from_metadata(metadata)

        card.setLayout(card_layout)
        return card

    def select_workday_day_summary(self) -> None:
        self.workday_day_summary_expanded = not self.workday_day_summary_expanded
        if self.workday_day_summary_expanded:
            self.selected_workday_meeting_folder = None
        self._refresh_workday_meetings()
        self.refresh_buttons()

    def _create_meeting_card(self, meeting_folder: Path, expanded: bool) -> QWidget:
        metadata = self.storage.read_meeting_metadata(meeting_folder)
        card = ClickableFrame()
        card.setObjectName(
            "activeMeetingCard"
            if metadata.get("status") == "active"
            else "meetingCard"
        )
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setToolTip("Нажмите, чтобы раскрыть pipeline этой встречи.")
        card.clicked.connect(lambda folder=meeting_folder: self.select_workday_meeting(folder))
        self.workday_meeting_cards[meeting_folder] = card
        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_label = QLabel(self._meeting_header_text(meeting_folder, metadata))
        header_label.setObjectName("meetingHeaderLabel")
        header_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge = QLabel()
        badge.setObjectName("statusBadge")
        badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge_text, badge_state = self._meeting_badge(metadata)
        badge.setText(badge_text)
        self._apply_badge_style(badge, badge_state)
        header_layout.addWidget(header_label, 1)
        header_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        card_layout.addLayout(header_layout)

        detail = QLabel(self._meeting_detail_text(metadata))
        detail.setObjectName("sectionHint")
        detail.setWordWrap(True)
        detail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        card_layout.addWidget(detail)

        if expanded:
            actions_layout = QHBoxLayout()
            actions_layout.setSpacing(8)
            reprocess_button = self._add_button(
                actions_layout,
                "Повторить обработку",
                lambda checked=False, folder=meeting_folder: self.reprocess_meeting(folder),
                "primaryButton",
            )
            open_meeting_button = self._add_button(
                actions_layout,
                "Открыть папку встречи",
                lambda checked=False, folder=meeting_folder: self.open_meeting_folder(folder),
            )
            open_day_button = self._add_button(
                actions_layout,
                "Открыть папку дня",
                self.open_day_folder,
            )
            actions_layout.addStretch(1)
            reprocess_button.setEnabled(self._can_reprocess_meeting(meeting_folder, metadata))
            open_meeting_button.setEnabled(True)
            open_day_button.setEnabled(self.storage.get_today_day_folder() is not None)
            card_layout.addLayout(actions_layout)
            pipeline_hint = QLabel(
                "Pipeline этой встречи: запись, audio.wav, transcript и итоги."
            )
            pipeline_hint.setObjectName("sectionHint")
            pipeline_hint.setWordWrap(True)
            pipeline_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            card_layout.addWidget(pipeline_hint)
            pipeline_layout = QVBoxLayout()
            pipeline_layout.setSpacing(8)
            pipeline_layout.setContentsMargins(0, 0, 0, 0)
            for key, title, icon in self.PIPELINE_STEPS:
                pipeline_layout.addWidget(self._create_pipeline_step_card(key, title, icon))
            card_layout.addLayout(pipeline_layout)
            self._refresh_pipeline_from_metadata(metadata)

        card.setLayout(card_layout)
        return card

    def select_workday_meeting(self, meeting_folder: Path) -> None:
        self.selected_workday_meeting_folder = (
            None
            if self.selected_workday_meeting_folder == meeting_folder
            else meeting_folder
        )
        if self.selected_workday_meeting_folder is not None:
            self.workday_day_summary_expanded = False
        self._refresh_workday_meetings()
        self.refresh_buttons()

    def _can_reprocess_meeting(
        self,
        meeting_folder: Path,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        metadata = metadata or self.storage.read_meeting_metadata(meeting_folder)
        return (
            metadata.get("status") == "ended"
            and not self.day_summary_running
            and meeting_folder != self.pipeline_meeting_folder
            and meeting_folder not in self.processing_queue
        )

    def reprocess_meeting(self, meeting_folder: Path) -> None:
        metadata = self.storage.read_meeting_metadata(meeting_folder)
        if not self._can_reprocess_meeting(meeting_folder, metadata):
            self.status_label.setText(
                "Эту встречу сейчас нельзя повторно обработать: она активна, уже находится в очереди "
                "или сейчас обновляются итоги дня."
            )
            return
        self.storage.mark_meeting_for_reprocessing(meeting_folder)
        self._enqueue_meeting_processing(meeting_folder)
        self.status_label.setText(f"Повторная обработка встречи добавлена в очередь: {meeting_folder.name}")
        self._refresh_after_lifecycle_change()

    def _today_meeting_folders_newest_first(self) -> list[Path]:
        return sorted(
            self.storage.list_today_meeting_folders(),
            key=self._meeting_sort_key,
            reverse=True,
        )

    def _meeting_sort_key(self, meeting_folder: Path) -> tuple[str, str]:
        metadata = self.storage.read_meeting_metadata(meeting_folder)
        started_at = str(metadata.get("started_at") or "")
        return started_at, meeting_folder.name

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            if child_layout is not None:
                MainWindow._clear_layout(child_layout)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _meeting_header_text(self, meeting_folder: Path, metadata: dict[str, object]) -> str:
        title = str(metadata.get("title") or meeting_folder.name)
        started_at = self._short_time(metadata.get("started_at"))
        duration = self._duration_text(metadata)
        return f"{started_at}   {title}   {duration}"

    def _meeting_detail_text(self, metadata: dict[str, object]) -> str:
        parts = []
        if metadata.get("transcription_quality") == "suspect":
            parts.append("транскрипция требует проверки")
        if metadata.get("summary_status") == "draft_created":
            parts.append("итоги готовы")
        elif metadata.get("summary_status") in {"disabled", "skipped"}:
            parts.append("итоги пропущены")
        elif metadata.get("processing_status") == "running":
            parts.append("обработка выполняется")
        elif metadata.get("processing_status") == "pending":
            parts.append("ожидает обработки")
        if metadata.get("transcription_status") == "completed":
            parts.append("transcript готов")
        elif metadata.get("transcription_status") == "skipped":
            parts.append("transcript пропущен")
        return " · ".join(parts) if parts else "Детали встречи доступны после раскрытия карточки."

    def _meeting_badge(self, metadata: dict[str, object]) -> tuple[str, str]:
        if metadata.get("status") == "active":
            if metadata.get("recording_status") == "recording":
                return "Идет запись", "active"
            return "Активна", "active"
        if metadata.get("processing_status") == "running":
            return "Обработка", "active"
        if metadata.get("processing_status") == "pending":
            return "В очереди", "wait"
        if metadata.get("transcription_quality") == "suspect":
            return "Требует проверки", "error"
        if metadata.get("summary_status") == "draft_created":
            return "Итоги готовы", "ok"
        return "Завершена", "ok"

    def _day_summary_detail_text(self, metadata: dict[str, object]) -> str:
        included = metadata.get("included_meetings")
        count = len(included) if isinstance(included, list) else 0
        status = metadata.get("day_summary_status")
        if status == "waiting_for_meetings":
            return "Итоги дня ждут завершения обработки встреч."
        if status == "running":
            return "Идет сбор итогов встреч и генерация выжимки дня."
        if status == "draft_created":
            return f"Итоги дня готовы. Включено встреч: {count}."
        if status == "up_to_date":
            return f"Итоги дня актуальны. Включено встреч: {count}."
        if status in {"failed", "openai_unavailable"}:
            return str(metadata.get("day_summary_error") or "Итоги дня не удалось подготовить.")
        if status == "disabled":
            return "Генерация итогов выключена в настройках."
        return "Итоги дня будут сформированы после завершения рабочего дня."

    def _day_summary_badge(self, metadata: dict[str, object]) -> tuple[str, str]:
        status = metadata.get("day_summary_status")
        if status == "draft_created":
            return "Итоги готовы", "ok"
        if status == "up_to_date":
            return "Актуально", "ok"
        if status in {"running"}:
            return "Генерация", "active"
        if status == "waiting_for_meetings":
            return "В очереди", "wait"
        if status in {"failed", "openai_unavailable"}:
            return "Ошибка", "error"
        if status == "disabled":
            return "Пропущено", "skip"
        return "Ожидает", "wait"

    def _refresh_day_summary_pipeline_from_metadata(self, metadata: dict[str, object]) -> None:
        pipeline = metadata.get("pipeline")
        if not isinstance(pipeline, dict):
            pipeline = {}
        for step, title, _icon in self.DAY_SUMMARY_PIPELINE_STEPS:
            state = str(pipeline.get(step) or "wait")
            label = self._day_summary_pipeline_label(state)
            self._set_day_summary_pipeline_step(
                step,
                label,
                self._day_summary_step_message(step, metadata, state),
                state,
            )

    @staticmethod
    def _day_summary_pipeline_label(state: str) -> str:
        return {
            "ok": "Готово",
            "active": "Выполняется",
            "wait": "Ожидает",
            "skip": "Пропущено",
            "error": "Ошибка",
        }.get(state, "Ожидает")

    def _day_summary_step_message(
        self,
        step: str,
        metadata: dict[str, object],
        state: str,
    ) -> str:
        if step == "collect":
            if state == "ok":
                return "Итоги встреч собраны."
            if metadata.get("day_summary_status") == "waiting_for_meetings":
                return "Ждет завершения pipeline встреч."
            return "Ждет завершения рабочего дня."
        if step == "check":
            included = metadata.get("included_meetings")
            missing = [
                item for item in included
                if isinstance(item, dict) and item.get("summary_missing")
            ] if isinstance(included, list) else []
            if state == "ok":
                return "Summary встреч проверены."
            if missing:
                return f"Есть встречи без summary: {len(missing)}."
            return "Проверка summary еще не выполнялась."
        if step == "generate":
            if metadata.get("day_summary_error"):
                return str(metadata["day_summary_error"])
            if state == "ok":
                return "00_day_summary_draft.md готов."
            if state == "active":
                return "OpenAI готовит выжимку итогов встреч."
            if state == "skip":
                return "Новых встреч нет, обновление не требуется."
            return "Ждет готовые данные встреч."
        if step == "links":
            if state == "ok":
                return "Список переходов к transcript сформирован."
            return "Ссылки появятся вместе с карточкой итогов дня."
        return ""

    def _set_day_summary_pipeline_step(
        self,
        step: str,
        label: str,
        message: str,
        state: str,
    ) -> None:
        widget = self.day_summary_pipeline_labels.get(step)
        if widget is None:
            return
        widget.setText(label)
        self._apply_badge_style(widget, state)
        message_widget = self.day_summary_pipeline_messages.get(step)
        if message_widget is not None:
            message_widget.setText(message)

    @staticmethod
    def _short_time(value: object) -> str:
        if not value:
            return "--:--"
        try:
            return datetime.fromisoformat(str(value)).strftime("%H:%M")
        except ValueError:
            return "--:--"

    @staticmethod
    def _duration_text(metadata: dict[str, object]) -> str:
        duration = metadata.get("duration_seconds")
        if isinstance(duration, int):
            minutes = max(1, duration // 60)
            return f"{minutes} мин."
        if metadata.get("status") == "active":
            return "идет сейчас"
        return "без длительности"

    def closeEvent(self, event) -> None:
        if self._has_processing_work():
            event.ignore()
            self.status_label.setText(
                "Дождитесь завершения обработки. Сейчас обновляются локальные файлы встречи или итогов дня."
            )
            return
        if hasattr(self, "floating_control"):
            self.floating_control.close_from_app()
        super().closeEvent(event)

    def _create_workday_page(self) -> QWidget:
        page = QWidget()
        self._prepare_page_surface(page)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(
            self._create_page_header(
                "Рабочий день",
                f"Выбранная дата: сегодня, {date.today().strftime('%d.%m.%Y')}",
            )
        )

        readiness_layout = QGridLayout()
        readiness_layout.setContentsMargins(0, 0, 0, 0)
        readiness_layout.setHorizontalSpacing(10)
        readiness_layout.setVerticalSpacing(10)
        readiness_rows = [
            "OBS",
            "FFmpeg",
            "Whisper",
            "Summary",
            "API key",
            "Summary endpoint",
        ]
        for index, component in enumerate(readiness_rows):
            row = index // 3
            column = index % 3
            readiness_layout.addWidget(
                self._create_readiness_tile(component),
                row,
                column,
                Qt.AlignmentFlag.AlignTop,
            )
        for column in range(3):
            readiness_layout.setColumnStretch(column, 1)
        for row in range(2):
            readiness_layout.setRowMinimumHeight(row, 82)
        layout.addWidget(self._create_readiness_card(readiness_layout))

        status_layout = QVBoxLayout()
        status_layout.setSpacing(0)
        self.workday_status_value = QLabel()
        self.meeting_status_value = QLabel()
        self.day_folder_value = QLabel()
        self.active_meeting_value = QLabel()
        self.obs_status_value = QLabel(self.recorder.status_text)

        self.day_status_badge = QLabel("Не активен")
        self.day_status_badge.setObjectName("statusBadge")
        self._apply_badge_style(self.day_status_badge, "wait")
        self.day_folder_badge = QLabel("Папка не создана")
        self.day_folder_badge.setObjectName("statusBadge")
        self._apply_badge_style(self.day_folder_badge, "wait")
        self.day_status_panel = QFrame()
        self.day_status_panel.setObjectName("overviewInnerPanel")
        self.day_status_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.day_status_panel.setMinimumHeight(160)
        self.day_status_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        day_panel_layout = QVBoxLayout()
        day_panel_layout.setContentsMargins(12, 10, 12, 10)
        day_panel_layout.setSpacing(8)
        day_panel_header = QHBoxLayout()
        day_panel_header.setContentsMargins(0, 0, 0, 0)
        self.day_date_title_value = QLabel()
        self.day_date_title_value.setObjectName("heroValue")
        day_panel_header.addWidget(self.day_date_title_value)
        day_panel_header.addStretch(1)
        day_panel_header.addWidget(self.day_folder_badge, 0, Qt.AlignmentFlag.AlignTop)
        self.day_status_detail_value = QLabel()
        self.day_status_detail_value.setObjectName("sectionHint")
        self.day_status_detail_value.setWordWrap(True)
        day_actions_layout = QHBoxLayout()
        day_actions_layout.setSpacing(8)
        self.workday_action_button = self._add_button(
            day_actions_layout, "Начать рабочий день", self.start_workday, "primaryButton"
        )
        self.start_workday_button = self.workday_action_button
        self.end_workday_button = self.workday_action_button
        self.day_status_open_folder_button = self._add_button(
            day_actions_layout, "Открыть папку дня", self.open_day_folder
        )
        day_actions_layout.addStretch(1)
        day_panel_layout.addLayout(day_panel_header)
        day_panel_layout.addWidget(self.day_status_detail_value)
        day_panel_layout.addStretch(1)
        day_panel_layout.addLayout(day_actions_layout)
        self.day_status_panel.setLayout(day_panel_layout)
        status_layout.addWidget(self.day_status_panel)

        active_call_layout = QVBoxLayout()
        active_call_layout.setSpacing(0)
        self.active_call_badge = QLabel("Не начат")
        self.active_call_badge.setObjectName("statusBadge")
        self._apply_badge_style(self.active_call_badge, "wait")
        self.active_call_panel = QFrame()
        self.active_call_panel.setObjectName("overviewInnerPanel")
        self.active_call_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.active_call_panel.setMinimumHeight(160)
        self.active_call_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        active_call_panel_layout = QHBoxLayout()
        active_call_panel_layout.setContentsMargins(14, 12, 14, 12)
        active_call_panel_layout.setSpacing(14)
        active_call_text_layout = QVBoxLayout()
        active_call_text_layout.setContentsMargins(0, 0, 0, 0)
        active_call_text_layout.setSpacing(8)
        active_call_text_layout.addStretch(1)
        self.active_call_title_value = QLabel()
        self.active_call_title_value.setObjectName("heroValue")
        self.active_call_detail_value = QLabel()
        self.active_call_detail_value.setObjectName("sectionHint")
        self.active_call_detail_value.setWordWrap(True)
        active_call_text_layout.addWidget(self.active_call_title_value)
        active_call_text_layout.addWidget(self.active_call_detail_value)
        active_call_text_layout.addStretch(1)

        active_call_controls_layout = QVBoxLayout()
        active_call_controls_layout.setContentsMargins(0, 0, 0, 0)
        active_call_controls_layout.setSpacing(10)
        active_call_controls_layout.addStretch(1)
        self.active_call_timer_value = QLabel("00:00:00")
        self.active_call_timer_value.setObjectName("callTimer")
        self.active_call_timer_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.start_meeting_button = QPushButton("Начать встречу")
        self.start_meeting_button.setObjectName("primaryButton")
        self.start_meeting_button.clicked.connect(self.start_meeting)
        self.end_meeting_button = QPushButton("Завершить встречу")
        self.end_meeting_button.setObjectName("dangerButton")
        self.end_meeting_button.clicked.connect(self.end_meeting)
        active_call_controls_layout.addWidget(
            self.active_call_timer_value,
            0,
            Qt.AlignmentFlag.AlignRight,
        )
        active_call_controls_layout.addWidget(
            self.start_meeting_button,
            0,
            Qt.AlignmentFlag.AlignRight,
        )
        active_call_controls_layout.addWidget(
            self.end_meeting_button,
            0,
            Qt.AlignmentFlag.AlignRight,
        )
        active_call_controls_layout.addStretch(1)
        active_call_panel_layout.addLayout(active_call_text_layout, 1)
        active_call_panel_layout.addLayout(active_call_controls_layout)
        self.active_call_panel.setLayout(active_call_panel_layout)
        active_call_layout.addWidget(self.active_call_panel)

        day_overview_layout = QHBoxLayout()
        day_overview_layout.setSpacing(14)
        self.day_status_card = self._create_card(
            "Состояние дня",
            status_layout,
            title_badges=[self.day_status_badge],
        )
        self.active_call_card = self._create_card(
            "Активный созвон",
            active_call_layout,
            title_badges=[self.active_call_badge],
        )
        for overview_card in [self.day_status_card, self.active_call_card]:
            overview_card.setMinimumHeight(self.DAY_OVERVIEW_CARD_MIN_HEIGHT)
            overview_card.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Maximum,
            )
        day_overview_layout.addWidget(self.day_status_card, 1)
        day_overview_layout.addWidget(self.active_call_card, 1)
        layout.addLayout(day_overview_layout)

        meetings_layout = QVBoxLayout()
        meetings_layout.setSpacing(10)
        self.today_meetings_value = QLabel()
        self.today_meetings_value.setObjectName("sectionHint")
        self.today_meetings_value.setWordWrap(True)
        meetings_layout.addWidget(self.today_meetings_value)
        self.meetings_cards_layout = QVBoxLayout()
        self.meetings_cards_layout.setSpacing(10)
        meetings_layout.addLayout(self.meetings_cards_layout)
        empty_day_actions = QHBoxLayout()
        empty_day_actions.setSpacing(8)
        self.open_day_folder_button = self._add_button(
            empty_day_actions, "Открыть папку дня", self.open_day_folder
        )
        empty_day_actions.addStretch(1)
        meetings_layout.addLayout(empty_day_actions)
        self.meetings_body = QWidget()
        self.meetings_body.setLayout(meetings_layout)
        meetings_body_layout = QVBoxLayout()
        meetings_body_layout.setContentsMargins(0, 0, 0, 0)
        meetings_body_layout.addWidget(self.meetings_body)
        self.toggle_meetings_button = QPushButton("Свернуть")
        self.toggle_meetings_button.setObjectName("headerButton")
        self.toggle_meetings_button.setFixedHeight(34)
        self.toggle_meetings_button.clicked.connect(self._toggle_meetings_card)
        layout.addWidget(
            self._create_card(
                "Встречи за день",
                meetings_body_layout,
                [self.toggle_meetings_button],
            )
        )

        self.status_label = QLabel(self._startup_status())
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("inlineStatus")
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        page.setLayout(layout)

        scroll_area = self._create_page_scroll_area("workdayScrollArea", page)
        self.workday_scroll_area = scroll_area
        return scroll_area

    def _create_review_page(self) -> QWidget:
        page = QWidget()
        self._prepare_page_surface(page)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(
            self._create_page_header(
                "Ревью",
                "Проверьте итоги выбранной встречи и итог дня перед сохранением финальных файлов.",
            )
        )

        content_layout = QHBoxLayout()
        content_layout.setSpacing(14)
        meetings_layout = QVBoxLayout()
        meetings_layout.setSpacing(10)
        self.review_meetings_hint = QLabel("Откройте ревью, чтобы загрузить встречи выбранного дня.")
        self.review_meetings_hint.setObjectName("sectionHint")
        self.review_meetings_hint.setWordWrap(True)
        meetings_layout.addWidget(self.review_meetings_hint)
        self.review_meeting_cards_layout = QVBoxLayout()
        self.review_meeting_cards_layout.setSpacing(10)
        meetings_layout.addLayout(self.review_meeting_cards_layout)
        meetings_layout.addStretch(1)
        meetings_group = self._create_card("Встречи за день", meetings_layout)
        meetings_group.setMinimumWidth(260)
        content_layout.addWidget(meetings_group)

        review_content_layout = QVBoxLayout()
        review_content_layout.setSpacing(12)
        self.review_tabs = QTabWidget()
        self.meeting_summary_editor = QPlainTextEdit()
        self.meeting_transcript_editor = QTextBrowser()
        self.meeting_transcript_editor.setReadOnly(True)
        self.meeting_transcript_editor.setOpenLinks(False)
        self.meeting_transcript_editor.anchorClicked.connect(self._open_review_transcript_link)
        self.day_summary_editor = self.meeting_summary_editor
        self.review_tabs.addTab(self.meeting_summary_editor, "Итоги встречи")
        self.review_tabs.addTab(self.meeting_transcript_editor, "Транскрипт")
        review_content_layout.addWidget(self.review_tabs, 1)
        content_layout.addLayout(review_content_layout, 1)
        layout.addLayout(content_layout, 1)

        actions_layout = QHBoxLayout()
        self.save_drafts_button = self._add_button(
            actions_layout, "Сохранить черновики", self.save_drafts
        )
        self.save_final_files_button = self._add_button(
            actions_layout, "Сохранить финальные файлы", self.save_final_files
        )
        self.review_open_folder_button = self._add_button(
            actions_layout, "Открыть папку дня", self.open_day_folder
        )
        layout.addLayout(actions_layout)

        self.review_status_label = QLabel("Откройте ревью, чтобы загрузить локальные файлы.")
        self.review_status_label.setWordWrap(True)
        self.review_status_label.setObjectName("inlineStatus")
        layout.addWidget(self.review_status_label)
        page.setLayout(layout)
        return page

    def _create_settings_page(self) -> QWidget:
        page = QWidget()
        self._prepare_page_surface(page)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(
            self._create_page_header(
                "Настройки",
                "Безопасные локальные настройки приложения. Секреты не сохраняйте в git.",
            )
        )

        storage_layout = QFormLayout()
        storage_layout.setHorizontalSpacing(18)
        storage_layout.setVerticalSpacing(8)
        self.settings_storage_root_input = QLineEdit(str(self.config["storage"]["root"]))
        storage_layout.addRow("Папка данных:", self.settings_storage_root_input)
        layout.addWidget(self._create_card("Хранение", storage_layout))

        obs_layout = QFormLayout()
        obs_layout.setHorizontalSpacing(18)
        obs_layout.setVerticalSpacing(8)
        self.settings_obs_enabled_checkbox = QCheckBox("OBS включен")
        self.settings_obs_enabled_checkbox.setChecked(bool(self.config["obs"]["enabled"]))
        self.settings_obs_host_input = QLineEdit(str(self.config["obs"]["websocket_host"]))
        self.settings_obs_port_input = QSpinBox()
        self.settings_obs_port_input.setRange(1, 65535)
        self.settings_obs_port_input.setValue(int(self.config["obs"]["websocket_port"]))
        self.settings_obs_password_input = QLineEdit(str(self.config["obs"]["websocket_password"]))
        self.settings_obs_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        obs_layout.addRow("", self.settings_obs_enabled_checkbox)
        obs_layout.addRow("WebSocket host:", self.settings_obs_host_input)
        obs_layout.addRow("WebSocket port:", self.settings_obs_port_input)
        obs_layout.addRow("WebSocket password:", self.settings_obs_password_input)
        layout.addWidget(self._create_card("OBS", obs_layout))

        secrets_layout = QFormLayout()
        secrets_layout.setHorizontalSpacing(18)
        secrets_layout.setVerticalSpacing(8)
        self.settings_secrets_env_file_input = QLineEdit(
            str(self.config.get("secrets", {}).get("env_file", ""))
        )
        secrets_hint = QLabel(
            "Один локальный .env файл для API-ключей внешних сервисов. "
            "Например, для AITUNNEL_KEY. Сам файл не добавляется в git."
        )
        secrets_hint.setObjectName("sectionHint")
        secrets_hint.setWordWrap(True)
        secrets_layout.addRow(".env файл:", self.settings_secrets_env_file_input)
        secrets_layout.addRow("", secrets_hint)
        layout.addWidget(self._create_card("Секреты", secrets_layout))

        transcription_layout = QFormLayout()
        transcription_layout.setHorizontalSpacing(18)
        transcription_layout.setVerticalSpacing(8)
        self.settings_transcription_profiles = deepcopy(
            self.config["transcription"].get("backends", {})
        )
        self.settings_current_transcription_backend = str(
            self.config["transcription"]["backend"]
        )
        self.settings_transcription_rows: list[tuple[QLabel | None, QWidget, set[str]]] = []
        self.settings_transcription_backend_select = QComboBox()
        self.settings_transcription_backend_select.addItems(
            ["whisper_cli", "faster_whisper", "aitunnel"]
        )
        self._set_combo_value(
            self.settings_transcription_backend_select,
            str(self.config["transcription"]["backend"]),
        )
        self.settings_transcription_model_select = QComboBox()
        self.settings_transcription_device_select = QComboBox()
        self.settings_transcription_device_select.addItems(["cpu", "cuda"])
        self.settings_transcription_timeout_input = QSpinBox()
        self.settings_transcription_timeout_input.setRange(1, 3600)
        self.settings_transcription_upload_limit_input = QSpinBox()
        self.settings_transcription_upload_limit_input.setRange(1, 25)
        self.settings_transcription_chunking_checkbox = QCheckBox(
            "Нарезать длинные записи автоматически"
        )
        self.settings_transcription_chunk_duration_input = QSpinBox()
        self.settings_transcription_chunk_duration_input.setRange(30, 3600)
        self.settings_transcription_chunk_duration_input.setSuffix(" сек.")
        self.settings_transcription_retry_attempts_input = QSpinBox()
        self.settings_transcription_retry_attempts_input.setRange(0, 10)
        self.settings_transcription_vad_checkbox = QCheckBox(
            "Для faster-whisper отсекать тишину и неречевой шум"
        )
        transcription_hint = QLabel(
            "Язык транскрипции всегда русский. API key для AI Tunnel берется из блока "
            "`Секреты` и переменной AITUNNEL_KEY."
        )
        transcription_hint.setObjectName("sectionHint")
        transcription_hint.setWordWrap(True)
        transcription_layout.addRow("Backend:", self.settings_transcription_backend_select)
        self._add_transcription_settings_row(
            transcription_layout,
            "Модель:",
            self.settings_transcription_model_select,
            {"whisper_cli", "faster_whisper", "aitunnel"},
        )
        self._add_transcription_settings_row(
            transcription_layout,
            "Устройство:",
            self.settings_transcription_device_select,
            {"faster_whisper"},
        )
        self._add_transcription_settings_row(
            transcription_layout,
            "",
            self.settings_transcription_vad_checkbox,
            {"faster_whisper"},
        )
        self._add_transcription_settings_row(
            transcription_layout,
            "Timeout, секунд:",
            self.settings_transcription_timeout_input,
            {"aitunnel"},
        )
        self._add_transcription_settings_row(
            transcription_layout,
            "Макс. размер аудио, МБ:",
            self.settings_transcription_upload_limit_input,
            {"aitunnel"},
        )
        self._add_transcription_settings_row(
            transcription_layout,
            "",
            self.settings_transcription_chunking_checkbox,
            {"aitunnel"},
        )
        self._add_transcription_settings_row(
            transcription_layout,
            "Длительность части:",
            self.settings_transcription_chunk_duration_input,
            {"aitunnel"},
        )
        self._add_transcription_settings_row(
            transcription_layout,
            "Повторов при временной ошибке:",
            self.settings_transcription_retry_attempts_input,
            {"aitunnel"},
        )
        transcription_layout.addRow("", transcription_hint)
        self._load_transcription_profile_into_settings(
            self.settings_current_transcription_backend
        )
        self.settings_transcription_backend_select.currentTextChanged.connect(
            self._on_transcription_backend_changed
        )
        self._update_transcription_settings_visibility()
        layout.addWidget(self._create_card("Транскрипция", transcription_layout))

        summary_layout = QFormLayout()
        summary_layout.setHorizontalSpacing(18)
        summary_layout.setVerticalSpacing(8)
        self.settings_summary_enabled_checkbox = QCheckBox("Генерация итогов включена")
        self.settings_summary_enabled_checkbox.setChecked(bool(self.config["summary"]["enabled"]))
        self.settings_summary_model_select = QComboBox()
        for label, value in SUMMARY_MODEL_OPTIONS:
            self.settings_summary_model_select.addItem(label, value)
        self.settings_summary_custom_model_input = QLineEdit()
        self.settings_summary_custom_model_input.setPlaceholderText(
            "Например: deepseek-r1, gemini-..., claude-..."
        )
        self.settings_summary_timeout_input = QSpinBox()
        self.settings_summary_timeout_input.setRange(1, 3600)
        self.settings_summary_timeout_input.setValue(int(self.config["summary"]["timeout_seconds"]))
        self.settings_summary_chunk_input = QSpinBox()
        self.settings_summary_chunk_input.setRange(1000, 200000)
        self.settings_summary_chunk_input.setValue(int(self.config["summary"]["max_chars_per_chunk"]))
        summary_hint = QLabel(
            "Summary использует AI Tunnel. API key берется из блока `Секреты` "
            "и переменной AITUNNEL_KEY."
        )
        summary_hint.setObjectName("sectionHint")
        summary_hint.setWordWrap(True)
        summary_layout.addRow("", self.settings_summary_enabled_checkbox)
        summary_layout.addRow("Модель:", self.settings_summary_model_select)
        self.settings_summary_custom_model_label = QLabel("ID модели:")
        summary_layout.addRow(
            self.settings_summary_custom_model_label,
            self.settings_summary_custom_model_input,
        )
        summary_layout.addRow("Timeout, секунд:", self.settings_summary_timeout_input)
        summary_layout.addRow("Символов на chunk:", self.settings_summary_chunk_input)
        summary_layout.addRow("", summary_hint)
        self._load_summary_model_settings(str(self.config["summary"]["model"]))
        self.settings_summary_model_select.currentIndexChanged.connect(
            self._update_summary_custom_model_visibility
        )
        layout.addWidget(self._create_card("Summary", summary_layout))

        ui_layout = QFormLayout()
        ui_layout.setHorizontalSpacing(18)
        ui_layout.setVerticalSpacing(8)
        self.settings_theme_select = QComboBox()
        self.settings_theme_select.addItem("Светлая", "light")
        self.settings_theme_select.addItem("Темная", "dark")
        self._set_combo_value(
            self.settings_theme_select,
            str(self.config.get("ui", {}).get("theme", "light")),
        )
        self.settings_floating_theme_select = QComboBox()
        self.settings_floating_theme_select.addItem("Как в приложении", "inherit")
        self.settings_floating_theme_select.addItem("Светлая", "light")
        self.settings_floating_theme_select.addItem("Темная", "dark")
        self._set_combo_value(
            self.settings_floating_theme_select,
            str(self.config.get("ui", {}).get("floating_theme", "inherit")),
        )
        theme_hint = QLabel(
            "Тема основного окна и floating control применяется сразу после сохранения. "
            "Настройки transcription применяются для следующих встреч после сохранения."
        )
        theme_hint.setObjectName("sectionHint")
        theme_hint.setWordWrap(True)
        ui_layout.addRow("Тема приложения:", self.settings_theme_select)
        ui_layout.addRow("Тема floating control:", self.settings_floating_theme_select)
        ui_layout.addRow("", theme_hint)
        layout.addWidget(self._create_card("Интерфейс", ui_layout))

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)
        self.save_settings_button = self._add_button(
            actions_layout, "Сохранить настройки", self.save_settings, "primaryButton"
        )
        actions_layout.addStretch(1)
        layout.addLayout(actions_layout)
        self.settings_status_label = QLabel(
            "Настройки сохраняются в локальный config.yaml. Файл не должен попадать в git."
        )
        self.settings_status_label.setWordWrap(True)
        self.settings_status_label.setObjectName("inlineStatus")
        layout.addWidget(self.settings_status_label)

        page.setLayout(layout)
        return self._create_page_scroll_area("settingsScrollArea", page)

    def _add_transcription_settings_row(
        self,
        layout: QFormLayout,
        label_text: str,
        field: QWidget,
        backends: set[str],
    ) -> None:
        layout.addRow(label_text, field)
        label = layout.labelForField(field)
        self.settings_transcription_rows.append((label, field, backends))

    def _on_transcription_backend_changed(self, backend: str) -> None:
        previous_backend = getattr(self, "settings_current_transcription_backend", "")
        if previous_backend:
            self._save_current_transcription_profile(previous_backend)
        self.settings_current_transcription_backend = backend
        self._load_transcription_profile_into_settings(backend)
        self._update_transcription_settings_visibility()

    def _load_transcription_profile_into_settings(self, backend: str) -> None:
        profile = self._transcription_settings_profile(backend)
        self._set_transcription_model_options(
            backend,
            str(profile.get("model") or "base"),
        )
        if backend == "faster_whisper":
            self._set_combo_value(
                self.settings_transcription_device_select,
                str(profile.get("device") or "cpu"),
            )
            self.settings_transcription_vad_checkbox.setChecked(
                bool(profile.get("vad_filter", True))
            )
        if backend == "aitunnel":
            self.settings_transcription_timeout_input.setValue(
                int(profile.get("timeout_seconds") or 300)
            )
            self.settings_transcription_upload_limit_input.setValue(
                int(profile.get("max_upload_mb") or 25)
            )
            self.settings_transcription_chunking_checkbox.setChecked(
                bool(profile.get("chunking_enabled", True))
            )
            self.settings_transcription_chunk_duration_input.setValue(
                int(profile.get("chunk_duration_seconds") or 600)
            )
            self.settings_transcription_retry_attempts_input.setValue(
                int(profile.get("retry_attempts") or 2)
            )

    def _set_transcription_model_options(self, backend: str, selected_model: str) -> None:
        if backend == "aitunnel":
            options = AITUNNEL_MODEL_OPTIONS
        elif backend == "faster_whisper":
            options = FASTER_WHISPER_MODEL_OPTIONS
        else:
            options = WHISPER_CLI_MODEL_OPTIONS
        self.settings_transcription_model_select.clear()
        for label, value in options:
            self.settings_transcription_model_select.addItem(label, value)
        self._set_combo_value(self.settings_transcription_model_select, selected_model)

    def _save_current_transcription_profile(self, backend: str) -> None:
        if not hasattr(self, "settings_transcription_profiles"):
            return
        profile = self._transcription_settings_profile(backend)
        model = self._combo_value(self.settings_transcription_model_select)
        if backend == "aitunnel":
            profile.update(
                {
                    "model": model or "whisper-large-v3-turbo",
                    "language": "ru",
                    "api_key_env": "AITUNNEL_KEY",
                    "base_url": "https://api.aitunnel.ru/v1/",
                    "env_file": "",
                    "timeout_seconds": self.settings_transcription_timeout_input.value(),
                    "max_upload_mb": self.settings_transcription_upload_limit_input.value(),
                    "chunking_enabled": self.settings_transcription_chunking_checkbox.isChecked(),
                    "chunk_duration_seconds": self.settings_transcription_chunk_duration_input.value(),
                    "retry_attempts": self.settings_transcription_retry_attempts_input.value(),
                    "retry_sleep_seconds": 1,
                }
            )
        elif backend == "faster_whisper":
            device = self._combo_value(self.settings_transcription_device_select) or "cpu"
            profile.update(
                {
                    "model": model or "base",
                    "language": "ru",
                    "device": device,
                    "compute_type": "float16" if device == "cuda" else "int8",
                    "vad_filter": self.settings_transcription_vad_checkbox.isChecked(),
                }
            )
        else:
            profile.update(
                {
                    "model": model or "base",
                    "language": "ru",
                    "whisper_command": "whisper",
                }
            )
        self.settings_transcription_profiles[backend] = profile

    def _transcription_settings_profile(self, backend: str) -> dict[str, object]:
        profiles = getattr(self, "settings_transcription_profiles", {})
        profile = profiles.get(backend)
        if isinstance(profile, dict):
            return dict(profile)
        defaults = DEFAULT_CONFIG["transcription"]["backends"]
        default_profile = defaults.get(backend, defaults["whisper_cli"])
        return dict(default_profile)

    def _update_transcription_settings_visibility(self) -> None:
        if not hasattr(self, "settings_transcription_rows"):
            return
        backend = self.settings_transcription_backend_select.currentText()
        for label, field, backends in self.settings_transcription_rows:
            visible = backend in backends
            field.setVisible(visible)
            if label is not None:
                label.setVisible(visible)

    def _load_summary_model_settings(self, model: str) -> None:
        known_models = {value for _, value in SUMMARY_MODEL_OPTIONS if value != "__custom__"}
        if model in known_models:
            self._set_combo_value(self.settings_summary_model_select, model)
            self.settings_summary_custom_model_input.clear()
        else:
            self._set_combo_value(self.settings_summary_model_select, "__custom__")
            self.settings_summary_custom_model_input.setText(model)
        self._update_summary_custom_model_visibility()

    def _update_summary_custom_model_visibility(self) -> None:
        visible = self._combo_value(self.settings_summary_model_select) == "__custom__"
        self.settings_summary_custom_model_label.setVisible(visible)
        self.settings_summary_custom_model_input.setVisible(visible)

    def _summary_model_from_settings(self) -> str:
        selected_model = self._combo_value(self.settings_summary_model_select)
        if selected_model == "__custom__":
            return self.settings_summary_custom_model_input.text().strip() or "gpt-5.4-mini"
        return selected_model or "gpt-5.4-mini"

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index < 0:
            index = combo.findText(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _combo_value(combo: QComboBox) -> str:
        data = combo.currentData()
        if data is not None:
            return str(data)
        return combo.currentText()

    def _create_archive_page(self) -> QWidget:
        page = QWidget()
        self._prepare_page_surface(page)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(
            self._create_page_header(
                "Архив",
                "Будущий read-only просмотр прошлых рабочих дней и встреч.",
            )
        )
        status_layout = QVBoxLayout()
        status_layout.setSpacing(8)
        archive_status = QLabel(
            "Архив пока не реализован. Текущие локальные файлы уже сохраняются в папке данных, "
            "но экран поиска и просмотра прошлых дней будет отдельным будущим PR."
        )
        archive_status.setObjectName("emptyState")
        archive_status.setWordWrap(True)
        status_layout.addWidget(archive_status)
        layout.addWidget(self._create_card("Статус архива", status_layout))

        planned_layout = QVBoxLayout()
        planned_layout.setSpacing(8)
        planned_text = QLabel(
            "Планируемое поведение:\n"
            "- список прошлых рабочих дней;\n"
            "- read-only карточки встреч;\n"
            "- открытие локальных папок и файлов;\n"
            "- без отправки аудио, видео или transcript во внешние сервисы."
        )
        planned_text.setObjectName("sectionHint")
        planned_text.setWordWrap(True)
        planned_layout.addWidget(planned_text)
        layout.addWidget(self._create_card("Что будет позже", planned_layout))
        layout.addStretch(1)
        page.setLayout(layout)
        return page

    def _create_help_page(self) -> QWidget:
        page = QWidget()
        self._prepare_page_surface(page)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(
            self._create_page_header(
                "Справка",
                "Краткая памятка по локальному рабочему сценарию.",
            )
        )

        flow_layout = QVBoxLayout()
        flow_text = QLabel(
            "1. Начните рабочий день.\n"
            "2. Начните встречу в блоке `Активный созвон`.\n"
            "3. Завершите встречу: запись остановится, а обработка пойдет в фоне.\n"
            "4. При необходимости сразу начните следующую встречу.\n"
            "5. Завершите рабочий день.\n"
            "6. Откройте `Ревью`, проверьте итоги и сохраните финальные файлы."
        )
        flow_text.setObjectName("sectionHint")
        flow_text.setWordWrap(True)
        flow_layout.addWidget(flow_text)
        layout.addWidget(self._create_card("Основной сценарий", flow_layout))

        local_layout = QVBoxLayout()
        local_text = QLabel(
            "Аудио и видео остаются локально. Для генерации итогов во внешний OpenAI-compatible "
            "endpoint отправляется только текст transcript. `config.yaml`, `.env`, записи, "
            "аудио, transcript и summary-файлы нельзя добавлять в git."
        )
        local_text.setObjectName("sectionHint")
        local_text.setWordWrap(True)
        local_layout.addWidget(local_text)
        layout.addWidget(self._create_card("Local-first и безопасность", local_layout))

        services_layout = QVBoxLayout()
        services_text = QLabel(
            "OBS управляет записью, FFmpeg локально извлекает audio.wav, Whisper/faster-whisper "
            "локально готовит transcript, а Summary generation создает черновик итогов встречи "
            "из готового текста transcript."
        )
        services_text.setObjectName("sectionHint")
        services_text.setWordWrap(True)
        services_layout.addWidget(services_text)
        layout.addWidget(self._create_card("Сервисы", services_layout))

        layout.addStretch(1)
        page.setLayout(layout)
        return page

    @staticmethod
    def _add_button(
        layout,
        label: str,
        callback: Callable[[], None],
        object_name: str | None = None,
    ) -> QPushButton:
        button = QPushButton(label)
        if object_name:
            button.setObjectName(object_name)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def start_workday(self) -> None:
        try:
            day_folder = self.storage.start_workday()
        except ValueError as error:
            self.status_label.setText(str(error))
            return
        if self.storage.last_workday_action == "reopened":
            self.status_label.setText(
                "Рабочий день переоткрыт. "
                f"Используется существующая папка дня: {day_folder}"
            )
        else:
            self.status_label.setText(f"Рабочий день начат. Папка: {day_folder}")
        self._refresh_after_lifecycle_change()

    def start_meeting(self) -> None:
        self.start_meeting_overlay.open_for_recorder(self.recorder)

    def _start_meeting_with_title(self, title: str) -> None:
        try:
            meeting_folder = self.storage.start_meeting(title)
        except ValueError as error:
            self.status_label.setText(str(error))
            return
        message = f"Встреча начата: {meeting_folder.name}"
        if self.storage.last_recorder_message:
            message = f"{message} {self.storage.last_recorder_message}"
        self.pipeline_completed = False
        self._refresh_after_lifecycle_change()
        if self.pipeline_running:
            message = f"{message} Предыдущая встреча еще обрабатывается в фоне."
        elif self._is_workday_pipeline_visible(meeting_folder):
            self._set_pipeline_step("meeting", "Готово", "Созвон начат.", "ok")
            self._set_pipeline_step("recording", "Выполняется", "OBS ведет запись или шаг пропущен.", "active")
            self._set_pipeline_step("audio", "Ожидает", "Ждет завершение встречи.", "wait")
            self._set_pipeline_step("transcription", "Ожидает", "Ждет audio.wav.", "wait")
            self._set_pipeline_step("summary", "Ожидает", "Ждет transcript.", "wait")
            self._set_pipeline_step("done", "Ожидает", "Встреча еще идет.", "wait")
        self.status_label.setText(message)

    def end_meeting(self) -> None:
        if not self.storage.meeting_active:
            self.status_label.setText("Нет активной встречи для завершения.")
            return
        finishing_meeting_folder = self.storage.active_meeting_folder
        processing_already_running = self.pipeline_running
        if not self.pipeline_running:
            self.pipeline_meeting_folder = finishing_meeting_folder
        self.pipeline_completed = False
        if not self.pipeline_running and self._is_workday_pipeline_visible(finishing_meeting_folder):
            self._set_pipeline_step("meeting", "Готово", "Созвон завершается.", "ok")
            self._set_pipeline_step("recording", "Выполняется", "Останавливаем OBS запись.", "active")
            self._set_pipeline_step("audio", "Ожидает", "Ждет остановку записи.", "wait")
            self._set_pipeline_step("transcription", "Ожидает", "Ждет audio.wav.", "wait")
            self._set_pipeline_step("summary", "Ожидает", "Ждет transcript.", "wait")
            self._set_pipeline_step("done", "Ожидает", "Pipeline ожидает обработки.", "wait")
        try:
            meeting_folder = self.storage.finish_active_meeting_recording(
                progress_callback=self._on_pipeline_progress if not self.pipeline_running else None
            )
        except ValueError as error:
            self.status_label.setText(str(error))
            return
        self._enqueue_meeting_processing(meeting_folder)
        queue_message = (
            "Обработка встречи добавлена в очередь."
            if processing_already_running
            else "Обработка встречи запущена в фоне."
        )
        self.status_label.setText(
            f"Запись встречи остановлена. Можно начать следующий созвон. {queue_message}"
        )
        self._refresh_after_lifecycle_change()

    def _enqueue_meeting_processing(self, meeting_folder: Path) -> None:
        self.processing_queue.append(meeting_folder)
        if not self.pipeline_running:
            self._start_next_pipeline()

    def _start_next_pipeline(self) -> None:
        if self.pipeline_running or self.pipeline_thread is not None or not self.processing_queue:
            return
        self.pipeline_meeting_folder = self.processing_queue.pop(0)
        self.pipeline_running = True
        self.pipeline_completed = False
        self.floating_background_message = "Фоновая обработка встречи запущена."
        metadata = self.storage.read_meeting_metadata(self.pipeline_meeting_folder)
        if self._is_workday_pipeline_visible(self.pipeline_meeting_folder):
            self._refresh_pipeline_from_metadata(metadata)
            self._set_pipeline_step("done", "Ожидает", "Pipeline выполняется.", "wait")
        self.status_label.setText(
            f"Фоновая обработка встречи запущена: {self.pipeline_meeting_folder.name}"
        )
        self.refresh_buttons()
        self.pipeline_thread = QThread(self)
        self.pipeline_worker = MeetingPipelineWorker(self.storage, self.pipeline_meeting_folder)
        self.pipeline_worker.moveToThread(self.pipeline_thread)
        self.pipeline_thread.started.connect(self.pipeline_worker.run)
        self.pipeline_worker.progress.connect(self._on_pipeline_progress)
        self.pipeline_worker.finished.connect(self._on_pipeline_finished)
        self.pipeline_worker.failed.connect(self._on_pipeline_failed)
        self.pipeline_worker.finished.connect(self.pipeline_thread.quit)
        self.pipeline_worker.failed.connect(self.pipeline_thread.quit)
        self.pipeline_thread.finished.connect(self.pipeline_worker.deleteLater)
        self.pipeline_thread.finished.connect(self.pipeline_thread.deleteLater)
        self.pipeline_thread.finished.connect(self._on_pipeline_thread_finished)
        self.pipeline_thread.start()

    def check_readiness(self) -> None:
        statuses = check_readiness(self.config, self.recorder, self.storage.root)
        messages = []
        for status in statuses:
            component = status["component"]
            label = self.readiness_labels.get(component)
            if label is None:
                continue
            state = status["state"]
            label.setText(self._readiness_state_text(state, status["message"]))
            self._apply_status_style(label, state)
            badge = self.readiness_badges.get(component)
            if badge is not None:
                badge.setText(self._badge_state_text(state))
                self._apply_badge_style(badge, state)
            messages.append(status["message"])
        self.obs_status_value.setText(self.recorder.status_text)
        self.status_label.setText("Проверка готовности завершена. " + " ".join(messages))

    def _on_pipeline_progress(self, event: str, message: str) -> None:
        mapping = {
            "meeting_ending": ("meeting", "Выполняется", "Завершаем созвон.", "active"),
            "recording_stopping": ("recording", "Выполняется", message, "active"),
            "recording_done": ("recording", "Готово", message or "OBS запись остановлена.", "ok"),
            "recording_skipped": ("recording", "Пропущено", message, "skip"),
            "audio_running": ("audio", "Выполняется", message, "active"),
            "audio_done": ("audio", None, message, None),
            "transcription_running": ("transcription", "Выполняется", message, "active"),
            "transcription_chunk_started": ("transcription", "Выполняется", message, "active"),
            "transcription_chunk_retry": ("transcription", "Выполняется", message, "active"),
            "transcription_chunk_done": ("transcription", "Выполняется", message, "active"),
            "transcription_done": ("transcription", None, message, None),
            "summary_running": ("summary", "Выполняется", message, "active"),
            "summary_done": ("summary", None, message, None),
            "meeting_done": ("done", "Готово", message, "ok"),
        }
        item = mapping.get(event)
        if item is None:
            self.status_label.setText(message)
            return
        step, label, default_message, state = item
        if label is None or state is None:
            metadata = self._read_pipeline_metadata()
            label, state = self._step_status_from_metadata(step, metadata)
        if self._is_workday_pipeline_visible(self.pipeline_meeting_folder):
            self._set_pipeline_step(step, label, default_message, state)
        self.status_label.setText(default_message)
        self.floating_background_message = default_message
        self._refresh_floating_control()

    def _on_pipeline_finished(self, meeting_folder_text: str) -> None:
        meeting_folder = self.pipeline_meeting_folder or Path(meeting_folder_text)
        metadata = self.storage.read_meeting_metadata(meeting_folder)
        if self._is_workday_pipeline_visible(meeting_folder):
            self._refresh_pipeline_from_metadata(metadata)
        message = f"Обработка встречи завершена: {meeting_folder.name}"
        for extra in [
            self.storage.last_recorder_message,
            self.storage.last_audio_message,
            self.storage.last_transcription_message,
            self.storage.last_summary_message,
        ]:
            if extra:
                message = f"{message} {extra}"
        self.status_label.setText(message)
        self.pipeline_running = False
        self.pipeline_completed = True
        self.pipeline_meeting_folder = None
        self.floating_background_message = ""
        self._refresh_after_lifecycle_change()

    def _on_pipeline_failed(self, message: str) -> None:
        self.pipeline_running = False
        failed_meeting_folder = self.pipeline_meeting_folder
        self.pipeline_meeting_folder = None
        self.floating_background_message = f"Ошибка фоновой обработки: {message}"
        if self._is_workday_pipeline_visible(failed_meeting_folder):
            self._set_pipeline_step("done", "Ошибка", message, "error")
        self.status_label.setText(f"Фоновая обработка встречи не выполнена: {message}")
        self.refresh_buttons()
        if hasattr(self, "floating_control") and self.floating_control.isVisible():
            self.floating_control.show_error("Ошибка фоновой обработки. Откройте приложение для деталей.")

    def _on_pipeline_thread_finished(self) -> None:
        self.pipeline_thread = None
        self.pipeline_worker = None
        self._start_next_pipeline()
        self._start_pending_day_summary_if_ready()

    def update_day_summary(self) -> None:
        day_folder = self.storage.get_today_day_folder()
        if day_folder is None:
            self.status_label.setText("Папка сегодняшнего рабочего дня пока не создана.")
            return
        self._request_day_summary_update(day_folder, force=True)

    def _request_day_summary_update(self, day_folder: Path, force: bool = False) -> None:
        if self.pipeline_running or self.processing_queue or self.storage.has_unfinished_meeting_processing(day_folder):
            self.storage.mark_day_summary_waiting(day_folder)
            self.day_summary_pending = True
            self.day_summary_force_pending = self.day_summary_force_pending or force
            self.day_summary_day_folder = day_folder
            self.status_label.setText(
                "Итоги дня поставлены в очередь и начнутся после завершения обработки встреч."
            )
            self._refresh_after_lifecycle_change()
            return
        self._start_day_summary_pipeline(day_folder, force)

    def _start_pending_day_summary_if_ready(self) -> None:
        if not self.day_summary_pending or self.pipeline_running or self.processing_queue:
            return
        day_folder = self.day_summary_day_folder or self.storage.get_today_day_folder()
        if day_folder is None or self.storage.has_unfinished_meeting_processing(day_folder):
            return
        force = self.day_summary_force_pending
        self.day_summary_pending = False
        self.day_summary_force_pending = False
        self._start_day_summary_pipeline(day_folder, force)

    def _start_day_summary_pipeline(self, day_folder: Path, force: bool = False) -> None:
        if self.day_summary_running or self.day_summary_thread is not None:
            self.day_summary_pending = True
            self.day_summary_force_pending = self.day_summary_force_pending or force
            self.day_summary_day_folder = day_folder
            return
        self.day_summary_running = True
        self.day_summary_day_folder = day_folder
        self.status_label.setText("Запущено обновление итогов дня.")
        self.refresh_buttons()
        self.day_summary_thread = QThread(self)
        self.day_summary_worker = DaySummaryPipelineWorker(self.storage, day_folder, force)
        self.day_summary_worker.moveToThread(self.day_summary_thread)
        self.day_summary_thread.started.connect(self.day_summary_worker.run)
        self.day_summary_worker.progress.connect(self._on_day_summary_progress)
        self.day_summary_worker.finished.connect(self._on_day_summary_finished)
        self.day_summary_worker.failed.connect(self._on_day_summary_failed)
        self.day_summary_worker.finished.connect(self.day_summary_thread.quit)
        self.day_summary_worker.failed.connect(self.day_summary_thread.quit)
        self.day_summary_thread.finished.connect(self.day_summary_worker.deleteLater)
        self.day_summary_thread.finished.connect(self.day_summary_thread.deleteLater)
        self.day_summary_thread.finished.connect(self._on_day_summary_thread_finished)
        self.day_summary_thread.start()

    def _on_day_summary_progress(self, event: str, message: str) -> None:
        mapping = {
            "day_summary_waiting": ("collect", "Выполняется", message, "active"),
            "day_summary_collecting": ("collect", "Выполняется", message, "active"),
            "day_summary_checking": ("check", "Выполняется", message, "active"),
            "day_summary_generating": ("generate", "Выполняется", message, "active"),
            "day_summary_up_to_date": ("generate", "Пропущено", message, "skip"),
            "day_summary_done": ("links", "Готово", message, "ok"),
        }
        item = mapping.get(event)
        if item is not None:
            step, label, default_message, state = item
            self._set_day_summary_pipeline_step(step, label, default_message, state)
            if event == "day_summary_done":
                self._set_day_summary_pipeline_step("collect", "Готово", "Итоги встреч собраны.", "ok")
                self._set_day_summary_pipeline_step("check", "Готово", "Summary встреч проверены.", "ok")
                self._set_day_summary_pipeline_step("generate", "Готово", "00_day_summary_draft.md готов.", "ok")
        self.status_label.setText(message)

    def _on_day_summary_finished(self, day_folder_text: str) -> None:
        day_folder = Path(day_folder_text)
        metadata = self.storage.read_day_summary_metadata(day_folder)
        self._refresh_day_summary_pipeline_from_metadata(metadata)
        self.day_summary_running = False
        self.day_summary_day_folder = None
        self.status_label.setText(self.storage.last_day_summary_message or "Итоги дня обновлены.")
        self._refresh_after_lifecycle_change()
        if self.review_day_summary_selected:
            self.load_day_summary_review()

    def _on_day_summary_failed(self, message: str) -> None:
        self.day_summary_running = False
        self.day_summary_day_folder = None
        self._set_day_summary_pipeline_step("generate", "Ошибка", message, "error")
        self.status_label.setText(f"Итоги дня не обновлены: {message}")
        self.refresh_buttons()

    def _on_day_summary_thread_finished(self) -> None:
        self.day_summary_thread = None
        self.day_summary_worker = None
        self._start_pending_day_summary_if_ready()

    def _set_pipeline_step(self, step: str, label: str, message: str, state: str) -> None:
        widget = self.pipeline_labels.get(step)
        if widget is None:
            return
        widget.setText(label)
        self._apply_badge_style(widget, state)
        message_widget = self.pipeline_messages.get(step)
        if message_widget is not None:
            message_widget.setText(message)

    def _is_workday_pipeline_visible(self, meeting_folder: Path | None) -> bool:
        return (
            meeting_folder is not None
            and self.selected_workday_meeting_folder == meeting_folder
        )

    def _read_pipeline_metadata(self) -> dict[str, object]:
        if self.pipeline_meeting_folder is None:
            return {}
        return self.storage.read_meeting_metadata(self.pipeline_meeting_folder)

    def _refresh_pipeline_from_metadata(self, metadata: dict[str, object]) -> None:
        if metadata.get("status") == "active":
            self._set_pipeline_step("meeting", "Выполняется", "Созвон идет.", "active")
        elif metadata:
            self._set_pipeline_step("meeting", "Готово", "Созвон завершен.", "ok")
        else:
            self._set_pipeline_step("meeting", "Ожидает", "Созвон не начат.", "wait")
        for step in ["recording", "audio", "transcription", "summary"]:
            label, state = self._step_status_from_metadata(step, metadata)
            self._set_pipeline_step(step, label, self._step_message(step, metadata), state)
        processing_status = metadata.get("processing_status")
        if metadata.get("status") == "active":
            self._set_pipeline_step("done", "Ожидает", "Встреча еще идет.", "wait")
        elif processing_status == "completed":
            self._set_pipeline_step("done", "Готово", "Metadata обновлена.", "ok")
        elif processing_status == "running":
            self._set_pipeline_step("done", "Выполняется", "Pipeline выполняется.", "active")
        elif processing_status == "pending":
            self._set_pipeline_step("done", "Ожидает", "Pipeline ожидает обработки.", "wait")
        else:
            self._set_pipeline_step("done", "Ожидает", "Pipeline не запускался.", "wait")

    def _step_status_from_metadata(
        self,
        step: str,
        metadata: dict[str, object],
    ) -> tuple[str, str]:
        status = str(metadata.get(f"{step}_status") or "")
        if step == "recording":
            status = str(metadata.get("recording_status") or "")
            if status == "recording":
                return "Выполняется", "active"
            if status in {"stopped", "disabled"}:
                return ("Готово" if status == "stopped" else "Пропущено", "ok" if status == "stopped" else "skip")
            if status.endswith("failed"):
                return "Ошибка", "error"
            return "Ожидает", "wait"
        if step == "audio":
            if status == "extracted":
                return "Готово", "ok"
            if status == "skipped":
                return "Пропущено", "skip"
            if status:
                return "Ошибка", "error"
        if step == "transcription":
            if status == "completed":
                if metadata.get("transcription_quality") == "suspect":
                    return "Проверить", "error"
                return "Готово", "ok"
            if status == "skipped":
                return "Пропущено", "skip"
            if status:
                return "Ошибка", "error"
        if step == "summary":
            if status == "draft_created":
                return "Готово", "ok"
            if status in {"disabled", "skipped"}:
                return "Пропущено", "skip"
            if status:
                return "Ошибка", "error"
        return "Ожидает", "wait"

    def _step_message(self, step: str, metadata: dict[str, object]) -> str:
        if step == "recording":
            if metadata.get("recording_status") == "recording":
                return "OBS ведет запись."
            if metadata.get("recording_status") == "stopped":
                return "Запись остановлена."
            if metadata.get("recording_status") == "disabled":
                return "OBS запись не активна."
            return str(metadata.get("recording_note") or "OBS запись ожидает обработки.")
        if step == "audio":
            if metadata.get("audio_error"):
                return str(metadata["audio_error"])
            if metadata.get("audio_status") == "extracted":
                return "audio.wav извлечен через FFmpeg."
            if metadata.get("audio_status") == "skipped":
                return "Аудио не извлекалось."
            return "Ждет завершения записи."
        if step == "transcription":
            if metadata.get("transcription_error"):
                return str(metadata["transcription_error"])
            if metadata.get("transcription_quality") == "suspect":
                warnings = metadata.get("transcription_quality_warnings") or []
                warning_text = " ".join(str(warning) for warning in warnings)
                return f"Транскрипция требует проверки. {warning_text}".strip()
            if metadata.get("transcription_status") == "completed":
                provider = metadata.get("transcription_provider")
                suffix = f" через {provider}" if provider else ""
                return f"transcript.md создан локально{suffix}."
            if metadata.get("transcription_status") == "skipped":
                return "Транскрипция пропущена."
            return "Ждет audio.wav."
        if step == "summary":
            if metadata.get("summary_error"):
                return str(metadata["summary_error"])
            if metadata.get("summary_status") == "draft_created":
                return "summary_draft.md готов к ревью."
            if metadata.get("summary_status") in {"disabled", "skipped"}:
                return "Генерация итогов выключена или пропущена."
            return "Ждет transcript."
        return ""

    @staticmethod
    def _readiness_state_text(state: str, message: str) -> str:
        prefix = {
            "ok": "Готово",
            "active": "Выполняется",
            "wait": "Ожидает",
            "skip": "Пропущено",
            "skipped": "Пропущено",
            "error": "Ошибка",
        }.get(state, "Статус")
        return f"{prefix}: {message}"

    @staticmethod
    def _badge_state_text(state: str) -> str:
        return {
            "ok": "OK",
            "active": "Идет",
            "wait": "Ждет",
            "skip": "Пропущено",
            "skipped": "Пропущено",
            "error": "Ошибка",
        }.get(state, "Статус")

    @staticmethod
    def _badge_state_from_text(text: str) -> str:
        normalized = text.strip().lower()
        if normalized == "ok":
            return "ok"
        if normalized in {"идет", "выполняется", "генерация", "обработка"}:
            return "active"
        if normalized in {"пропущено", "пропущен"}:
            return "skip"
        if normalized == "ошибка":
            return "error"
        return "wait"

    def _status_colors(self) -> dict[str, tuple[str, str]]:
        if getattr(self, "current_theme", "light") == "dark":
            return {
                "ok": ("#064e3b", "#bbf7d0"),
                "active": ("#1e3a8a", "#bfdbfe"),
                "wait": ("#1f2937", "#d1d5db"),
                "skip": ("#451a03", "#fde68a"),
                "skipped": ("#451a03", "#fde68a"),
                "error": ("#7f1d1d", "#fecaca"),
            }
        return {
            "ok": ("#dcfce7", "#166534"),
            "active": ("#dbeafe", "#1d4ed8"),
            "wait": ("#f3e8dc", "#7b4b35"),
            "skip": ("#fef3c7", "#92400e"),
            "skipped": ("#fef3c7", "#92400e"),
            "error": ("#fee2e2", "#991b1b"),
        }

    def _apply_status_style(self, label: QLabel, state: str) -> None:
        label.setMinimumHeight(28)
        colors = self._status_colors()
        background, color = colors.get(state, colors["wait"])
        if label.objectName() == "pipelineStatus":
            label.setStyleSheet(
                f"padding: 0; background: transparent; color: {color};"
            )
            return
        if label.objectName() == "readinessMessage":
            label.setStyleSheet(
                "padding: 0; background: transparent; "
                f"color: {self._theme_palette()['hint']};"
            )
            return
        label.setStyleSheet(
            f"padding: 5px 8px; border-radius: 6px; background: {background}; color: {color};"
        )

    def _apply_badge_style(self, label: QLabel, state: str) -> None:
        colors = self._status_colors()
        background, color = colors.get(state, colors["wait"])
        label.setStyleSheet(
            f"border-radius: 10px; padding: 3px 8px; font-size: 11px; "
            f"font-weight: 800; background: {background}; color: {color};"
        )

    def check_obs(self) -> None:
        try:
            message = self.recorder.check_connection()
        except RecorderError as error:
            self.status_label.setText(str(error))
            return
        self.obs_status_value.setText(self.recorder.status_text)
        self.status_label.setText(message)

    def end_workday(self) -> None:
        try:
            day_folder = self.storage.end_workday()
        except ValueError as error:
            self.status_label.setText(str(error))
            return
        self.status_label.setText(f"Рабочий день завершен. Итоги дня готовятся: {day_folder}")
        self._refresh_after_lifecycle_change()
        self._request_day_summary_update(day_folder, force=False)

    def open_review(self) -> None:
        self.pages.setCurrentIndex(1)
        self.refresh_review()

    def refresh_review(self) -> None:
        self._clear_layout(self.review_meeting_cards_layout)
        self.review_meeting_cards = {}
        day_folder = self.storage.get_today_day_folder()
        if day_folder is None:
            self._clear_review_editors()
            self.review_status_label.setText("Папка сегодняшнего рабочего дня пока не создана.")
            self.review_meetings_hint.setText("Папка дня еще не создана.")
            self.selected_review_meeting_folder = None
            self.review_day_summary_selected = False
            self._refresh_review_buttons()
            return

        meeting_folders = self._today_meeting_folders_newest_first()
        has_day_summary = self.storage.day_summary_exists(day_folder)
        if not has_day_summary:
            self.review_day_summary_selected = False
        if (
            not self.review_day_summary_selected
            and (
                self.selected_review_meeting_folder is None
                or self.selected_review_meeting_folder not in meeting_folders
            )
        ):
            if has_day_summary:
                self.review_day_summary_selected = True
                self.selected_review_meeting_folder = None
            else:
                self.review_day_summary_selected = False
                self.selected_review_meeting_folder = meeting_folders[0] if meeting_folders else None
        if has_day_summary:
            self.review_meeting_cards_layout.addWidget(
                self._create_review_day_summary_card(day_folder, self.review_day_summary_selected)
            )
        for meeting_folder in meeting_folders:
            self.review_meeting_cards_layout.addWidget(
                self._create_review_meeting_card(
                    meeting_folder,
                    not self.review_day_summary_selected
                    and meeting_folder == self.selected_review_meeting_folder,
                )
            )

        if self.review_day_summary_selected and has_day_summary:
            self.load_day_summary_review()
            self.review_meetings_hint.setText(
                "Выберите «Итоги дня» или встречу, чтобы проверить локальные черновики."
            )
            self.review_status_label.setText("Итоги дня загружены.")
        elif meeting_folders:
            self.load_selected_meeting(self.selected_review_meeting_folder)
            self.review_meetings_hint.setText(
                "Выберите встречу, чтобы проверить итоги и transcript."
            )
            self.review_status_label.setText("Локальные файлы ревью загружены.")
        else:
            self.meeting_summary_editor.clear()
            self.meeting_transcript_editor.clear()
            self.review_meetings_hint.setText("За выбранный день пока нет встреч.")
            self.review_status_label.setText("За сегодня пока нет встреч.")
        self._refresh_review_buttons()

    def _create_review_day_summary_card(self, day_folder: Path, selected: bool) -> QWidget:
        metadata = self.storage.read_day_summary_metadata(day_folder)
        card = ClickableFrame()
        card.setObjectName("activeMeetingCard" if selected else "meetingCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setToolTip("Нажмите, чтобы открыть итоги дня в ревью.")
        card.clicked.connect(self.select_review_day_summary)
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_label = QLabel("Итоги дня")
        header_label.setObjectName("meetingHeaderLabel")
        header_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge = QLabel()
        badge.setObjectName("statusBadge")
        badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge_text, badge_state = self._day_summary_badge(metadata)
        badge.setText(badge_text)
        self._apply_badge_style(badge, badge_state)
        header_layout.addWidget(header_label, 1)
        header_layout.addWidget(badge)
        layout.addLayout(header_layout)
        detail = QLabel(self._day_summary_detail_text(metadata))
        detail.setObjectName("sectionHint")
        detail.setWordWrap(True)
        detail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(detail)
        card.setLayout(layout)
        return card

    def _create_review_meeting_card(self, meeting_folder: Path, selected: bool) -> QWidget:
        metadata = self.storage.read_meeting_metadata(meeting_folder)
        card = ClickableFrame()
        card.setObjectName("activeMeetingCard" if selected else "meetingCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setToolTip("Нажмите, чтобы открыть встречу в ревью.")
        card.clicked.connect(lambda folder=meeting_folder: self.select_review_meeting(folder))
        self.review_meeting_cards[meeting_folder] = card
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_label = QLabel(self._meeting_header_text(meeting_folder, metadata))
        header_label.setObjectName("meetingHeaderLabel")
        header_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge = QLabel()
        badge.setObjectName("statusBadge")
        badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge_text, badge_state = self._meeting_badge(metadata)
        badge.setText(badge_text)
        self._apply_badge_style(badge, badge_state)
        header_layout.addWidget(header_label, 1)
        header_layout.addWidget(badge)
        layout.addLayout(header_layout)
        detail = QLabel(self._meeting_detail_text(metadata))
        detail.setObjectName("sectionHint")
        detail.setWordWrap(True)
        detail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(detail)
        card.setLayout(layout)
        return card

    def select_review_meeting(self, meeting_folder: Path) -> None:
        self.review_day_summary_selected = False
        self.selected_review_meeting_folder = meeting_folder
        self.refresh_review()

    def select_review_day_summary(self) -> None:
        self.review_day_summary_selected = True
        self.selected_review_meeting_folder = None
        self.refresh_review()

    def load_selected_meeting(self, meeting_folder: Path | None = None) -> None:
        if meeting_folder is None:
            self.meeting_summary_editor.clear()
            self.meeting_transcript_editor.clear()
            self._refresh_review_buttons()
            return
        self.review_tabs.setTabText(0, "Итоги встречи")
        self.review_tabs.setTabText(1, "Транскрипт")
        self.meeting_summary_editor.setReadOnly(False)
        self.meeting_summary_editor.setPlainText(
            self.storage.read_meeting_summary_draft(meeting_folder)
        )
        self.meeting_transcript_editor.setPlainText(self._read_meeting_transcript(meeting_folder))
        self._refresh_review_buttons()

    def load_day_summary_review(self) -> None:
        day_folder = self.storage.get_today_day_folder()
        self.review_tabs.setTabText(0, "Итоги встреч")
        self.review_tabs.setTabText(1, "Транскрипт")
        if day_folder is None:
            self.meeting_summary_editor.clear()
            self.meeting_transcript_editor.clear()
            self._refresh_review_buttons()
            return
        self.meeting_summary_editor.setReadOnly(False)
        self.meeting_summary_editor.setPlainText(self.storage.read_day_summary_draft(day_folder))
        self.meeting_transcript_editor.setHtml(self._day_transcript_links_html(day_folder))
        self._refresh_review_buttons()

    def _day_transcript_links_html(self, day_folder: Path) -> str:
        meeting_folders = self._today_meeting_folders_newest_first()
        if not meeting_folders:
            return "<p>За выбранный день пока нет встреч.</p>"
        rows = ["<h2>Транскрипты встреч за день</h2>"]
        for index, meeting_folder in enumerate(meeting_folders):
            metadata = self.storage.read_meeting_metadata(meeting_folder)
            title = str(metadata.get("title") or meeting_folder.name)
            started_at = self._short_time(metadata.get("started_at"))
            rows.append(
                "<p>"
                f"<b>{started_at} · {self._html_escape(title)}</b><br>"
                f"<a href=\"meeting-index://{index}\">"
                "Открыть транскрипт внутри приложения"
                "</a>"
                "</p>"
            )
        return "\n".join(rows)

    def _open_review_transcript_link(self, url: QUrl) -> None:
        if url.scheme() != "meeting-index":
            return
        try:
            index = int(url.host() or url.path().strip("/"))
        except ValueError:
            return
        meeting_folders = self._today_meeting_folders_newest_first()
        if index < 0 or index >= len(meeting_folders):
            return
        self.select_review_meeting(meeting_folders[index])
        self.review_tabs.setCurrentIndex(1)

    @staticmethod
    def _html_escape(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def _refresh_day_summary_review(self, day_folder: Path) -> None:
        del day_folder
        self.load_day_summary_review()

    @staticmethod
    def _read_meeting_transcript(meeting_folder: Path) -> str:
        transcript_path = meeting_folder / "transcript.md"
        if not transcript_path.is_file():
            return "# Транскрипт\n\n_Файл transcript.md пока не создан._\n"
        return transcript_path.read_text(encoding="utf-8")

    def save_drafts(self) -> None:
        selected_meeting = self._selected_meeting_folder()
        day_folder = self.storage.get_today_day_folder()
        if day_folder is None:
            self.review_status_label.setText("Папка сегодняшнего рабочего дня пока не создана.")
            return
        if self.review_day_summary_selected:
            self.storage.save_day_summary_draft(day_folder, self.meeting_summary_editor.toPlainText())
            self.review_status_label.setText("Черновик итогов дня сохранен локально.")
            return
        if selected_meeting is None:
            self.review_status_label.setText("Выберите встречу для сохранения черновиков.")
            return
        self.storage.save_meeting_summary_draft(
            selected_meeting, self.meeting_summary_editor.toPlainText()
        )
        if self.day_summary_editor.isEnabled():
            self.storage.save_day_summary_draft(day_folder, self.day_summary_editor.toPlainText())
        self.review_status_label.setText("Черновики сохранены локально.")

    def save_final_files(self) -> None:
        selected_meeting = self._selected_meeting_folder()
        day_folder = self.storage.get_today_day_folder()
        if self.review_day_summary_selected:
            if day_folder is None:
                self.review_status_label.setText("Папка сегодняшнего рабочего дня пока не создана.")
                return
            self.storage.save_day_summary_final(day_folder, self.meeting_summary_editor.toPlainText())
            self.review_status_label.setText("Финальные итоги дня сохранены локально. Черновик не удален.")
            return
        if selected_meeting is None:
            self.review_status_label.setText("Выберите встречу для сохранения финальных файлов.")
            return
        day_folder = selected_meeting.parent
        tasks_path = day_folder / "00_tasks_draft.md"
        tasks_text = tasks_path.read_text(encoding="utf-8") if tasks_path.is_file() else ""
        self.storage.save_final_files(
            selected_meeting,
            self.meeting_summary_editor.toPlainText(),
            self.day_summary_editor.toPlainText(),
            tasks_text,
        )
        self.review_status_label.setText("Финальные файлы сохранены локально. Черновики не удалены.")

    def save_final_summaries(self) -> None:
        self.save_final_files()

    def save_settings(self) -> None:
        config_path = Path("config.yaml")
        config_to_save = self._settings_config_from_ui()
        config_path.write_text(
            yaml.safe_dump(
                config_to_save,
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        self.config = load_config(config_path)
        self._apply_theme_settings()
        self._apply_runtime_settings_after_save()

    def _settings_config_from_ui(self) -> dict[str, object]:
        if hasattr(self, "settings_current_transcription_backend"):
            self._save_current_transcription_profile(
                self.settings_current_transcription_backend
            )
        return {
            "storage": {
                "root": self.settings_storage_root_input.text().strip() or "MeetingSummaries",
            },
            "obs": {
                "enabled": self.settings_obs_enabled_checkbox.isChecked(),
                "websocket_host": self.settings_obs_host_input.text().strip() or "localhost",
                "websocket_port": self.settings_obs_port_input.value(),
                "websocket_password": self.settings_obs_password_input.text(),
            },
            "secrets": {
                "env_file": self.settings_secrets_env_file_input.text().strip(),
            },
            "transcription": {
                "backend": self.settings_transcription_backend_select.currentText(),
                "backends": deepcopy(self.settings_transcription_profiles),
            },
            "summary": {
                "enabled": self.settings_summary_enabled_checkbox.isChecked(),
                "provider": "openai",
                "model": self._summary_model_from_settings(),
                "api_key_env": str(DEFAULT_CONFIG["summary"]["api_key_env"]),
                "base_url": str(DEFAULT_CONFIG["summary"]["base_url"]),
                "env_file": "",
                "timeout_seconds": self.settings_summary_timeout_input.value(),
                "max_chars_per_chunk": self.settings_summary_chunk_input.value(),
            },
            "ui": {
                "theme": self._combo_value(self.settings_theme_select),
                "floating_theme": self._combo_value(self.settings_floating_theme_select),
            },
        }

    def _apply_runtime_settings_after_save(self) -> None:
        if self._has_processing_work():
            self.settings_status_label.setText(
                "Настройки сохранены. Тема интерфейса применена сразу. "
                "Текущая обработка завершится со старой конфигурацией, "
                "следующие встречи будут использовать обновленные настройки."
            )
            return
        self.storage.transcriber = create_transcriber(self._transcription_runtime_config())
        self.storage.summarizer = create_summarizer(self._summary_runtime_config())
        self.settings_status_label.setText(
            "Настройки сохранены. Тема интерфейса применена сразу. "
            "Следующие встречи будут использовать обновленные настройки."
        )

    def open_day_folder(self) -> None:
        day_folder = self.storage.get_today_day_folder()
        if day_folder is None:
            message = "Папка сегодняшнего рабочего дня пока не создана."
            self.status_label.setText(message)
            self.review_status_label.setText(message)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(day_folder.resolve()))):
            message = f"Не удалось открыть папку дня: {day_folder}"
            self.status_label.setText(message)
            self.review_status_label.setText(message)

    def open_selected_meeting_folder(self) -> None:
        meeting_folder = self.selected_workday_meeting_folder
        if meeting_folder is None:
            self.status_label.setText("Выберите встречу, чтобы открыть ее папку.")
            return
        self.open_meeting_folder(meeting_folder)

    def open_meeting_folder(self, meeting_folder: Path) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(meeting_folder.resolve()))):
            self.status_label.setText(f"Не удалось открыть папку встречи: {meeting_folder}")

    def _selected_meeting_folder(self) -> Path | None:
        if self.review_day_summary_selected:
            return None
        return self.selected_review_meeting_folder

    def _startup_status(self) -> str:
        warnings = self.config.get("_warnings") or []
        if warnings:
            return " ".join(str(warning) for warning in warnings)
        if self.storage.meeting_active:
            return f"Восстановлена активная встреча: {self.storage.active_meeting_folder.name}"
        if self.storage.workday_active:
            return "Восстановлен активный рабочий день. Можно начать встречу."
        return "Готово. Начните рабочий день, когда потребуется."

    def _refresh_after_lifecycle_change(self) -> None:
        self.refresh_status()
        self.refresh_buttons()
        if self.pages.currentIndex() == 1:
            self.refresh_review()

    @staticmethod
    def _set_widget_object_name(widget: QWidget, object_name: str) -> None:
        if widget.objectName() == object_name:
            return
        widget.setObjectName(object_name)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    @staticmethod
    def _format_day_title(value: date) -> str:
        months = [
            "января",
            "февраля",
            "марта",
            "апреля",
            "мая",
            "июня",
            "июля",
            "августа",
            "сентября",
            "октября",
            "ноября",
            "декабря",
        ]
        return f"{value.day} {months[value.month - 1]} {value.year}"

    def _refresh_day_status_display(self, day_folder: Path | None) -> None:
        if not hasattr(self, "day_date_title_value"):
            return

        today_title = self._format_day_title(date.today())
        if self.storage.workday_active:
            self.day_status_badge.setText("Активен")
            self._apply_badge_style(self.day_status_badge, "active")
            self.day_date_title_value.setText(today_title)
            self.day_status_detail_value.setText(
                "Рабочий день активен. Можно запускать встречи и сохранять локальные итоги."
            )
        elif day_folder is not None:
            self.day_status_badge.setText("Не активен")
            self._apply_badge_style(self.day_status_badge, "wait")
            self.day_date_title_value.setText(today_title)
            self.day_status_detail_value.setText(
                "Рабочий день не активен. Можно переоткрыть день и продолжить работу в той же папке."
            )
        else:
            self.day_status_badge.setText("Не активен")
            self._apply_badge_style(self.day_status_badge, "wait")
            self.day_date_title_value.setText("Рабочий день не начат")
            self.day_status_detail_value.setText(
                "Начните рабочий день, чтобы записывать встречи и сохранять итоги локально."
            )

        if day_folder is not None:
            self.day_folder_badge.setText("Папка создана")
            self._apply_badge_style(self.day_folder_badge, "ok")
        else:
            self.day_folder_badge.setText("Папка не создана")
            self._apply_badge_style(self.day_folder_badge, "wait")

    def _refresh_active_call_display(self) -> None:
        if not hasattr(self, "active_call_title_value"):
            return
        if self.storage.meeting_active and self.storage.active_meeting_folder is not None:
            self._set_widget_object_name(self.active_call_panel, "activeCallInnerPanel")
            metadata = self.storage.read_meeting_metadata(self.storage.active_meeting_folder)
            self.active_call_title_value.setText(
                str(metadata.get("title") or self.storage.active_meeting_folder.name)
            )
            self.active_call_detail_value.setText(
                "OBS записывает встречу. После завершения начнется локальная обработка."
                if metadata.get("recording_status") == "recording"
                else "Встреча идет сейчас. Запись может быть отключена или недоступна."
            )
            self.active_call_timer_value.setText(self._elapsed_text(metadata.get("started_at")))
            badge_text, badge_state = self._meeting_badge(metadata)
            self.active_call_badge.setText(badge_text)
            self._apply_badge_style(self.active_call_badge, badge_state)
            self._refresh_floating_control()
            return

        self._set_widget_object_name(self.active_call_panel, "overviewInnerPanel")
        self.active_call_timer_value.setText("00:00:00")
        if self.storage.workday_active:
            self.active_call_title_value.setText("Нет активного созвона")
            self.active_call_detail_value.setText(
                "Можно начать новую встречу. Если предыдущая еще обрабатывается, она продолжит выполняться в фоне."
            )
            self.active_call_badge.setText("Ожидает")
            self._apply_badge_style(self.active_call_badge, "wait")
        else:
            self.active_call_title_value.setText("Нет активного созвона")
            self.active_call_detail_value.setText(
                "Сначала начните рабочий день, затем можно будет запустить встречу."
            )
            self.active_call_badge.setText("Не начат")
            self._apply_badge_style(self.active_call_badge, "wait")

    @staticmethod
    def _elapsed_text(started_at: object) -> str:
        if not started_at:
            return "00:00:00"
        try:
            started = datetime.fromisoformat(str(started_at))
        except ValueError:
            return "00:00:00"
        total_seconds = max(0, int((datetime.now() - started).total_seconds()))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def refresh_buttons(self) -> None:
        day_folder = self.storage.get_today_day_folder()
        has_day_folder = day_folder is not None
        has_today_meetings = bool(self.storage.list_today_meeting_folders()) if has_day_folder else False

        self.check_readiness_button.setEnabled(not self.pipeline_running and not self.day_summary_running)
        self._configure_workday_action_button()
        self.start_meeting_button.setEnabled(
            self.storage.workday_active and not self.storage.meeting_active
        )
        self.end_meeting_button.setEnabled(self.storage.meeting_active)
        self.start_meeting_button.setVisible(
            self.storage.workday_active and not self.storage.meeting_active
        )
        self.end_meeting_button.setVisible(self.storage.meeting_active)
        self.day_status_open_folder_button.setEnabled(has_day_folder)
        self.day_status_open_folder_button.setVisible(has_day_folder)
        self.open_day_folder_button.setEnabled(has_day_folder)
        self.open_day_folder_button.setVisible(has_day_folder and not has_today_meetings)
        self._refresh_review_buttons()

    def _configure_workday_action_button(self) -> None:
        mode = "end" if self.storage.workday_active else "start"
        if mode != self.workday_action_mode:
            try:
                self.workday_action_button.clicked.disconnect()
            except RuntimeError:
                pass
            if mode == "end":
                self.workday_action_button.setText("Завершить рабочий день")
                self.workday_action_button.setObjectName("dangerButton")
                self.workday_action_button.clicked.connect(self.end_workday)
            else:
                self.workday_action_button.setText("Начать рабочий день")
                self.workday_action_button.setObjectName("primaryButton")
                self.workday_action_button.clicked.connect(self.start_workday)
            self.workday_action_button.style().unpolish(self.workday_action_button)
            self.workday_action_button.style().polish(self.workday_action_button)
            self.workday_action_mode = mode
        self.workday_action_button.setEnabled(
            not self.storage.meeting_active
            and not self.day_summary_running
        )

    def refresh_status(self) -> None:
        day_folder = self.storage.get_today_day_folder()
        self.workday_status_value.setText("активен" if self.storage.workday_active else "не активен")
        self.meeting_status_value.setText("активна" if self.storage.meeting_active else "не активна")
        self.day_folder_value.setText(str(day_folder) if day_folder else "не создана")
        self.active_meeting_value.setText(
            self.storage.active_meeting_folder.name if self.storage.meeting_active else "нет"
        )
        self.obs_status_value.setText(self.recorder.status_text)
        self._refresh_day_status_display(day_folder)
        self._refresh_active_call_display()
        meeting_count = len(self.storage.list_today_meeting_folders()) if day_folder else 0
        if meeting_count == 0:
            if day_folder:
                self.today_meetings_value.setText(
                    "За выбранный день пока нет созданных встреч. Папку дня можно открыть кнопкой ниже."
                )
            else:
                self.today_meetings_value.setText(
                    "Папка дня еще не создана. Начните рабочий день, чтобы встречи появились здесь."
                )
        else:
            self.today_meetings_value.setText(
                f"Создано встреч за день: {meeting_count}. Нажмите на карточку, чтобы раскрыть pipeline конкретной встречи."
            )
        self._refresh_workday_meetings()

    def _refresh_review_buttons(self) -> None:
        day_folder = self.storage.get_today_day_folder()
        has_day_folder = day_folder is not None
        has_selected_meeting = self._selected_meeting_folder() is not None
        has_day_summary = (
            day_folder is not None
            and (day_folder / "00_day_summary_draft.md").is_file()
        )
        has_review_content = has_selected_meeting or (self.review_day_summary_selected and has_day_summary)
        self.review_open_folder_button.setEnabled(has_day_folder)
        self.save_drafts_button.setEnabled(
            has_day_folder and has_review_content and not self._has_processing_work()
        )
        self.save_final_files_button.setEnabled(
            has_day_folder
            and has_review_content
            and has_day_summary
            and not self._has_processing_work()
        )
        self._refresh_floating_control()

    def _has_processing_work(self) -> bool:
        return (
            self.pipeline_running
            or bool(self.processing_queue)
            or self.day_summary_running
            or self.day_summary_pending
        )

    def _clear_review_editors(self) -> None:
        self.meeting_summary_editor.clear()
        self.day_summary_editor.clear()
        self.meeting_transcript_editor.clear()
