from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime
from html import escape
from pathlib import Path
import re
import tempfile

import yaml

from PySide6.QtCore import (
    QEasingCurve,
    QEventLoop,
    QObject,
    Property,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QFileDialog,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.config import DEFAULT_CONFIG, load_config
from app.services.archive import (
    ArchiveDateFilter,
    ArchiveDay,
    ArchiveSearchMatch,
    build_archive_days,
    search_archive,
)
from app.services.readiness import READINESS_CARDS, check_readiness
from app.services.recorder import Recorder, RecorderError, create_recorder
from app.services.storage import MetadataReadError, StorageService
from app.services.summarization import build_summary_system_prompt, create_summarizer
from app.services.transcription import create_transcriber
from app.ui.summary_viewer import SummaryMaterialView


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


class NumericLineEdit(QLineEdit):
    def __init__(self, value: int, minimum: int, maximum: int, parent: QWidget | None = None) -> None:
        super().__init__(str(value), parent)
        self.minimum = minimum
        self.maximum = maximum

    def setValue(self, value: int) -> None:
        self.setText(str(value))

    def value(self) -> int:
        return int(self.text().strip())

    def validated_value(self, label: str) -> int:
        raw_value = self.text().strip()
        try:
            value = int(raw_value)
        except ValueError as error:
            raise ValueError(f"{label}: укажите целое число.") from error
        if value < self.minimum or value > self.maximum:
            raise ValueError(
                f"{label}: допустимое значение от {self.minimum} до {self.maximum}."
            )
        return value


class ThemeToggleButton(QPushButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self._animation = QPropertyAnimation(self, b"progress", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("themeToggleButton")
        self.setMinimumHeight(48)
        self.setText("Светлая тема")
        self.setToolTip("Переключить тему приложения")
        self.setAccessibleName("Переключить тему приложения")

    def sizeHint(self) -> QSize:
        return QSize(202, 48)

    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    progress = Property(float, get_progress, set_progress)

    def set_theme(self, theme: str, *, animated: bool = False) -> None:
        dark = theme == "dark"
        target = 1.0 if dark else 0.0
        self.setChecked(dark)
        self.setText("Темная тема" if dark else "Светлая тема")
        self.setAccessibleDescription(
            "Сейчас включена темная тема" if dark else "Сейчас включена светлая тема"
        )
        if animated and abs(self._progress - target) > 0.01:
            self._animation.stop()
            self._animation.setStartValue(self._progress)
            self._animation.setEndValue(target)
            self._animation.start()
            return
        self._animation.stop()
        self.set_progress(target)

    @staticmethod
    def _mix(start: str, end: str, progress: float) -> QColor:
        start_color = QColor(start)
        end_color = QColor(end)
        return QColor(
            int(start_color.red() + (end_color.red() - start_color.red()) * progress),
            int(
                start_color.green()
                + (end_color.green() - start_color.green()) * progress
            ),
            int(start_color.blue() + (end_color.blue() - start_color.blue()) * progress),
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        progress = self._progress
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        button_rect = QRectF(self.rect()).adjusted(14, 5, -14, -5)
        bg_color = self._mix("#fff8ef", "#111827", progress)
        border_color = self._mix("#ead8c6", "#374151", progress)
        text_color = self._mix("#3a1408", "#f9fafb", progress)
        accent_color = self._mix("#ff6f1a", "#60a5fa", progress)

        painter.setPen(QPen(border_color, 1))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(button_rect, 10, 10)

        switch_rect = QRectF(button_rect.left() + 10, button_rect.top() + 7, 58, 26)
        track_color = self._mix("#ffe4cc", "#1f2937", progress)
        painter.setPen(QPen(border_color, 1))
        painter.setBrush(QBrush(track_color))
        painter.drawRoundedRect(switch_rect, 13, 13)

        knob_size = 22
        knob_x = switch_rect.left() + 2 + (switch_rect.width() - knob_size - 4) * progress
        knob_rect = QRectF(knob_x, switch_rect.top() + 2, knob_size, knob_size)
        knob_color = self._mix("#ffffff", "#f8fafc", progress)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(knob_color))
        painter.drawEllipse(knob_rect)

        icon_center = knob_rect.center()
        icon_pen = QPen(accent_color, 1.6)
        icon_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(icon_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if progress < 0.5:
            painter.drawEllipse(icon_center, 4.2, 4.2)
            for dx, dy in [
                (0, -8),
                (0, 8),
                (-8, 0),
                (8, 0),
                (-5.6, -5.6),
                (5.6, -5.6),
                (-5.6, 5.6),
                (5.6, 5.6),
            ]:
                painter.drawLine(
                    QPointF(icon_center.x() + dx * 0.72, icon_center.y() + dy * 0.72),
                    QPointF(icon_center.x() + dx, icon_center.y() + dy),
                )
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(accent_color))
            painter.drawEllipse(icon_center, 6.2, 6.2)
            painter.setBrush(QBrush(knob_color))
            painter.drawEllipse(
                QRectF(icon_center.x() + 3.5, icon_center.y() - 2.5, 6.2, 6.2)
            )

        label_rect = QRectF(
            switch_rect.right() + 12,
            button_rect.top(),
            button_rect.right() - switch_rect.right() - 22,
            button_rect.height(),
        )
        painter.setPen(QPen(text_color))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text(),
        )


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


class ReadinessCheckWorker(QObject):
    finished = Signal(int, object, str)
    failed = Signal(int, str)

    def __init__(
        self,
        request_id: int,
        config: dict[str, object],
        recorder: Recorder,
        data_root: Path,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.config = config
        self.recorder = recorder
        self.data_root = data_root

    @Slot()
    def run(self) -> None:
        try:
            statuses = check_readiness(self.config, self.recorder, self.data_root)
        except Exception as error:
            self.failed.emit(self.request_id, str(error))
            return
        self.finished.emit(self.request_id, statuses, self.recorder.status_text)


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
            QLabel#overlayMessage {
                color: %(text)s;
                font-size: 14px;
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


class SafetyCloseOverlay(QWidget):
    confirmed = Signal()
    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("meetingOverlay")
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
        body_layout.setSpacing(12)

        self.title_label = QLabel()
        self.title_label.setObjectName("overlayTitle")
        self.title_label.setMinimumHeight(26)
        self.message_label = QLabel()
        self.message_label.setObjectName("overlayLabel")
        self.message_label.setWordWrap(True)

        body_layout.addWidget(self.title_label)
        body_layout.addWidget(self.message_label)
        body.setLayout(body_layout)

        footer = QWidget()
        footer.setObjectName("meetingOverlayFooter")
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(24, 14, 16, 14)
        footer_layout.setSpacing(10)
        footer_layout.addStretch(1)
        self.primary_button = QPushButton()
        self.primary_button.setObjectName("dialogPrimaryButton")
        self.primary_button.clicked.connect(self._dismiss)
        self.secondary_button = QPushButton("Закрыть и восстановить позже")
        self.secondary_button.setObjectName("dialogButton")
        self.secondary_button.clicked.connect(self._confirm)
        footer_layout.addWidget(self.primary_button)
        footer_layout.addWidget(self.secondary_button)
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

    def apply_theme(self, theme: str) -> None:
        self.setStyleSheet(StartMeetingOverlay._overlay_style(theme))

    def show_active_meeting_warning(self) -> None:
        self.title_label.setText("Идет активный созвон")
        self.message_label.setText(
            "Сейчас идет запись встречи. Закрытие приложения может нарушить "
            "управление записью. Сначала завершите встречу."
        )
        self.primary_button.setText("Понятно")
        self.secondary_button.hide()
        self._open()

    def show_background_processing_warning(self) -> None:
        self.title_label.setText("Идет обработка встречи")
        self.message_label.setText(
            "Приложение сейчас готовит материалы завершенной встречи. Если закрыть "
            "приложение сейчас, обработка остановится. При следующем запуске "
            "приложение попробует восстановить обработку с безопасного места."
        )
        self.primary_button.setText("Остаться в приложении")
        self.secondary_button.show()
        self._open()

    def show_day_summary_processing_warning(self) -> None:
        self.title_label.setText("Идет обновление итогов дня")
        self.message_label.setText(
            "Приложение сейчас готовит итоги дня. Если закрыть приложение сейчас, "
            "обработка остановится. При следующем запуске приложение попробует "
            "восстановить обновление итогов дня."
        )
        self.primary_button.setText("Остаться в приложении")
        self.secondary_button.show()
        self._open()

    def _open(self) -> None:
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()

    def _dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()

    def _confirm(self) -> None:
        self.hide()
        self.confirmed.emit()


class RiskyActionConfirmationOverlay(QWidget):
    confirmed = Signal()
    canceled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("meetingOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._confirmation_result = False
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
        body_layout.setSpacing(12)

        self.title_label = QLabel()
        self.title_label.setObjectName("overlayTitle")
        self.title_label.setMinimumHeight(26)
        self.message_label = QLabel()
        self.message_label.setObjectName("overlayMessage")
        self.message_label.setWordWrap(True)

        body_layout.addWidget(self.title_label)
        body_layout.addWidget(self.message_label)
        body.setLayout(body_layout)

        footer = QWidget()
        footer.setObjectName("meetingOverlayFooter")
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(24, 14, 16, 14)
        footer_layout.setSpacing(10)
        footer_layout.addStretch(1)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setObjectName("dialogButton")
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self._cancel)
        self.confirm_button = QPushButton()
        self.confirm_button.setObjectName("dialogPrimaryButton")
        self.confirm_button.setDefault(False)
        self.confirm_button.clicked.connect(self._confirm)
        footer_layout.addWidget(self.cancel_button)
        footer_layout.addWidget(self.confirm_button)
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

    def apply_theme(self, theme: str) -> None:
        self.setStyleSheet(StartMeetingOverlay._overlay_style(theme))

    def open_confirmation(
        self,
        title: str,
        text: str,
        confirm_button_text: str,
    ) -> None:
        self.title_label.setText(title)
        self.message_label.setText(text)
        self.confirm_button.setText(confirm_button_text)
        self.cancel_button.setDefault(True)
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.cancel_button.setFocus()

    def confirm_action(
        self,
        title: str,
        text: str,
        confirm_button_text: str,
    ) -> bool:
        loop = QEventLoop()

        def finish_confirmed() -> None:
            self._confirmation_result = True
            loop.quit()

        def finish_canceled() -> None:
            self._confirmation_result = False
            loop.quit()

        self.confirmed.connect(finish_confirmed)
        self.canceled.connect(finish_canceled)
        self._confirmation_result = False
        self.open_confirmation(title, text, confirm_button_text)
        loop.exec()
        self.confirmed.disconnect(finish_confirmed)
        self.canceled.disconnect(finish_canceled)
        return self._confirmation_result

    def _cancel(self) -> None:
        self.hide()
        self.canceled.emit()

    def _confirm(self) -> None:
        self.hide()
        self.confirmed.emit()

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
        details = "OBS пишет." if self._recorder_enabled else "OBS недоступен."
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
    READINESS_CARD_EXPANDED_HEIGHT = 274
    READINESS_CARD_COLLAPSED_HEIGHT = 86
    READINESS_GRID_HEIGHT = 184
    DAY_OVERVIEW_CARD_MIN_HEIGHT = 226
    PIPELINE_STEPS = [
        ("recording", "OBS запись", "✓"),
        ("audio", "Аудио", "A"),
        ("transcription", "Транскрипция", "T"),
        ("summary", "Итоги", "Σ"),
    ]
    DAY_SUMMARY_PIPELINE_STEPS = [
        ("collect", "Сбор итогов встреч", "1"),
        ("check", "Проверка итогов встреч", "2"),
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
        self.readiness_check_running = False
        self.readiness_check_request_id = 0
        self.readiness_check_thread: QThread | None = None
        self.readiness_check_worker: ReadinessCheckWorker | None = None
        self.readiness_check_pending_result: (
            tuple[int, list[dict[str, object]], str] | None
        ) = None
        self.readiness_check_pending_error: tuple[int, str] | None = None
        self.readiness_check_rerun_requested = False
        self.readiness_check_rerun_reason = ""
        self.readiness_startup_check_done = False
        self.last_readiness_statuses: list[dict[str, object]] | None = None
        self.readiness_check_stale = True
        self.pending_storage_root_path: Path | None = None
        self.pending_runtime_settings = False
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
        self.past_workday_folder: Path | None = self.storage.find_past_active_workday()
        self.past_workday_recovery_hidden = False
        self.past_workday_recovery_badge: QLabel | None = None
        self.past_workday_recovery_detail: QLabel | None = None
        self.readiness_badges: dict[str, QLabel] = {}
        self.readiness_tiles: dict[str, QWidget] = {}
        self.readiness_detail_rows: dict[str, QWidget] = {}
        self.readiness_detail_values: dict[str, dict[str, QLabel]] = {}
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
        self.archive_days: list[ArchiveDay] = []
        self.archive_matches: list[ArchiveSearchMatch] = []
        self.archive_period = "all"
        self.archive_query = ""
        self.selected_archive_day_folder: Path | None = None
        self.selected_archive_meeting_folder: Path | None = None
        self.archive_selected_material: str | None = None
        self.workday_action_mode: str | None = None
        self.allow_close_with_processing = False
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
        self.safety_close_overlay = SafetyCloseOverlay(container)
        self.safety_close_overlay.apply_theme(self.current_theme)
        self.safety_close_overlay.confirmed.connect(self._confirm_close_with_processing)
        self._resize_safety_close_overlay()
        self.risky_action_confirmation_overlay = RiskyActionConfirmationOverlay(container)
        self.risky_action_confirmation_overlay.apply_theme(self.current_theme)
        self._resize_risky_action_confirmation_overlay()
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
        self._restore_today_pending_processing_queue()
        self._restore_today_pending_day_summary_queue()

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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.readiness_startup_check_done:
            self.readiness_startup_check_done = True
            QTimer.singleShot(
                0,
                lambda: self._schedule_readiness_autocheck("startup"),
            )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_start_meeting_overlay()
        self._resize_safety_close_overlay()
        self._resize_risky_action_confirmation_overlay()

    def _resize_start_meeting_overlay(self) -> None:
        if not hasattr(self, "start_meeting_overlay"):
            return
        parent = self.start_meeting_overlay.parentWidget()
        if parent is not None:
            self.start_meeting_overlay.setGeometry(parent.rect())

    def _resize_safety_close_overlay(self) -> None:
        if not hasattr(self, "safety_close_overlay"):
            return
        parent = self.safety_close_overlay.parentWidget()
        if parent is not None:
            self.safety_close_overlay.setGeometry(parent.rect())

    def _resize_risky_action_confirmation_overlay(self) -> None:
        if not hasattr(self, "risky_action_confirmation_overlay"):
            return
        parent = self.risky_action_confirmation_overlay.parentWidget()
        if parent is not None:
            self.risky_action_confirmation_overlay.setGeometry(parent.rect())

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
        self._start_meeting_with_title(title, source="floating")
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
        self._add_nav_button(layout, 2, "Архив", self.open_archive)
        self._add_nav_button(layout, 3, "Настройки", lambda: self.pages.setCurrentIndex(3))
        self._add_nav_button(layout, 4, "Справка", lambda: self.pages.setCurrentIndex(4))
        layout.addStretch()
        self.theme_toggle_button = ThemeToggleButton()
        self.theme_toggle_button.set_theme(self.current_theme)
        self.theme_toggle_button.clicked.connect(self.toggle_theme_from_sidebar)
        layout.addWidget(self.theme_toggle_button)
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

    def toggle_theme_from_sidebar(self) -> None:
        previous_theme = self._configured_theme()
        next_theme = "dark" if previous_theme == "light" else "light"
        try:
            config_to_save = self._config_with_sidebar_theme(next_theme)
            self._write_local_config(config_to_save)
        except (OSError, ValueError) as error:
            if hasattr(self, "theme_toggle_button"):
                self.theme_toggle_button.set_theme(previous_theme)
            if hasattr(self, "status_label"):
                self.status_label.setText(f"Тема не изменена: {error}")
            return

        self.config.setdefault("ui", {})["theme"] = next_theme
        if hasattr(self, "settings_theme_select"):
            self._set_combo_value(self.settings_theme_select, next_theme)
        self._apply_theme_settings(update_theme_toggle=False)
        if hasattr(self, "theme_toggle_button"):
            self.theme_toggle_button.set_theme(next_theme, animated=True)
        if hasattr(self, "status_label"):
            self.status_label.setText(
                "Включена темная тема." if next_theme == "dark" else "Включена светлая тема."
            )

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
            QWidget#archiveHeader,
            QWidget#archiveNoMatchesSpacer,
            QWidget#scrollViewport,
            QWidget#archiveScrollViewport {
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
            QWidget#card,
            QWidget#archiveSearchCard,
            QWidget#archiveDetailCard {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
            }
            QWidget#settingsTemplatePane {
                background: %(surface_alt)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
            }
            QFrame#settingsInnerCard {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
            }
            QFrame#settingsTemplateStructurePanel,
            QFrame#settingsTemplateSidePanel {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
            }
            QLabel#settingsSectionNumber {
                background: %(disabled_bg)s;
                color: %(muted)s;
                border-radius: 7px;
                font-size: 12px;
                font-weight: 800;
                qproperty-alignment: AlignCenter;
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
                min-height: 150px;
                max-height: 150px;
                min-width: 300px;
            }
            QLabel#readinessTitle {
                color: %(text)s;
                font-weight: 800;
            }
            QLabel#readinessDetailLabel {
                color: %(hint)s;
                font-size: 12px;
            }
            QLabel#readinessDetailValue {
                color: %(text)s;
                font-size: 13px;
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
            QPushButton#settingsSectionButton {
                background: %(surface)s;
                color: %(muted)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 8px 18px;
                min-width: 108px;
                font-weight: 800;
            }
            QPushButton#settingsSectionButton:hover {
                color: %(accent)s;
                border-color: %(accent)s;
            }
            QPushButton#settingsSectionButton:checked {
                background: %(accent)s;
                color: #ffffff;
                border-color: %(accent)s;
            }
            QPushButton#compactButton,
            QPushButton#compactDangerButton {
                padding: 4px 10px;
                min-height: 24px;
                max-height: 30px;
                min-width: 34px;
                font-weight: 800;
            }
            QPushButton#compactDangerButton {
                background: %(danger)s;
                color: #ffffff;
                border-color: %(danger)s;
            }
            QPushButton#compactDangerButton:disabled {
                background: %(disabled_bg)s;
                color: %(disabled_text)s;
                border-color: %(border)s;
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
            QScrollArea#settingsScrollArea,
            QScrollArea#archiveDaysScroll,
            QScrollArea#archiveResultsScroll {
                background: %(bg)s;
            }
            QWidget#archiveDaysList,
            QWidget#archiveResultsList {
                background: %(bg)s;
            }
            QPushButton#archivePeriodButton {
                min-width: 86px;
                max-width: 86px;
                padding: 6px 10px;
            }
            QPushButton#archivePeriodButton:checked {
                background: %(accent)s;
                color: #ffffff;
                border-color: %(accent)s;
            }
            QPushButton#archiveDayCard {
                background: %(surface)s;
                color: %(text)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 0;
                text-align: left;
            }
            QPushButton#archiveDayCard:hover {
                border-color: %(accent)s;
                color: %(text)s;
            }
            QPushButton#archiveDayCard[selected="true"] {
                background: %(surface_soft)s;
                border-color: %(accent)s;
            }
            QWidget#archiveSearchResult {
                background: %(surface_soft)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
            }
            QLabel#archiveSearchTitle {
                color: %(text)s;
                font-weight: 800;
            }
            QLabel#archiveSearchSnippet {
                color: %(hint)s;
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

    def _apply_theme_settings(self, *, update_theme_toggle: bool = True) -> None:
        self._apply_app_style()
        if update_theme_toggle and hasattr(self, "theme_toggle_button"):
            self.theme_toggle_button.set_theme(self.current_theme)
        if hasattr(self, "start_meeting_overlay"):
            self.start_meeting_overlay.apply_theme(self.current_theme)
        if hasattr(self, "safety_close_overlay"):
            self.safety_close_overlay.apply_theme(self.current_theme)
        if hasattr(self, "risky_action_confirmation_overlay"):
            self.risky_action_confirmation_overlay.apply_theme(self.current_theme)
        if hasattr(self, "floating_control"):
            self.floating_control.apply_theme(self._effective_floating_theme())
        if hasattr(self, "readiness_detail_values"):
            for values in self.readiness_detail_values.values():
                for label in values.values():
                    state = str(label.property("readiness_state") or "neutral")
                    self._apply_readiness_detail_style(label, state)
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

    def _create_past_workday_recovery_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("activeMeetingCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel("Найден незавершенный рабочий день")
        title_label.setObjectName("meetingHeaderLabel")
        self.past_workday_recovery_badge = QLabel("Найден")
        self.past_workday_recovery_badge.setObjectName("statusBadge")
        self._apply_badge_style(self.past_workday_recovery_badge, "active")
        header_layout.addWidget(title_label, 1)
        header_layout.addWidget(self.past_workday_recovery_badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header_layout)

        self.past_workday_recovery_detail = QLabel()
        self.past_workday_recovery_detail.setObjectName("sectionHint")
        self.past_workday_recovery_detail.setWordWrap(True)
        layout.addWidget(self.past_workday_recovery_detail)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)
        self.recover_past_workday_button = self._add_button(
            actions_layout,
            "Завершить день и сформировать итоги",
            self.recover_past_workday,
            "primaryButton",
        )
        self.open_past_workday_folder_button = self._add_button(
            actions_layout,
            "Открыть папку дня",
            self.open_past_workday_folder,
        )
        self.hide_past_workday_card_button = self._add_button(
            actions_layout,
            "Скрыть",
            self.hide_past_workday_recovery_card,
            "headerButton",
        )
        actions_layout.addStretch(1)
        layout.addLayout(actions_layout)

        card.setLayout(layout)
        self.past_workday_recovery_card = card
        self._refresh_past_workday_recovery_card()
        return card

    def _refresh_past_workday_recovery_card(self) -> None:
        card = getattr(self, "past_workday_recovery_card", None)
        if card is None:
            return
        if self.past_workday_folder is None or self.past_workday_recovery_hidden:
            card.hide()
            return
        card.show()
        badge_text, badge_state = self._past_workday_recovery_badge()
        if self.past_workday_recovery_badge is not None:
            self.past_workday_recovery_badge.setText(badge_text)
            self._apply_badge_style(self.past_workday_recovery_badge, badge_state)
        if self.past_workday_recovery_detail is not None:
            self.past_workday_recovery_detail.setText(
                self._past_workday_recovery_detail_text()
            )

    def _past_workday_recovery_badge(self) -> tuple[str, str]:
        day_folder = self.past_workday_folder
        if day_folder is None:
            return "Скрыто", "wait"
        try:
            day_metadata = self.storage.read_day_metadata(day_folder)
        except MetadataReadError:
            return "Требует внимания", "error"
        if day_metadata.get("status") == "active":
            return "Найден", "active"
        try:
            has_unfinished_meetings = self.storage.has_unfinished_meeting_processing(day_folder)
        except MetadataReadError:
            return "Требует внимания", "error"
        if has_unfinished_meetings:
            return "Обработка встреч", "active"
        if self.day_summary_running and self.day_summary_day_folder == day_folder:
            return "Формируются итоги дня", "active"
        if self.storage.day_summary_exists(day_folder):
            metadata = self.storage.read_day_summary_metadata(day_folder)
            status = metadata.get("day_summary_status")
            if status in {"draft_created", "up_to_date"}:
                return "Итоги готовы", "ok"
            if status == "running":
                return "Формируются итоги дня", "active"
            if status == "waiting_for_meetings":
                return "Обработка встреч", "active"
            if status in {"failed", "openai_unavailable"}:
                return "Требует внимания", "error"
        return "Формируются итоги дня", "active"

    def _past_workday_recovery_detail_text(self) -> str:
        day_folder = self.past_workday_folder
        if day_folder is None:
            return ""
        try:
            day_metadata = self.storage.read_day_metadata(day_folder)
        except MetadataReadError as error:
            return f"Metadata дня поврежден и сохранен в backup: {error.backup_path}"
        meeting_count = len(self.storage.list_meeting_folders(day_folder))
        try:
            unfinished = self.storage.has_unfinished_meeting_processing(day_folder)
        except MetadataReadError as error:
            return f"Metadata встречи поврежден и сохранен в backup: {error.backup_path}"
        summary_ready = False
        if self.storage.day_summary_exists(day_folder):
            summary_metadata = self.storage.read_day_summary_metadata(day_folder)
            summary_ready = summary_metadata.get("day_summary_status") in {
                "draft_created",
                "up_to_date",
            }
        workday_date = str(day_metadata.get("date") or day_folder.name)
        processing_text = (
            "есть незавершенная обработка встреч"
            if unfinished
            else "незавершенной обработки встреч нет"
        )
        summary_text = "итоги дня готовы" if summary_ready else "итоги дня еще не готовы"
        return f"Дата: {workday_date}. Встреч: {meeting_count}. {processing_text}, {summary_text}."

    def hide_past_workday_recovery_card(self) -> None:
        self.past_workday_recovery_hidden = True
        self._refresh_past_workday_recovery_card()
        self.status_label.setText(
            "Карточка прошлого рабочего дня скрыта до следующего запуска приложения."
        )

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

    def _create_readiness_tile(
        self,
        component: str,
        detail_labels: tuple[str, ...],
    ) -> QWidget:
        tile = QFrame()
        tile.setObjectName("readinessTile")
        tile.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tile.setFrameShape(QFrame.Shape.StyledPanel)
        tile.setFrameShadow(QFrame.Shadow.Plain)
        tile.setFixedHeight(150)
        tile.setMinimumWidth(300)
        tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        tile_layout = QVBoxLayout()
        tile_layout.setContentsMargins(12, 10, 12, 10)
        tile_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        title_label = QLabel(component)
        title_label.setObjectName("readinessTitle")
        title_label.setFixedHeight(34)
        title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        badge_label = QLabel("Не проверено")
        badge_label.setObjectName("statusBadge")
        badge_label.setProperty("readinessBadge", True)
        badge_label.setFixedHeight(28)
        badge_label.setMinimumWidth(42)
        badge_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_badge_style(badge_label, "wait")
        header_layout.addWidget(title_label, 1, Qt.AlignmentFlag.AlignTop)
        header_layout.addStretch()
        header_layout.addWidget(badge_label, 0, Qt.AlignmentFlag.AlignTop)

        details_widget = QWidget()
        details_layout = QGridLayout()
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setHorizontalSpacing(8)
        details_layout.setVerticalSpacing(5)
        value_labels: dict[str, QLabel] = {}
        for row, detail_label in enumerate(detail_labels):
            label = QLabel(detail_label)
            label.setObjectName("readinessDetailLabel")
            label.setMinimumWidth(88)
            value = QLabel("Не проверено")
            value.setObjectName("readinessDetailValue")
            value.setProperty("readiness_state", "neutral")
            value.setWordWrap(True)
            value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            details_layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            details_layout.addWidget(value, row, 1, Qt.AlignmentFlag.AlignTop)
            value_labels[detail_label] = value
        details_layout.setColumnStretch(1, 1)
        details_widget.setLayout(details_layout)

        self.readiness_tiles[component] = tile
        self.readiness_badges[component] = badge_label
        self.readiness_detail_rows[component] = details_widget
        self.readiness_detail_values[component] = value_labels
        tile_layout.addLayout(header_layout, 0)
        tile_layout.addWidget(details_widget, 0, Qt.AlignmentFlag.AlignTop)
        tile_layout.addStretch(1)
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
                "Этапы итогов дня: сбор итогов встреч, генерация выжимки и ссылки на транскрипты."
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
            needs_attention = self._meeting_needs_attention(metadata)
            if self._should_show_reprocess_button(meeting_folder, metadata):
                self._add_button(
                    actions_layout,
                    "Повторить обработку",
                    lambda checked=False, folder=meeting_folder: self.reprocess_meeting(folder),
                    "primaryButton" if needs_attention else None,
                )
            if self._meeting_summary_is_ready(meeting_folder, metadata):
                self._add_button(
                    actions_layout,
                    "Открыть итоги встречи",
                    lambda checked=False, folder=meeting_folder: self.open_meeting_summary_review(folder),
                    None if needs_attention else "primaryButton",
                )
            actions_layout.addStretch(1)
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
            and metadata.get("processing_status") not in {"pending", "running"}
            and not self.day_summary_running
            and meeting_folder != self.pipeline_meeting_folder
            and meeting_folder not in self.processing_queue
            and self._is_reprocessable_result(metadata)
            and self._meeting_has_reprocess_source(metadata)
        )

    def _should_show_reprocess_button(
        self,
        meeting_folder: Path,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        return self._can_reprocess_meeting(meeting_folder, metadata)

    def _meeting_summary_is_ready(
        self,
        meeting_folder: Path,
        metadata: dict[str, object],
    ) -> bool:
        return self.storage.meeting_summary_is_ready(meeting_folder, metadata)

    def _is_reprocessable_result(self, metadata: dict[str, object]) -> bool:
        badge_text, _ = self._meeting_badge(metadata)
        return badge_text in {"Требует внимания", "Итоги готовы"}

    @staticmethod
    def _meeting_has_reprocess_source(metadata: dict[str, object]) -> bool:
        recording_path = metadata.get("recording_path")
        if not recording_path:
            return False
        try:
            return Path(str(recording_path)).is_file()
        except OSError:
            return False

    def reprocess_meeting(self, meeting_folder: Path) -> None:
        metadata = self.storage.read_meeting_metadata(meeting_folder)
        if not self._can_reprocess_meeting(meeting_folder, metadata):
            self.status_label.setText(
                "Эту встречу сейчас нельзя повторно обработать: проверьте статус встречи, очередь "
                "обработки и наличие файла записи."
            )
            return
        if not self._confirm_risky_action(
            "Повторить обработку встречи?",
            "Если вы вручную меняли Итог встречи, новая обработка заменит ваши изменения.",
            "Повторить обработку",
        ):
            self.status_label.setText("Повторная обработка встречи отменена.")
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
    def _clear_layout(layout, preserve: set[QWidget] | None = None) -> None:
        preserve = preserve or set()
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            if child_layout is not None:
                MainWindow._clear_layout(child_layout, preserve)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                if widget not in preserve:
                    widget.deleteLater()

    def _meeting_header_text(self, meeting_folder: Path, metadata: dict[str, object]) -> str:
        title = str(metadata.get("title") or meeting_folder.name)
        started_at = self._short_time(metadata.get("started_at"))
        duration = self._duration_text(metadata)
        return f"{started_at}   {title}   {duration}"

    def _meeting_detail_text(self, metadata: dict[str, object]) -> str:
        parts = []
        if self._meeting_has_no_recording(metadata):
            parts.append("запись не создана")
        elif self._meeting_needs_attention(metadata):
            parts.append("pipeline требует внимания")
        elif metadata.get("summary_status") == "disabled":
            parts.append("итоги выключены")
        if metadata.get("summary_status") == "draft_created":
            parts.append("итоги готовы")
        elif metadata.get("summary_status") == "skipped":
            parts.append("итоги не сформированы")
        elif metadata.get("processing_status") == "running":
            parts.append("обработка выполняется")
        elif metadata.get("processing_status") == "pending":
            parts.append("ждет обработки встречи")
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
            return "Ждет обработки встречи", "wait"
        if self._meeting_has_no_recording(metadata):
            return "Без записи", "error"
        if self._meeting_needs_attention(metadata):
            return "Требует внимания", "error"
        if metadata.get("summary_status") == "draft_created":
            return "Итоги готовы", "ok"
        if metadata.get("summary_status") == "disabled":
            return "Итоги выключены", "skip"
        return "Ждет обработки встречи", "wait"

    @staticmethod
    def _meeting_has_no_recording(metadata: dict[str, object]) -> bool:
        if metadata.get("status") == "active":
            return False
        recording_status = str(metadata.get("recording_status") or "")
        if recording_status in {"disabled", "start_failed", "stop_failed"}:
            return True
        return recording_status == "stopped" and not metadata.get("recording_path")

    @staticmethod
    def _meeting_needs_attention(metadata: dict[str, object]) -> bool:
        if metadata.get("processing_status") == "failed":
            return True
        if metadata.get("transcription_quality") == "suspect":
            return True
        if str(metadata.get("audio_status") or "") in {
            "missing_recording",
            "ffmpeg_unavailable",
            "failed",
        }:
            return True
        if str(metadata.get("transcription_status") or "") in {
            "missing_audio",
            "whisper_unavailable",
            "faster_whisper_unavailable",
            "aitunnel_unavailable",
            "file_too_large",
            "failed",
        }:
            return True
        return str(metadata.get("summary_status") or "") in {
            "skipped",
            "openai_unavailable",
            "failed",
        }

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
                return "Итоги встреч проверены."
            if missing:
                return f"Есть встречи без итогов: {len(missing)}."
            return "Проверка итогов встреч еще не выполнялась."
        if step == "generate":
            if metadata.get("day_summary_error"):
                return str(metadata["day_summary_error"])
            if state == "ok":
                return "00_day_summary.md готов."
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
        if self.storage.meeting_active:
            event.ignore()
            self.safety_close_overlay.show_active_meeting_warning()
            return
        if self._has_meeting_processing_work():
            if self.allow_close_with_processing:
                if hasattr(self, "floating_control"):
                    self.floating_control.close_from_app()
                super().closeEvent(event)
                return
            event.ignore()
            self.safety_close_overlay.show_background_processing_warning()
            return
        if self._has_day_summary_processing_work():
            if self.allow_close_with_processing:
                if hasattr(self, "floating_control"):
                    self.floating_control.close_from_app()
                super().closeEvent(event)
                return
            event.ignore()
            self.safety_close_overlay.show_day_summary_processing_warning()
            return
        if self.readiness_check_running:
            event.ignore()
            self.status_label.setText(
                "Дождитесь завершения проверки готовности перед закрытием приложения."
            )
            return
        if hasattr(self, "floating_control"):
            self.floating_control.close_from_app()
        super().closeEvent(event)

    def _confirm_close_with_processing(self) -> None:
        self.allow_close_with_processing = True
        self.close()

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
        layout.addWidget(self._create_past_workday_recovery_card())

        readiness_layout = QGridLayout()
        readiness_layout.setContentsMargins(0, 0, 0, 0)
        readiness_layout.setHorizontalSpacing(10)
        readiness_layout.setVerticalSpacing(10)
        for index, card in enumerate(READINESS_CARDS):
            row = index // 4
            column = index % 4
            readiness_layout.addWidget(
                self._create_readiness_tile(
                    str(card["component"]),
                    tuple(card["initial_details"]),
                ),
                row,
                column,
                Qt.AlignmentFlag.AlignTop,
            )
        for column in range(4):
            readiness_layout.setColumnStretch(column, 1)
        readiness_layout.setRowMinimumHeight(0, 150)
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
                "Проверьте итоги выбранной встречи и итог дня перед сохранением.",
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
        self.review_summary_view = SummaryMaterialView("Итоги встречи")
        self.review_summary_view.save_requested.connect(self.save_review_summary)
        self.meeting_summary_editor = self.review_summary_view.editor
        self.meeting_transcript_editor = QTextBrowser()
        self.meeting_transcript_editor.setReadOnly(True)
        self.meeting_transcript_editor.setOpenLinks(False)
        self.meeting_transcript_editor.anchorClicked.connect(self._open_review_transcript_link)
        self.day_summary_editor = self.meeting_summary_editor
        self.review_tabs.addTab(self.review_summary_view, "Итоги встречи")
        self.review_tabs.addTab(self.meeting_transcript_editor, "Транскрипт")
        review_content_layout.addWidget(self.review_tabs, 1)
        content_layout.addLayout(review_content_layout, 1)
        layout.addLayout(content_layout, 1)

        self.review_status_label = QLabel("Откройте ревью, чтобы загрузить локальные итоги.")
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

        sections_layout = QVBoxLayout()
        sections_layout.setSpacing(12)
        sections_hint = QLabel(
            "Настройки разделены по смыслу, чтобы экран не превращался в длинную простыню."
        )
        sections_hint.setObjectName("sectionHint")
        sections_hint.setWordWrap(True)
        sections_layout.addWidget(sections_hint)

        section_buttons_layout = QHBoxLayout()
        section_buttons_layout.setSpacing(8)
        self.settings_section_buttons: dict[str, QPushButton] = {}
        for index, title in enumerate(("Основное", "Запись", "Транскрипция", "Итоги")):
            button = QPushButton(title)
            button.setObjectName("settingsSectionButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _=False, current_index=index: self._show_settings_section(current_index)
            )
            self.settings_section_buttons[title] = button
            section_buttons_layout.addWidget(button)
        section_buttons_layout.addStretch(1)
        sections_layout.addLayout(section_buttons_layout)
        layout.addWidget(self._create_card("Разделы настроек", sections_layout))

        self.settings_sections = QStackedWidget()
        self.settings_basic_section = self._create_settings_basic_tab()
        self.settings_recording_section = self._create_settings_recording_tab()
        self.settings_transcription_section = self._create_settings_transcription_tab()
        self.settings_summary_section = self._create_settings_summary_tab()
        self.settings_sections.addWidget(self.settings_basic_section)
        self.settings_sections.addWidget(self.settings_recording_section)
        self.settings_sections.addWidget(self.settings_transcription_section)
        self.settings_sections.addWidget(self.settings_summary_section)
        layout.addWidget(self.settings_sections)
        self._show_settings_section(3)

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

    def _show_settings_section(self, index: int) -> None:
        self.settings_sections.setCurrentIndex(index)
        for button_index, button in enumerate(self.settings_section_buttons.values()):
            button.setChecked(button_index == index)

    def _create_settings_basic_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        storage_layout = QVBoxLayout()
        storage_layout.setSpacing(8)
        self.settings_storage_root_input = QLineEdit(str(self.config["storage"]["root"]))
        self.settings_storage_root_input.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.settings_storage_root_browse_button = QPushButton("Выбрать папку")
        self.settings_storage_root_browse_button.setObjectName("headerButton")
        self.settings_storage_root_browse_button.clicked.connect(
            self.choose_storage_root_folder
        )
        storage_root_row = QHBoxLayout()
        storage_root_row.setContentsMargins(0, 0, 0, 0)
        storage_root_row.setSpacing(8)
        storage_root_row.addWidget(self.settings_storage_root_input, 1)
        storage_root_row.addWidget(self.settings_storage_root_browse_button)
        storage_root_widget = QWidget()
        storage_root_widget.setLayout(storage_root_row)
        storage_layout.addWidget(
            self._create_settings_field_row("Папка данных:", storage_root_widget)
        )
        layout.addWidget(self._create_card("Хранение", storage_layout))

        secrets_layout = QVBoxLayout()
        secrets_layout.setSpacing(8)
        self.settings_secrets_env_file_input = QLineEdit(
            str(self.config.get("secrets", {}).get("env_file", ""))
        )
        secrets_layout.addWidget(
            self._create_settings_field_row(
                ".env файл:",
                self.settings_secrets_env_file_input,
                "Один локальный .env файл для API-ключей внешних сервисов. Например, для AITUNNEL_KEY. Сам файл не добавляется в git.",
            )
        )
        layout.addWidget(self._create_card("Секреты", secrets_layout))

        ui_layout = QVBoxLayout()
        ui_layout.setSpacing(8)
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
        ui_layout.addWidget(
            self._create_settings_field_row("Тема приложения:", self.settings_theme_select)
        )
        ui_layout.addWidget(
            self._create_settings_field_row(
                "Тема плавающей кнопки:",
                self.settings_floating_theme_select,
                "Тема основного окна и плавающей кнопки применяется сразу после сохранения.",
            )
        )
        layout.addWidget(self._create_card("Интерфейс", ui_layout))

        layout.addStretch(1)
        page.setLayout(layout)
        return page

    def _create_settings_recording_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        obs_layout = QVBoxLayout()
        obs_layout.setSpacing(8)
        self.settings_obs_host_input = QLineEdit(str(self.config["obs"]["websocket_host"]))
        self.settings_obs_port_input = NumericLineEdit(
            int(self.config["obs"]["websocket_port"]),
            1,
            65535,
        )
        self.settings_obs_password_input = QLineEdit(str(self.config["obs"]["websocket_password"]))
        self.settings_obs_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        obs_layout.addWidget(
            self._create_settings_field_row("Адрес WebSocket:", self.settings_obs_host_input)
        )
        obs_layout.addWidget(
            self._create_settings_field_row("Порт WebSocket:", self.settings_obs_port_input)
        )
        obs_layout.addWidget(
            self._create_settings_field_row(
                "Пароль WebSocket:",
                self.settings_obs_password_input,
                "OBS обязателен для записи разговора. Путь записи настраивается в самом OBS; приложение сохраняет путь, если OBS возвращает его после остановки записи.",
            )
        )
        layout.addWidget(self._create_card("Запись разговора (OBS)", obs_layout))

        layout.addStretch(1)
        page.setLayout(layout)
        return page

    def _create_settings_transcription_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

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
        self.settings_transcription_timeout_input = NumericLineEdit(300, 1, 3600)
        self.settings_transcription_upload_limit_input = NumericLineEdit(25, 1, 25)
        self.settings_transcription_chunking_checkbox = QCheckBox(
            "Нарезать длинные записи автоматически"
        )
        self.settings_transcription_chunk_duration_input = NumericLineEdit(300, 30, 3600)
        self.settings_transcription_retry_attempts_input = NumericLineEdit(2, 0, 10)
        self.settings_transcription_vad_checkbox = QCheckBox(
            "Для faster-whisper отсекать тишину и неречевой шум"
        )
        transcription_hint = QLabel(
            "Язык транскрипции всегда русский. API key для AI Tunnel берется из блока "
            "`Секреты` и переменной AITUNNEL_KEY."
        )
        transcription_hint.setObjectName("sectionHint")
        transcription_hint.setWordWrap(True)
        transcription_layout.addRow("Способ транскрипции:", self.settings_transcription_backend_select)
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
            "Лимит ожидания ответа, сек.:",
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
            "Длительность одной части, сек.:",
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

        layout.addStretch(1)
        page.setLayout(layout)
        return page

    def _create_settings_summary_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        summary_layout = QVBoxLayout()
        summary_layout.setSpacing(8)
        self.settings_summary_enabled_checkbox = QCheckBox("Генерация итогов включена")
        self.settings_summary_enabled_checkbox.setChecked(bool(self.config["summary"]["enabled"]))
        self.settings_summary_model_select = QComboBox()
        for label, value in SUMMARY_MODEL_OPTIONS:
            self.settings_summary_model_select.addItem(label, value)
        self.settings_summary_custom_model_input = QLineEdit()
        self.settings_summary_custom_model_input.setPlaceholderText(
            "Например: deepseek-r1, gemini-..., claude-..."
        )
        self.settings_summary_timeout_input = NumericLineEdit(
            int(self.config["summary"]["timeout_seconds"]),
            1,
            3600,
        )
        self.settings_summary_chunk_input = NumericLineEdit(
            int(self.config["summary"]["max_chars_per_chunk"]),
            1000,
            200000,
        )
        summary_hint = QLabel(
            "Итоги используют AI Tunnel. API key берется из блока `Секреты` "
            "и переменной AITUNNEL_KEY."
        )
        summary_hint.setObjectName("sectionHint")
        summary_hint.setWordWrap(True)
        summary_layout.addWidget(
            self._create_settings_field_row(
                "Генерация:",
                self.settings_summary_enabled_checkbox,
            )
        )
        summary_layout.addWidget(
            self._create_settings_field_row("Модель:", self.settings_summary_model_select)
        )
        self.settings_summary_custom_model_label = QLabel("ID модели:")
        self.settings_summary_custom_model_row = self._create_settings_field_row(
            "ID модели:",
            self.settings_summary_custom_model_input,
            label_widget=self.settings_summary_custom_model_label,
        )
        summary_layout.addWidget(self.settings_summary_custom_model_row)
        summary_layout.addWidget(
            self._create_settings_field_row(
                "Лимит ожидания ответа AI, сек.:",
                self.settings_summary_timeout_input,
            )
        )
        summary_layout.addWidget(
            self._create_settings_field_row(
            "Лимит текста в одном AI-запросе, символов:",
            self.settings_summary_chunk_input,
                "Если расшифровка встречи длиннее этого лимита, приложение разделит ее на части, отправит их в AI по очереди и соберет итог из нескольких ответов. Рекомендуемое значение: 20000 символов.",
            )
        )
        summary_layout.addWidget(summary_hint)
        self._load_summary_model_settings(str(self.config["summary"]["model"]))
        self.settings_summary_model_select.currentIndexChanged.connect(
            self._update_summary_custom_model_visibility
        )
        layout.addWidget(self._create_card("Генерация итогов", summary_layout))

        layout.addWidget(self._create_summary_templates_settings_card())

        page.setLayout(layout)
        return page

    @staticmethod
    def _create_settings_field_row(
        label_text: str,
        field: QWidget,
        hint: str = "",
        label_widget: QLabel | None = None,
    ) -> QWidget:
        row = QWidget()
        row.setObjectName("settingsFieldRow")
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)
        label = label_widget or QLabel(label_text)
        label.setFixedWidth(260)
        label.setWordWrap(True)
        row_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)

        value_layout = QVBoxLayout()
        value_layout.setContentsMargins(0, 0, 0, 0)
        value_layout.setSpacing(6)
        value_layout.addWidget(field)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("sectionHint")
            hint_label.setWordWrap(True)
            value_layout.addWidget(hint_label)
        row_layout.addLayout(value_layout, 1)
        row.setLayout(row_layout)
        return row

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
                    "timeout_seconds": self._settings_numeric_value(
                        self.settings_transcription_timeout_input,
                        "Лимит ожидания ответа транскрипции",
                    ),
                    "max_upload_mb": self._settings_numeric_value(
                        self.settings_transcription_upload_limit_input,
                        "Лимит размера аудио",
                    ),
                    "chunking_enabled": self.settings_transcription_chunking_checkbox.isChecked(),
                    "chunk_duration_seconds": self._settings_numeric_value(
                        self.settings_transcription_chunk_duration_input,
                        "Длительность одной части",
                    ),
                    "retry_attempts": self._settings_numeric_value(
                        self.settings_transcription_retry_attempts_input,
                        "Количество повторов транскрипции",
                    ),
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
        if hasattr(self, "settings_summary_custom_model_row"):
            self.settings_summary_custom_model_row.setVisible(visible)

    def _summary_model_from_settings(self) -> str:
        selected_model = self._combo_value(self.settings_summary_model_select)
        if selected_model == "__custom__":
            return self.settings_summary_custom_model_input.text().strip() or "gpt-5.4-mini"
        return selected_model or "gpt-5.4-mini"

    def _create_summary_templates_settings_card(self) -> QWidget:
        self.settings_summary_templates = self._summary_templates_for_settings()
        self.settings_summary_template_title_inputs: dict[str, QLineEdit] = {}
        self.settings_summary_template_rules_inputs: dict[str, QPlainTextEdit] = {}
        self.settings_summary_template_section_inputs: dict[
            str,
            list[tuple[QLineEdit, QPlainTextEdit]],
        ] = {}
        self.settings_summary_template_section_layouts: dict[str, QVBoxLayout] = {}
        self.settings_summary_template_grids: dict[str, QGridLayout] = {}
        self.settings_summary_template_structure_panels: dict[str, QFrame] = {}
        self.settings_summary_template_side_panels: dict[str, QFrame] = {}
        self.settings_summary_template_right_splitters: dict[str, QSplitter] = {}
        self.settings_summary_template_markdown_previews: dict[str, QPlainTextEdit] = {}
        self.settings_summary_template_prompt_previews: dict[str, QPlainTextEdit] = {}
        self.settings_summary_template_prompt_buttons: dict[str, QPushButton] = {}
        self.settings_summary_template_prompt_cards: dict[str, QFrame] = {}

        layout = QVBoxLayout()
        layout.setSpacing(12)
        hint = QLabel(
            "Настройте структуру итогов и правила для AI. Базовые защитные правила приложения остаются включенными."
        )
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.settings_summary_template_tabs = None
        self.settings_summary_template_buttons: dict[str, QPushButton] = {}
        template_buttons_layout = QHBoxLayout()
        template_buttons_layout.setSpacing(8)
        for index, (label, kind) in enumerate(
            (("Одна встреча", "meeting"), ("Итоги дня", "day"))
        ):
            button = QPushButton(label)
            button.setObjectName("settingsSectionButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _=False, current_index=index: self._show_summary_template_editor(current_index)
            )
            self.settings_summary_template_buttons[label] = button
            template_buttons_layout.addWidget(button)
        template_buttons_layout.addStretch(1)
        layout.addLayout(template_buttons_layout)

        self.settings_summary_template_stack = QStackedWidget()
        self.settings_summary_template_stack.addWidget(self._create_summary_template_editor("meeting"))
        self.settings_summary_template_stack.addWidget(self._create_summary_template_editor("day"))
        layout.addWidget(self.settings_summary_template_stack)
        self._show_summary_template_editor(0)
        return self._create_card("Шаблоны итогов", layout)

    def _show_summary_template_editor(self, index: int) -> None:
        self.settings_summary_template_stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.settings_summary_template_buttons.values()):
            button.setChecked(button_index == index)

    def _summary_templates_for_settings(self) -> dict[str, dict[str, object]]:
        templates = self.config.get("summary", {}).get("templates")
        if not isinstance(templates, dict):
            templates = DEFAULT_CONFIG["summary"]["templates"]
        result: dict[str, dict[str, object]] = {}
        for kind in ("meeting", "day"):
            default_template = DEFAULT_CONFIG["summary"]["templates"][kind]
            template = templates.get(kind) if isinstance(templates, dict) else None
            if not isinstance(template, dict):
                template = default_template
            sections = template.get("sections")
            if not isinstance(sections, list) or not sections:
                sections = default_template["sections"]
            result[kind] = {
                "title": str(template.get("title") or default_template["title"]),
                "sections": [
                    {
                        "title": str(section.get("title") or "").strip(),
                        "instruction": str(section.get("instruction") or "").strip(),
                    }
                    for section in sections
                    if isinstance(section, dict) and str(section.get("title") or "").strip()
                ],
                "rules": str(template.get("rules") or default_template.get("rules") or ""),
            }
        return result

    def _create_summary_template_editor(self, kind: str) -> QWidget:
        page = QWidget()
        page.setObjectName("settingsTemplatePane")
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        template = self.settings_summary_templates[kind]
        title_input = QLineEdit(str(template.get("title") or ""))
        self.settings_summary_template_title_inputs[kind] = title_input
        layout.addWidget(
            self._create_settings_field_row(
                "Заголовок итогов:",
                title_input,
                "Это главный заголовок, который AI использует в итоговом Markdown-файле.",
            )
        )

        editor_grid = QGridLayout()
        editor_grid.setContentsMargins(0, 0, 0, 0)
        editor_grid.setHorizontalSpacing(14)
        editor_grid.setVerticalSpacing(12)
        editor_grid.setColumnStretch(0, 12)
        editor_grid.setColumnStretch(1, 9)
        self.settings_summary_template_grids[kind] = editor_grid

        structure_panel = self._create_summary_template_structure_panel(kind)
        side_panel = self._create_summary_template_side_panel(kind)
        self.settings_summary_template_structure_panels[kind] = structure_panel
        self.settings_summary_template_side_panels[kind] = side_panel
        editor_grid.addWidget(structure_panel, 0, 0)
        editor_grid.addWidget(side_panel, 0, 1)
        layout.addLayout(editor_grid)

        self._refresh_summary_template_sections(kind)
        self._refresh_summary_template_previews(kind)
        page.setLayout(layout)
        return page

    def _create_summary_template_structure_panel(self, kind: str) -> QFrame:
        panel_layout = QVBoxLayout()
        panel_layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(12)
        title_block = QVBoxLayout()
        title_block.setSpacing(4)
        title_label = QLabel("Структура разделов")
        title_label.setObjectName("cardTitle")
        note_label = QLabel(
            "Название раздела обязательно. Поле «Что писать в разделе» можно оставить пустым."
        )
        note_label.setObjectName("sectionHint")
        note_label.setWordWrap(True)
        title_block.addWidget(title_label)
        title_block.addWidget(note_label)
        header.addLayout(title_block, 1)

        add_button = QPushButton("Добавить раздел")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(
            lambda _=False, current_kind=kind: self._add_summary_template_section(current_kind)
        )
        header.addWidget(add_button, 0, Qt.AlignmentFlag.AlignTop)
        panel_layout.addLayout(header)

        sections_layout = QVBoxLayout()
        sections_layout.setContentsMargins(0, 0, 0, 0)
        sections_layout.setSpacing(8)
        self.settings_summary_template_section_layouts[kind] = sections_layout
        panel_layout.addLayout(sections_layout)
        return self._create_settings_inner_card("", panel_layout, object_name="settingsTemplateStructurePanel")

    def _create_summary_template_side_panel(self, kind: str) -> QFrame:
        panel_layout = QVBoxLayout()
        panel_layout.setSpacing(0)
        template = self.settings_summary_templates[kind]

        rules_layout = QVBoxLayout()
        rules_layout.setSpacing(8)
        rules_hint = QLabel(
            "Эти правила добавляются к базовым защитным правилам приложения."
        )
        rules_hint.setObjectName("sectionHint")
        rules_hint.setWordWrap(True)
        rules_input = QPlainTextEdit()
        rules_input.setPlainText(str(template.get("rules") or ""))
        rules_input.setPlaceholderText(
            "Например: писать кратко, не использовать канцелярит, явно отмечать спорные места."
        )
        rules_input.setMinimumHeight(96)
        self.settings_summary_template_rules_inputs[kind] = rules_input
        rules_layout.addWidget(rules_hint)
        rules_layout.addWidget(rules_input)

        base_rules_notice = QLabel(
            "Базовые ограничения не редактируются пользователем: русский язык, Markdown, не выдумывать факты, не писать от лица AI."
        )
        base_rules_notice.setObjectName("inlineStatus")
        base_rules_notice.setWordWrap(True)
        rules_layout.addWidget(base_rules_notice)
        rules_card = self._create_settings_inner_card("Правила для AI", rules_layout)

        markdown_preview = QPlainTextEdit()
        markdown_preview.setReadOnly(True)
        markdown_preview.setMinimumHeight(124)
        self.settings_summary_template_markdown_previews[kind] = markdown_preview
        markdown_card = self._create_settings_inner_card(
            "Предпросмотр итогового Markdown",
            self._single_widget_layout(markdown_preview),
        )

        prompt_button = QPushButton("Показать инструкцию для AI")
        prompt_button.setObjectName("headerButton")
        self.settings_summary_template_prompt_buttons[kind] = prompt_button
        prompt_preview = QPlainTextEdit()
        prompt_preview.setReadOnly(True)
        prompt_preview.setMinimumHeight(135)
        prompt_preview.setVisible(False)
        self.settings_summary_template_prompt_previews[kind] = prompt_preview
        prompt_button.clicked.connect(
            lambda _=False, current_kind=kind, button=prompt_button: self._toggle_summary_prompt_preview(
                current_kind,
                button,
            )
        )
        prompt_layout = QVBoxLayout()
        prompt_layout.setSpacing(8)
        prompt_layout.addWidget(prompt_button, 0, Qt.AlignmentFlag.AlignLeft)
        prompt_layout.addWidget(prompt_preview)
        prompt_card = self._create_settings_inner_card("Инструкция для AI", prompt_layout)
        prompt_card.setMinimumHeight(96)
        prompt_card.setMaximumHeight(110)
        self.settings_summary_template_prompt_cards[kind] = prompt_card

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("settingsSummaryTemplateRightSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(rules_card)
        splitter.addWidget(markdown_card)
        splitter.addWidget(prompt_card)
        splitter.setSizes([170, 150, 100])
        self.settings_summary_template_right_splitters[kind] = splitter
        panel_layout.addWidget(splitter)

        return self._create_settings_inner_card("", panel_layout, object_name="settingsTemplateSidePanel")

    def _refresh_summary_template_sections(self, kind: str) -> None:
        layout = self.settings_summary_template_section_layouts[kind]
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        section_inputs: list[tuple[QLineEdit, QPlainTextEdit]] = []
        sections = self.settings_summary_templates[kind]["sections"]
        if not isinstance(sections, list):
            sections = []
            self.settings_summary_templates[kind]["sections"] = sections

        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            card_layout = QVBoxLayout()
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.setSpacing(6)

            header = QHBoxLayout()
            header.setSpacing(10)
            section_number = QLabel(str(index + 1))
            section_number.setObjectName("settingsSectionNumber")
            section_number.setFixedSize(28, 28)
            header.addWidget(section_number, 0, Qt.AlignmentFlag.AlignTop)
            header.addStretch(1)

            move_up = QPushButton("↑")
            move_up.setObjectName("compactButton")
            move_up.setEnabled(index > 0)
            move_up.clicked.connect(
                lambda _=False, current_kind=kind, current_index=index: self._move_summary_template_section(
                    current_kind,
                    current_index,
                    -1,
                )
            )
            header.addWidget(move_up)

            move_down = QPushButton("↓")
            move_down.setObjectName("compactButton")
            move_down.setEnabled(index < len(sections) - 1)
            move_down.clicked.connect(
                lambda _=False, current_kind=kind, current_index=index: self._move_summary_template_section(
                    current_kind,
                    current_index,
                    1,
                )
            )
            header.addWidget(move_down)

            delete_button = QPushButton("Удалить")
            delete_button.setObjectName("compactDangerButton")
            delete_button.setEnabled(len(sections) > 1)
            delete_button.clicked.connect(
                lambda _=False, current_kind=kind, current_index=index: self._delete_summary_template_section(
                    current_kind,
                    current_index,
                )
            )
            header.addWidget(delete_button)
            card_layout.addLayout(header)

            title_input = QLineEdit(str(section.get("title") or ""))
            title_input.setPlaceholderText("Например: Решения")
            instruction_input = QPlainTextEdit(str(section.get("instruction") or ""))
            instruction_input.setPlaceholderText(
                "Необязательно. Если поле пустое, AI получит только название раздела и общие правила."
            )
            instruction_input.setMinimumHeight(72)
            instruction_input.setMaximumHeight(88)
            card_layout.addWidget(self._create_template_section_field("Название раздела", title_input))
            card_layout.addWidget(
                self._create_template_section_field(
                    "Что писать в разделе",
                    instruction_input,
                )
            )

            card = self._create_settings_inner_card("", card_layout)
            layout.addWidget(card)
            section_inputs.append((title_input, instruction_input))
        self.settings_summary_template_section_inputs[kind] = section_inputs

    @staticmethod
    def _create_template_section_field(label_text: str, field: QWidget, hint: str = "") -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(label_text)
        label.setObjectName("sectionHint")
        layout.addWidget(label)
        layout.addWidget(field)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("sectionHint")
            hint_label.setWordWrap(True)
            layout.addWidget(hint_label)
        wrapper.setLayout(layout)
        return wrapper

    @staticmethod
    def _single_widget_layout(widget: QWidget) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(widget)
        return layout

    def _toggle_summary_prompt_preview(self, kind: str, button: QPushButton) -> None:
        preview = self.settings_summary_template_prompt_previews[kind]
        card = self.settings_summary_template_prompt_cards[kind]
        splitter = self.settings_summary_template_right_splitters[kind]
        visible = not preview.isVisible()
        preview.setVisible(visible)
        if visible:
            card.setMinimumHeight(190)
            card.setMaximumHeight(16777215)
            splitter.setSizes([150, 130, 210])
        else:
            card.setMinimumHeight(96)
            card.setMaximumHeight(110)
            splitter.setSizes([170, 150, 100])
        button.setText("Скрыть инструкцию для AI" if visible else "Показать инструкцию для AI")

    def _refresh_summary_template_previews(self, kind: str) -> None:
        self._save_summary_template_editor_state(kind)
        markdown_preview = self.settings_summary_template_markdown_previews.get(kind)
        prompt_preview = self.settings_summary_template_prompt_previews.get(kind)
        if markdown_preview is not None:
            markdown_preview.setPlainText(self._summary_template_markdown_preview(kind))
        if prompt_preview is not None:
            prompt_preview.setPlainText(self._summary_template_prompt_preview(kind))

    def _refresh_all_summary_template_previews(self) -> None:
        if not hasattr(self, "settings_summary_template_markdown_previews"):
            return
        for kind in ("meeting", "day"):
            if kind in self.settings_summary_template_markdown_previews:
                self._refresh_summary_template_previews(kind)

    def _summary_template_markdown_preview(self, kind: str) -> str:
        template = self.settings_summary_templates[kind]
        title = str(template.get("title") or "").strip() or "Итоги"
        lines = [f"# {title}", ""]
        for section in template.get("sections") or []:
            if not isinstance(section, dict):
                continue
            section_title = str(section.get("title") or "").strip()
            if section_title:
                lines.extend([f"## {section_title}", ""])
        return "\n".join(lines).rstrip() + "\n"

    def _summary_template_prompt_preview(self, kind: str) -> str:
        template = self.settings_summary_templates[kind]
        return build_summary_system_prompt({"templates": {kind: template}}, kind)

    @staticmethod
    def _create_settings_inner_card(
        title: str,
        body_layout,
        object_name: str = "settingsInnerCard",
    ) -> QFrame:
        card = QFrame()
        card.setObjectName(object_name)
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setFrameShadow(QFrame.Shadow.Plain)
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("cardTitle")
            layout.addWidget(title_label)
        layout.addLayout(body_layout)
        card.setLayout(layout)
        return card

    def _save_summary_template_editor_state(self, kind: str) -> None:
        title_input = self.settings_summary_template_title_inputs.get(kind)
        if title_input is not None:
            self.settings_summary_templates[kind]["title"] = title_input.text().strip()
        rules_input = self.settings_summary_template_rules_inputs.get(kind)
        if rules_input is not None:
            self.settings_summary_templates[kind]["rules"] = rules_input.toPlainText().strip()
        section_inputs = self.settings_summary_template_section_inputs.get(kind, [])
        self.settings_summary_templates[kind]["sections"] = [
            {
                "title": title_input.text().strip(),
                "instruction": instruction_input.toPlainText().strip(),
            }
            for title_input, instruction_input in section_inputs
        ]

    def _add_summary_template_section(self, kind: str) -> None:
        self._save_summary_template_editor_state(kind)
        sections = self.settings_summary_templates[kind]["sections"]
        if not isinstance(sections, list):
            sections = []
            self.settings_summary_templates[kind]["sections"] = sections
        sections.append({"title": "Новый раздел", "instruction": ""})
        self._refresh_summary_template_sections(kind)
        self._refresh_summary_template_previews(kind)

    def _move_summary_template_section(self, kind: str, index: int, direction: int) -> None:
        self._save_summary_template_editor_state(kind)
        sections = self.settings_summary_templates[kind]["sections"]
        if not isinstance(sections, list):
            return
        new_index = index + direction
        if new_index < 0 or new_index >= len(sections):
            return
        sections[index], sections[new_index] = sections[new_index], sections[index]
        self._refresh_summary_template_sections(kind)
        self._refresh_summary_template_previews(kind)

    def _delete_summary_template_section(self, kind: str, index: int) -> None:
        self._save_summary_template_editor_state(kind)
        sections = self.settings_summary_templates[kind]["sections"]
        if not isinstance(sections, list) or len(sections) <= 1:
            return
        sections.pop(index)
        self._refresh_summary_template_sections(kind)
        self._refresh_summary_template_previews(kind)

    def _summary_templates_from_settings(self) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        kind_labels = {"meeting": "Итоги одной встречи", "day": "Итоги дня"}
        for kind in ("meeting", "day"):
            self._save_summary_template_editor_state(kind)
            title = self.settings_summary_template_title_inputs[kind].text().strip()
            if not title:
                raise ValueError(f"{kind_labels[kind]}: укажите заголовок итогов.")
            sections = []
            raw_sections = self.settings_summary_templates[kind].get("sections")
            if isinstance(raw_sections, list):
                for index, section in enumerate(raw_sections, start=1):
                    if not isinstance(section, dict):
                        continue
                    section_title = str(section.get("title") or "").strip()
                    if not section_title:
                        raise ValueError(
                            f"{kind_labels[kind]}: укажите название раздела {index}."
                        )
                    sections.append(
                        {
                            "title": section_title,
                            "instruction": str(section.get("instruction") or "").strip(),
                        }
                    )
            if not sections:
                raise ValueError(f"{kind_labels[kind]}: добавьте хотя бы один раздел.")
            result[kind] = {
                "title": title,
                "sections": sections,
                "rules": self.settings_summary_template_rules_inputs[kind].toPlainText().strip(),
            }
        return result

    @staticmethod
    def _settings_numeric_value(field: QWidget, label: str) -> int:
        if isinstance(field, NumericLineEdit):
            return field.validated_value(label)
        if hasattr(field, "value"):
            return int(field.value())
        raise ValueError(f"{label}: поле недоступно.")

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
        self.archive_header = self._create_page_header(
            "Архив",
            "Прошлые рабочие дни, итоги и транскрипты остаются в локальной папке данных.",
        )
        self.archive_header.setObjectName("archiveHeader")
        self.archive_header.setMaximumHeight(88)
        self.archive_header.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        layout.addWidget(self.archive_header)

        controls_layout = QVBoxLayout()
        controls_layout.setSpacing(8)
        self.archive_search_input = QLineEdit()
        self.archive_search_input.setPlaceholderText("Поиск по дате, названию, итогам или транскрипту")
        self.archive_search_input.textChanged.connect(self._schedule_archive_search)
        controls_layout.addWidget(self.archive_search_input)

        filter_row = QHBoxLayout()
        self.archive_week_button = self._add_button(
            filter_row, "Неделя", lambda checked=False: self.set_archive_period("week")
        )
        self.archive_month_button = self._add_button(
            filter_row, "Месяц", lambda checked=False: self.set_archive_period("month")
        )
        self.archive_all_button = self._add_button(
            filter_row, "Все", lambda checked=False: self.set_archive_period("all")
        )
        for button in (self.archive_week_button, self.archive_month_button, self.archive_all_button):
            button.setObjectName("archivePeriodButton")
            button.setCheckable(True)
            button.setMinimumWidth(86)
            button.setMaximumWidth(86)
        filter_row.addWidget(QLabel("с"))
        self.archive_from_input = QLineEdit()
        self.archive_from_input.setPlaceholderText("YYYY-MM-DD")
        self.archive_from_input.setInputMask("0000-00-00;_")
        self.archive_from_input.textChanged.connect(self._schedule_archive_search)
        filter_row.addWidget(self.archive_from_input)
        filter_row.addWidget(QLabel("по"))
        self.archive_to_input = QLineEdit()
        self.archive_to_input.setPlaceholderText("YYYY-MM-DD")
        self.archive_to_input.setInputMask("0000-00-00;_")
        self.archive_to_input.textChanged.connect(self._schedule_archive_search)
        filter_row.addWidget(self.archive_to_input)
        filter_row.addStretch(1)
        controls_layout.addLayout(filter_row)
        self._sync_archive_period_buttons()

        self.archive_results_list = QWidget()
        self.archive_results_list.setObjectName("archiveResultsList")
        self.archive_results_list.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.archive_results_layout = QVBoxLayout()
        self.archive_results_layout.setContentsMargins(0, 0, 0, 0)
        self.archive_results_layout.setSpacing(6)
        self.archive_results_list.setLayout(self.archive_results_layout)
        self.archive_results_scroll = QScrollArea()
        self.archive_results_scroll.setObjectName("archiveResultsScroll")
        self.archive_results_scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.archive_results_scroll.viewport().setObjectName("archiveScrollViewport")
        self.archive_results_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.archive_results_scroll.setWidgetResizable(True)
        self.archive_results_scroll.setMinimumHeight(196)
        self.archive_results_scroll.setMaximumHeight(196)
        self.archive_results_scroll.setWidget(self.archive_results_list)
        controls_layout.addWidget(self.archive_results_scroll)
        self.archive_search_card = self._create_card("Поиск", controls_layout)
        self.archive_search_card.setObjectName("archiveSearchCard")
        self.archive_search_card.setMaximumHeight(340)
        self.archive_search_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        layout.addWidget(self.archive_search_card)

        self.archive_search_timer = QTimer(self)
        self.archive_search_timer.setInterval(250)
        self.archive_search_timer.setSingleShot(True)
        self.archive_search_timer.timeout.connect(self.apply_archive_filters)

        self.archive_empty_state = QLabel(
            "Прошлых рабочих дней пока нет.\n"
            "Сегодняшний день находится во вкладке `Рабочий день`."
        )
        self.archive_empty_state.setObjectName("emptyState")
        self.archive_empty_state.setWordWrap(True)
        layout.addWidget(self.archive_empty_state)

        self.archive_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.archive_splitter.setObjectName("archiveSplitter")

        days_panel = QWidget()
        days_layout = QVBoxLayout()
        days_layout.setContentsMargins(0, 0, 0, 0)
        days_layout.setSpacing(10)
        days_layout.addWidget(QLabel("Прошлые дни"))
        days_panel.setMinimumWidth(300)
        self.archive_days_list = QWidget()
        self.archive_days_list.setObjectName("archiveDaysList")
        self.archive_days_list.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.archive_days_layout = QVBoxLayout()
        self.archive_days_layout.setContentsMargins(0, 0, 0, 0)
        self.archive_days_layout.setSpacing(8)
        self.archive_days_list.setLayout(self.archive_days_layout)
        self.archive_days_scroll = QScrollArea()
        self.archive_days_scroll.setObjectName("archiveDaysScroll")
        self.archive_days_scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.archive_days_scroll.viewport().setObjectName("archiveScrollViewport")
        self.archive_days_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.archive_days_scroll.setWidgetResizable(True)
        self.archive_days_scroll.setWidget(self.archive_days_list)
        days_layout.addWidget(self.archive_days_scroll, 1)
        days_panel.setLayout(days_layout)

        details_panel = QWidget()
        self.archive_detail_layout = QVBoxLayout()
        self.archive_detail_layout.setContentsMargins(0, 28, 0, 0)
        self.archive_detail_layout.setSpacing(10)
        details_panel.setLayout(self.archive_detail_layout)

        self.archive_splitter.addWidget(days_panel)
        self.archive_splitter.addWidget(details_panel)
        self.archive_splitter.setStretchFactor(0, 0)
        self.archive_splitter.setStretchFactor(1, 1)
        self.archive_splitter.setSizes([320, 760])
        layout.addWidget(self.archive_splitter, 1)

        self.archive_no_matches_spacer = QWidget()
        self.archive_no_matches_spacer.setObjectName("archiveNoMatchesSpacer")
        self.archive_no_matches_spacer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.archive_no_matches_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.archive_no_matches_spacer.setVisible(False)
        layout.addWidget(self.archive_no_matches_spacer, 1)

        self.archive_summary_view = SummaryMaterialView("Итоги")
        self.archive_summary_view.save_requested.connect(self.save_archive_summary)
        self.archive_editor = self.archive_summary_view.editor
        self.archive_transcript_view = QTextBrowser()
        self.archive_transcript_view.setObjectName("archiveTranscript")
        self.archive_transcript_view.setReadOnly(True)
        self.archive_open_material: tuple[str, Path] | None = None
        self.archive_material_mode = "summary"
        page.setLayout(layout)
        return page

    def open_archive(self) -> None:
        self.pages.setCurrentIndex(2)
        self.refresh_archive()

    def refresh_archive(self) -> None:
        self.apply_archive_filters()

    def set_archive_period(self, period: str) -> None:
        self.archive_period = period
        if period in {"week", "month", "all"}:
            self.archive_from_input.clear()
            self.archive_to_input.clear()
        self._sync_archive_period_buttons()
        self.apply_archive_filters()

    def _sync_archive_period_buttons(self) -> None:
        if not hasattr(self, "archive_week_button"):
            return
        self.archive_week_button.setChecked(self.archive_period == "week")
        self.archive_month_button.setChecked(self.archive_period == "month")
        self.archive_all_button.setChecked(self.archive_period == "all")

    def _schedule_archive_search(self) -> None:
        if hasattr(self, "archive_search_timer"):
            self.archive_search_timer.start()

    def apply_archive_filters(self) -> None:
        self.archive_query = self.archive_search_input.text().strip()
        date_filter = self._archive_date_filter()
        all_days = build_archive_days(self.storage, date_filter=date_filter)
        self.archive_matches = search_archive(all_days, self.archive_query)
        if self.archive_query:
            matched_day_folders = {match.day_folder for match in self.archive_matches}
            self.archive_days = [day for day in all_days if day.folder in matched_day_folders]
        else:
            self.archive_days = all_days
        if self.selected_archive_day_folder not in {day.folder for day in self.archive_days}:
            self.selected_archive_day_folder = self.archive_days[0].folder if self.archive_days else None
            self.archive_open_material = (
                ("day_summary", self.selected_archive_day_folder)
                if self.selected_archive_day_folder is not None
                else None
            )

        self.archive_empty_state.setVisible(not self.archive_days and not self.archive_query)
        self.archive_splitter.setVisible(bool(self.archive_days))
        self.archive_no_matches_spacer.setVisible(bool(self.archive_query and not self.archive_days))
        self._render_archive_search_results()
        self._render_archive_days()
        self._render_archive_detail()

    def _archive_date_filter(self) -> ArchiveDateFilter | None:
        manual_start = self._parse_archive_date(self.archive_from_input.text())
        manual_end = self._parse_archive_date(self.archive_to_input.text())
        if manual_start is not None or manual_end is not None:
            return ArchiveDateFilter(start=manual_start, end=manual_end)
        now = datetime.now()
        if self.archive_period == "week":
            return ArchiveDateFilter.week(now)
        if self.archive_period == "month":
            return ArchiveDateFilter.month(now)
        return None

    @staticmethod
    def _parse_archive_date(value: str) -> date | None:
        value = value.strip().replace("_", "")
        if not value:
            return None
        if len(value) != 10:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def _render_archive_search_results(self) -> None:
        self.archive_results_scroll.setVisible(bool(self.archive_query))
        self._clear_layout(self.archive_results_layout)
        if self.archive_query and not self.archive_matches:
            empty_label = QLabel("Совпадений не найдено")
            empty_label.setObjectName("sectionHint")
            empty_label.setWordWrap(True)
            self.archive_results_layout.addWidget(empty_label)
            self.archive_results_layout.addStretch(1)
            return
        for match in self.archive_matches:
            result = QWidget()
            result.setObjectName("archiveSearchResult")
            result.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row = QHBoxLayout()
            row.setContentsMargins(10, 8, 10, 8)
            row.setSpacing(8)
            text_layout = QVBoxLayout()
            text_layout.setContentsMargins(0, 0, 0, 0)
            text_layout.setSpacing(2)
            title = QLabel(self._highlight_archive_query(f"{match.kind}: {match.title}"))
            title.setObjectName("archiveSearchTitle")
            title.setTextFormat(Qt.TextFormat.RichText)
            title.setWordWrap(True)
            snippet = QLabel(self._highlight_archive_query(match.snippet))
            snippet.setObjectName("archiveSearchSnippet")
            snippet.setTextFormat(Qt.TextFormat.RichText)
            snippet.setProperty("plain_text", match.snippet)
            snippet.setWordWrap(True)
            snippet.setMaximumHeight(44)
            text_layout.addWidget(title)
            text_layout.addWidget(snippet)
            row.addLayout(text_layout, 1)
            open_button = self._add_button(
                row,
                "Открыть",
                lambda checked=False, selected=match: self.open_archive_search_match(selected),
                "secondaryButton",
            )
            open_button.setMaximumWidth(90)
            result.setLayout(row)
            self.archive_results_layout.addWidget(result)
        self.archive_results_layout.addStretch(1)

    def _highlight_archive_query(self, text: str) -> str:
        query = self.archive_query.strip()
        if not query:
            return escape(text)
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        matches = list(pattern.finditer(text))
        if not matches:
            return escape(text)
        parts: list[str] = []
        cursor = 0
        for match in matches:
            parts.append(escape(text[cursor : match.start()]))
            parts.append(
                '<span class="archiveSearchHighlight" '
                'style="background-color: #fb923c; color: #111827; '
                f'font-weight: 800; padding: 0 2px;">{escape(match.group(0))}</span>'
            )
            cursor = match.end()
        parts.append(escape(text[cursor:]))
        return "".join(parts)

    def open_archive_search_match(self, match: ArchiveSearchMatch) -> None:
        self.selected_archive_day_folder = match.day_folder
        if match.meeting_folder is not None:
            if match.kind == "Транскрипт":
                self.open_archive_meeting_transcript(match.meeting_folder)
            else:
                self.open_archive_meeting_summary(match.meeting_folder)
            return
        if match.kind == "Итоги дня":
            self.open_archive_day_summary(match.day_folder)
            return
        self._render_archive_detail()

    def _create_archive_day_card(self, day: ArchiveDay) -> QWidget:
        selected = day.folder == self.selected_archive_day_folder
        card = QPushButton()
        card.setObjectName("archiveDayCard")
        card.setProperty("selected", selected)
        card.setProperty("day_folder", day.folder)
        card.setCheckable(True)
        card.setChecked(selected)
        card.setMaximumHeight(72)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.clicked.connect(lambda checked=False, folder=day.folder: self.select_archive_day(folder))
        body_layout = QVBoxLayout()
        body_layout.setContentsMargins(12, 8, 12, 8)
        body_layout.setSpacing(3)
        date_label = QLabel(day.workday.isoformat())
        date_label.setObjectName("meetingHeaderLabel")
        body_layout.addWidget(date_label)
        details = QLabel(f"{day.detail_text} · {day.status_label}")
        details.setObjectName("sectionHint")
        details.setWordWrap(True)
        body_layout.addWidget(details)
        card.setLayout(body_layout)
        return card

    def select_archive_day(self, day_folder: Path) -> None:
        self.selected_archive_day_folder = day_folder
        self.selected_archive_meeting_folder = None
        self.archive_open_material = ("day_summary", day_folder)
        self.archive_material_mode = "summary"
        self._render_archive_days()
        self._render_archive_detail()

    def _render_archive_days(self) -> None:
        self._clear_layout(self.archive_days_layout)
        for day in self.archive_days:
            self.archive_days_layout.addWidget(self._create_archive_day_card(day))
        self.archive_days_layout.addStretch(1)

    def _selected_archive_day(self) -> ArchiveDay | None:
        for day in self.archive_days:
            if day.folder == self.selected_archive_day_folder:
                return day
        return None

    def _render_archive_detail(self) -> None:
        self._clear_layout(
            self.archive_detail_layout,
            preserve={self.archive_summary_view, self.archive_transcript_view},
        )
        day = self._selected_archive_day()
        if day is None:
            return
        if self._archive_day_summary_visible(day):
            self.archive_detail_layout.addWidget(self._create_archive_day_summary_card(day))
        for meeting in self._archive_visible_meetings(day):
            self.archive_detail_layout.addWidget(self._create_archive_meeting_card(meeting))
        self.archive_detail_layout.addStretch(1)

    def _create_archive_day_summary_card(self, day: ArchiveDay) -> QWidget:
        body_layout = QVBoxLayout()
        body_layout.setSpacing(6)
        status = "Сформированы" if day.has_day_summary else "Не сформированы"
        label = QLabel(f"Дата: {day.workday.isoformat()}\nСтатус: {status}")
        label.setObjectName("sectionHint")
        label.setWordWrap(True)
        body_layout.addWidget(label)
        actions = QHBoxLayout()
        self._add_button(
            actions,
            "Редактировать итог дня",
            lambda checked=False, folder=day.folder: self.edit_archive_day_summary(folder),
        )
        self._add_button(
            actions,
            "Обновить итоги дня",
            lambda checked=False, folder=day.folder: self.request_archive_day_summary_update(folder),
            "primaryButton",
        )
        if day.metadata.get("status") == "active":
            self._add_button(
                actions,
                "Завершить день",
                lambda checked=False, folder=day.folder: self.finish_archive_workday(folder),
                "primaryButton",
            )
        actions.addStretch(1)
        body_layout.addLayout(actions)
        if self.archive_open_material == ("day_summary", day.folder):
            self.archive_summary_view.set_title("Итоги дня")
            self.archive_summary_view.set_markdown(self.storage.read_day_summary(day.folder))
            body_layout.addWidget(self.archive_summary_view, 1)
        card = self._create_card("Итоги дня", body_layout)
        card.setObjectName("archiveDetailCard")
        return card

    def _create_archive_meeting_card(self, meeting) -> QWidget:
        body_layout = QVBoxLayout()
        body_layout.setSpacing(6)
        started_at = self._short_time(meeting.started_at)
        title = QLabel(f"{started_at}   {meeting.title}".strip())
        title.setObjectName("meetingHeaderLabel")
        body_layout.addWidget(title)
        status = QLabel(meeting.status_label)
        status.setObjectName("sectionHint")
        body_layout.addWidget(status)
        actions = QHBoxLayout()
        self._add_button(
            actions,
            "Редактировать итог встречи",
            lambda checked=False, folder=meeting.folder: self.edit_archive_meeting_summary(folder),
        )
        self._add_button(
            actions,
            "Просмотреть транскрипт",
            lambda checked=False, folder=meeting.folder: self.show_archive_transcript(folder),
        )
        reprocess_button = self._add_button(
            actions,
            "Повторить обработку",
            lambda checked=False, folder=meeting.folder: self.archive_reprocess_meeting(folder),
        )
        reprocess_button.setEnabled(self._can_reprocess_meeting(meeting.folder, meeting.metadata))
        actions.addStretch(1)
        body_layout.addLayout(actions)
        if self.archive_open_material == ("meeting_summary", meeting.folder):
            self.archive_summary_view.set_title("Итоги встречи")
            self.archive_summary_view.set_markdown(self.storage.read_meeting_summary(meeting.folder))
            body_layout.addWidget(self.archive_summary_view, 1)
        elif self.archive_open_material == ("meeting_transcript", meeting.folder):
            self.archive_transcript_view.setPlainText(self._read_meeting_transcript(meeting.folder))
            body_layout.addWidget(self.archive_transcript_view, 1)
        card = self._create_card("Встреча", body_layout)
        card.setObjectName("archiveDetailCard")
        return card

    def _archive_visible_meetings(self, day: ArchiveDay):
        if not self.archive_query:
            return day.meetings
        matching_folders = {
            match.meeting_folder
            for match in self.archive_matches
            if match.day_folder == day.folder and match.meeting_folder is not None
        }
        return [meeting for meeting in day.meetings if meeting.folder in matching_folders]

    def _archive_day_summary_visible(self, day: ArchiveDay) -> bool:
        if not self.archive_query:
            return True
        return any(
            match.day_folder == day.folder
            and match.meeting_folder is None
            and match.kind == "Итоги дня"
            for match in self.archive_matches
        )

    def select_archive_meeting(self, meeting_folder: Path) -> None:
        self.open_archive_meeting_summary(meeting_folder)

    def edit_archive_meeting_summary(self, meeting_folder: Path) -> None:
        self.open_archive_meeting_summary(meeting_folder)

    def open_archive_day_summary(self, day_folder: Path) -> None:
        self.selected_archive_day_folder = day_folder
        self.selected_archive_meeting_folder = None
        self.archive_open_material = ("day_summary", day_folder)
        self.archive_material_mode = "summary"
        self.archive_editor = self.archive_summary_view.editor
        self._render_archive_days()
        self._render_archive_detail()

    def open_archive_meeting_summary(self, meeting_folder: Path) -> None:
        self.selected_archive_meeting_folder = meeting_folder
        self.selected_archive_day_folder = meeting_folder.parent
        self.archive_open_material = ("meeting_summary", meeting_folder)
        self.archive_material_mode = "summary"
        self.archive_editor = self.archive_summary_view.editor
        self._render_archive_days()
        self._render_archive_detail()

    def edit_archive_day_summary(self, day_folder: Path) -> None:
        self.open_archive_day_summary(day_folder)

    def show_archive_transcript(self, meeting_folder: Path) -> None:
        self.open_archive_meeting_transcript(meeting_folder)

    def open_archive_meeting_transcript(self, meeting_folder: Path) -> None:
        self.selected_archive_meeting_folder = meeting_folder
        self.selected_archive_day_folder = meeting_folder.parent
        self.archive_open_material = ("meeting_transcript", meeting_folder)
        self.archive_material_mode = "transcript"
        self.archive_editor = self.archive_transcript_view
        self._render_archive_days()
        self._render_archive_detail()

    def _show_archive_editor(self, title: str) -> None:
        self._clear_layout(self.archive_detail_layout, preserve={self.archive_editor})
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        self.archive_detail_layout.addWidget(title_label)
        self.archive_detail_layout.addWidget(self.archive_editor, 1)
        if not self.archive_editor.isReadOnly():
            actions = QHBoxLayout()
            self._add_button(actions, "Сохранить", self.save_archive_draft)
            self._add_button(actions, "Отмена", self.refresh_archive)
            actions.addStretch(1)
            self.archive_detail_layout.addLayout(actions)

    def save_archive_draft(self) -> None:
        self.save_archive_summary(self.archive_summary_view.editor.toPlainText())

    def save_archive_final(self) -> None:
        self.save_archive_summary(self.archive_summary_view.editor.toPlainText())

    def save_archive_summary(self, content: str) -> None:
        if self.archive_open_material is None:
            return
        kind, folder = self.archive_open_material
        if kind == "day_summary":
            self.storage.save_day_summary(folder, content)
            self.status_label.setText("Итог дня сохранен локально.")
        elif kind == "meeting_summary":
            self.storage.save_meeting_summary(folder, content)
            self.status_label.setText("Итог встречи сохранен локально.")
        self.refresh_archive()

    def request_archive_day_summary_update(self, day_folder: Path) -> None:
        if not self._confirm_risky_action(
            "Обновить итоги дня?",
            "Если вы вручную меняли Итог дня, обновление заменит ваши изменения.",
            "Обновить итоги дня",
        ):
            self.status_label.setText("Обновление итогов дня отменено.")
            return
        self._request_day_summary_update(day_folder, force=True)
        self.refresh_archive()

    def finish_archive_workday(self, day_folder: Path) -> None:
        try:
            self.storage.end_workday_folder(day_folder)
            recovered = self.storage.recover_interrupted_meeting_processing(day_folder)
            pending = self.storage.list_pending_meeting_processing_folders(day_folder)
        except (ValueError, MetadataReadError) as error:
            self.status_label.setText(f"Прошлый рабочий день требует внимания: {error}")
            self.refresh_archive()
            return
        for meeting_folder in pending:
            self._enqueue_meeting_processing(meeting_folder)
        if pending or recovered:
            self.storage.mark_day_summary_waiting(day_folder)
        self._request_day_summary_update(day_folder, force=False)
        self.past_workday_folder = self.storage.find_past_active_workday()
        self._refresh_past_workday_recovery_card()
        self.refresh_archive()

    def archive_reprocess_meeting(self, meeting_folder: Path) -> None:
        self.reprocess_meeting(meeting_folder)
        self.refresh_archive()

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
            "6. Откройте `Ревью`, проверьте итоги и сохраните изменения."
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
            "локально готовит transcript, а Summary generation создает итоги встречи "
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
        self._schedule_readiness_autocheck("workday")

    def start_meeting(self) -> None:
        block_reason = self._meeting_start_block_reason(source="main")
        if block_reason is not None:
            self.status_label.setText(block_reason)
            return
        self.start_meeting_overlay.open_for_recorder(self.recorder)

    def _start_meeting_with_title(self, title: str, source: str = "main") -> None:
        block_reason = self._meeting_start_block_reason(source=source)
        if block_reason is not None:
            self.status_label.setText(block_reason)
            return
        warnings = self._processing_readiness_warnings()
        if warnings and not self._confirm_start_meeting_with_readiness_warnings(warnings):
            self.status_label.setText(
                "Созвон не начат. Исправьте готовность системы или подтвердите старт "
                "с ограничениями."
            )
            return
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

    def _meeting_start_block_reason(self, source: str = "main") -> str | None:
        if self.readiness_check_running:
            return "Дождитесь завершения проверки готовности перед стартом встречи."
        if self.last_readiness_statuses is None or self.readiness_check_stale:
            if source != "floating" and not self.isVisible():
                return None
            self._schedule_readiness_autocheck("meeting")
            return (
                "Сначала дождитесь проверки готовности системы. "
                "После завершения проверки нажмите «Начать встречу» еще раз."
            )
        obs_state = self._readiness_component_state("Запись разговора (OBS)")
        if obs_state == "error":
            return (
                "OBS недоступен. Старт встречи заблокирован: запустите OBS "
                "и проверьте WebSocket."
            )
        return None

    def _processing_readiness_warnings(self) -> list[str]:
        warnings = []
        ffmpeg_state = self._readiness_component_state("Извлечение аудио (FFmpeg)")
        transcription_state = self._readiness_component_state("Транскрипция")
        if ffmpeg_state == "error":
            warnings.append(
                "FFmpeg не готов: запись можно начать, но audio.wav может не извлечься."
            )
        if transcription_state == "error":
            warnings.append(
                "Транскрипция не готова: запись можно начать, но transcript и итоги могут не создаться."
            )
        return warnings

    def _confirm_start_meeting_with_readiness_warnings(self, warnings: list[str]) -> bool:
        message = (
            "Есть проблемы с обработкой встречи:\n\n"
            + "\n".join(f"• {warning}" for warning in warnings)
            + "\n\nНачать встречу все равно?"
        )
        return (
            QMessageBox.question(
                self,
                "Готовность системы",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _confirm_risky_action(
        self,
        title: str,
        text: str,
        confirm_button_text: str,
    ) -> bool:
        return self.risky_action_confirmation_overlay.confirm_action(
            title,
            text,
            confirm_button_text,
        )

    def _readiness_component_state(self, component: str) -> str | None:
        if self.last_readiness_statuses is None or self.readiness_check_stale:
            return None
        for status in self.last_readiness_statuses:
            if status.get("component") == component:
                return str(status.get("state") or "")
        return None

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
        if (
            self.pipeline_running
            and meeting_folder == self.pipeline_meeting_folder
        ) or meeting_folder in self.processing_queue:
            return
        self.processing_queue.append(meeting_folder)
        if not self.pipeline_running:
            self._start_next_pipeline()

    def recover_past_workday(self) -> None:
        day_folder = self.past_workday_folder
        if day_folder is None:
            self.status_label.setText("Прошлый незавершенный рабочий день не найден.")
            return
        try:
            self.storage.end_workday_folder(day_folder)
            self.storage.recover_interrupted_meeting_processing(day_folder)
            pending_meetings = self.storage.list_pending_meeting_processing_folders(day_folder)
        except (ValueError, MetadataReadError) as error:
            self.status_label.setText(f"Прошлый рабочий день требует внимания: {error}")
            self._refresh_past_workday_recovery_card()
            return

        for meeting_folder in pending_meetings:
            self._enqueue_meeting_processing(meeting_folder)
        update_message = self._request_day_summary_update(day_folder, force=False)
        if pending_meetings:
            self.status_label.setText(
                "Прошлый рабочий день завершен. "
                f"Встречи поставлены в обработку: {len(pending_meetings)}. {update_message}"
            )
        else:
            self.status_label.setText(
                f"Прошлый рабочий день завершен. {update_message}"
            )
        self.past_workday_folder = self.storage.find_past_active_workday()
        self._refresh_past_workday_recovery_card()

    def _restore_today_pending_processing_queue(self) -> None:
        """
        Restore any pending or interrupted meeting processing for today by recovering interrupted pipelines and enqueuing meetings whose metadata indicates processing is still pending.
        
        This method:
        - Attempts to recover interrupted meeting processing for today's day folder; if metadata corruption is detected, updates the status label with the backup path.
        - Scans today's meeting folders and enqueues any meeting whose metadata has `"status": "ended"` and `"processing_status": "pending"`, skipping the meeting currently being processed and those already in the processing queue; metadata read errors update the status label and cause that meeting to be skipped.
        - If any meetings were recovered or restored, updates the status label with a summary message and starts the next pipeline.
        """
        restored = 0
        recovered = 0
        recovery_messages: list[str] = []
        day_folder = self.storage.get_today_day_folder()
        if day_folder is not None:
            try:
                recovered = len(self.storage.recover_interrupted_meeting_processing(day_folder))
            except MetadataReadError as error:
                recovery_messages.append(
                    f"Metadata поврежден и сохранен в backup: {error.backup_path}"
                )
        for meeting_folder in self.storage.list_today_meeting_folders():
            if meeting_folder == self.pipeline_meeting_folder:
                continue
            if meeting_folder in self.processing_queue:
                continue
            try:
                metadata = self.storage.read_meeting_metadata(meeting_folder)
            except MetadataReadError as error:
                recovery_messages.append(
                    f"Metadata встречи поврежден и сохранен в backup: {error.backup_path}"
                )
                continue
            if (
                metadata.get("status") == "ended"
                and metadata.get("processing_status") == "pending"
            ):
                self.processing_queue.append(meeting_folder)
                restored += 1
        if restored:
            if recovered:
                recovery_messages.insert(
                    0,
                    f"Восстановлена обработка встреч после перезапуска: {recovered}."
                )
            else:
                recovery_messages.insert(
                    0,
                    f"Восстановлена очередь обработки встреч: {restored}."
                )
            self._start_next_pipeline()
        if recovery_messages:
            self.status_label.setText(" ".join(recovery_messages))

    def _restore_today_pending_day_summary_queue(self) -> None:
        day_folder = self.storage.get_today_day_folder()
        if day_folder is None:
            return
        metadata_path = self.storage.day_summary_metadata_path(day_folder)
        if not metadata_path.is_file():
            return
        try:
            metadata = self.storage.read_day_summary_metadata(day_folder)
        except MetadataReadError as error:
            self.status_label.setText(
                f"Metadata итогов дня поврежден и сохранен в backup: {error.backup_path}"
            )
            return
        if metadata.get("day_summary_status") not in {"pending", "running", "waiting_for_meetings"}:
            return
        update_message = self._request_day_summary_update(day_folder, force=False)
        self.status_label.setText(
            "Восстановлено обновление итогов дня после перезапуска приложения. "
            f"{update_message}"
        )

    def _start_next_pipeline(self) -> None:
        """
        Start background processing for the next meeting in the processing queue.
        
        If a pipeline is already running, a thread exists, or the processing queue is empty, this does nothing. Otherwise it sets the selected meeting as the active pipeline, updates internal state and visible status text, and launches a background worker in a new thread to perform the meeting processing. Progress, completion, and failure are reported via the worker's signals and handled by the instance's pipeline callbacks.
        """
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
        self._request_readiness_check("manual")

    def _schedule_readiness_autocheck(self, reason: str) -> None:
        if not self._can_run_readiness_autocheck(reason):
            return
        if self.readiness_check_running:
            self.readiness_check_rerun_requested = True
            self.readiness_check_rerun_reason = reason
            self.status_label.setText(
                "Проверка готовности уже выполняется. "
                "После завершения будет запущена повторная проверка."
            )
            return
        self._request_readiness_check(reason)

    def _can_run_readiness_autocheck(self, reason: str) -> bool:
        if self.isVisible():
            return True
        return (
            reason == "meeting"
            and hasattr(self, "floating_control")
            and self.floating_control.isVisible()
        )

    def _request_readiness_check(self, reason: str) -> None:
        if self.readiness_check_running:
            self.status_label.setText(
                "Проверка готовности уже выполняется. Дождитесь завершения."
            )
            return
        self.readiness_check_request_id += 1
        request_id = self.readiness_check_request_id
        self.readiness_check_running = True
        self.readiness_check_stale = True
        self._set_readiness_check_in_progress(reason)
        self.refresh_buttons()

        self.readiness_check_thread = QThread(self)
        self.readiness_check_worker = ReadinessCheckWorker(
            request_id,
            deepcopy(self.config),
            self.recorder,
            self.storage.root,
        )
        self.readiness_check_worker.moveToThread(self.readiness_check_thread)
        self.readiness_check_thread.started.connect(self.readiness_check_worker.run)
        self.readiness_check_worker.finished.connect(self._on_readiness_check_finished)
        self.readiness_check_worker.failed.connect(self._on_readiness_check_failed)
        self.readiness_check_worker.finished.connect(self.readiness_check_thread.quit)
        self.readiness_check_worker.failed.connect(self.readiness_check_thread.quit)
        self.readiness_check_thread.finished.connect(self._on_readiness_check_thread_finished)
        self.readiness_check_thread.finished.connect(self.readiness_check_worker.deleteLater)
        self.readiness_check_thread.finished.connect(self.readiness_check_thread.deleteLater)
        self.readiness_check_thread.start()

    def _on_readiness_check_finished(
        self,
        request_id: int,
        statuses: list[dict[str, object]],
        recorder_status_text: str,
    ) -> None:
        self.readiness_check_pending_result = (request_id, statuses, recorder_status_text)

    def _on_readiness_check_failed(self, request_id: int, message: str) -> None:
        self.readiness_check_pending_error = (request_id, message)

    def _on_readiness_check_thread_finished(self) -> None:
        pending_result = self.readiness_check_pending_result
        pending_error = self.readiness_check_pending_error
        self.readiness_check_pending_result = None
        self.readiness_check_pending_error = None
        self._complete_readiness_check()
        if pending_result is not None:
            request_id, statuses, recorder_status_text = pending_result
            if request_id != self.readiness_check_request_id:
                self._show_stale_readiness_result_message()
            else:
                self._render_readiness_statuses(statuses, recorder_status_text)
        elif pending_error is not None:
            request_id, message = pending_error
            if request_id != self.readiness_check_request_id:
                self._show_stale_readiness_result_message()
            else:
                self.status_label.setText(f"Проверка готовности не завершилась: {message}")
        self.readiness_check_worker = None
        self.readiness_check_thread = None
        if self.readiness_check_rerun_requested and self._can_run_readiness_autocheck(
            self.readiness_check_rerun_reason or "settings"
        ):
            reason = self.readiness_check_rerun_reason or "settings"
            self.readiness_check_rerun_requested = False
            self.readiness_check_rerun_reason = ""
            QTimer.singleShot(0, lambda: self._schedule_readiness_autocheck(reason))

    def _show_stale_readiness_result_message(self) -> None:
        self._reset_readiness_cards_to_unchecked()
        self.readiness_check_stale = True
        self.status_label.setText(
            "Настройки изменились во время проверки готовности. "
            "Повторная проверка будет запущена автоматически."
        )

    def _complete_readiness_check(self) -> None:
        self.readiness_check_running = False
        self.check_readiness_button.setText("Проверить готовность")
        self.refresh_buttons()

    def _set_readiness_check_in_progress(self, reason: str = "manual") -> None:
        self.check_readiness_button.setText("Проверяется...")
        self.check_readiness_button.setEnabled(False)
        for card in READINESS_CARDS:
            component = str(card["component"])
            details = [
                {"label": label, "value": "Проверяется...", "state": "neutral"}
                for label in card["initial_details"]
            ]
            self._render_readiness_details(component, details)
            badge = self.readiness_badges.get(component)
            if badge is not None:
                badge.setText("Проверяется")
                self._apply_badge_style(badge, "active")
        if reason == "startup":
            self.status_label.setText("Проверка готовности запущена автоматически.")
        elif reason == "settings":
            self.status_label.setText(
                "Проверка готовности запущена автоматически после сохранения настроек."
            )
        elif reason == "workday":
            self.status_label.setText(
                "Проверка готовности запущена автоматически после начала рабочего дня."
            )
        elif reason == "meeting":
            self.status_label.setText(
                "Проверка готовности запущена перед стартом встречи."
            )
        else:
            self.status_label.setText("Проверка готовности выполняется...")

    def _reset_readiness_cards_to_unchecked(self) -> None:
        self.last_readiness_statuses = None
        self.readiness_check_stale = True
        for card in READINESS_CARDS:
            component = str(card["component"])
            details = [
                {"label": label, "value": "Не проверено", "state": "neutral"}
                for label in card["initial_details"]
            ]
            self._render_readiness_details(component, details)
            badge = self.readiness_badges.get(component)
            if badge is not None:
                badge.setText("Не проверено")
                self._apply_badge_style(badge, "wait")

    def _render_readiness_statuses(
        self,
        statuses: list[dict[str, object]],
        recorder_status_text: str,
    ) -> None:
        self.last_readiness_statuses = deepcopy(statuses)
        self.readiness_check_stale = False
        messages = []
        for status in statuses:
            component = str(status["component"])
            state = str(status["state"])
            details = status.get("details", [])
            self._render_readiness_details(component, details if isinstance(details, list) else [])
            badge = self.readiness_badges.get(component)
            if badge is not None:
                badge.setText(self._badge_state_text(state))
                self._apply_badge_style(badge, state)
            messages.append(str(status["message"]))
        self.obs_status_value.setText(recorder_status_text)
        self.status_label.setText("Проверка готовности завершена. " + " ".join(messages))

    def _render_readiness_details(
        self,
        component: str,
        details: list[dict[str, str]],
    ) -> None:
        values = self.readiness_detail_values.get(component)
        if values is None:
            return
        known_labels = set(values)
        incoming_labels = {detail["label"] for detail in details}
        if incoming_labels != known_labels:
            self._rebuild_readiness_detail_rows(component, details)
            values = self.readiness_detail_values.get(component, {})
        for detail in details:
            value_label = values.get(detail["label"])
            if value_label is None:
                continue
            state = detail.get("state", "neutral")
            value_label.setText(detail["value"])
            value_label.setProperty("readiness_state", state)
            self._apply_readiness_detail_style(value_label, state)

    def _rebuild_readiness_detail_rows(
        self,
        component: str,
        details: list[dict[str, str]],
    ) -> None:
        details_widget = self.readiness_detail_rows.get(component)
        if details_widget is None:
            return
        old_layout = details_widget.layout()
        if old_layout is not None:
            self._clear_layout(old_layout)
            layout = old_layout
        else:
            layout = QGridLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setHorizontalSpacing(8)
            layout.setVerticalSpacing(5)
        value_labels: dict[str, QLabel] = {}
        for row, detail in enumerate(details):
            label = QLabel(detail["label"])
            label.setObjectName("readinessDetailLabel")
            label.setMinimumWidth(88)
            value = QLabel(detail["value"])
            value.setObjectName("readinessDetailValue")
            state = detail.get("state", "neutral")
            value.setProperty("readiness_state", state)
            value.setWordWrap(True)
            value.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self._apply_readiness_detail_style(value, state)
            layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(value, row, 1, Qt.AlignmentFlag.AlignTop)
            value_labels[detail["label"]] = value
        layout.setColumnStretch(1, 1)
        if old_layout is None:
            details_widget.setLayout(layout)
        self.readiness_detail_values[component] = value_labels

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
            "transcription_chunk_failed": ("transcription", "Ошибка", message, "error"),
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
        self._refresh_after_lifecycle_change()
        if hasattr(self, "floating_control") and self.floating_control.isVisible():
            self.floating_control.show_error("Ошибка фоновой обработки. Откройте приложение для деталей.")

    def _on_pipeline_thread_finished(self) -> None:
        self.pipeline_thread = None
        self.pipeline_worker = None
        self._start_next_pipeline()
        self._start_pending_day_summary_if_ready()
        self.apply_pending_runtime_settings()

    def update_day_summary(self) -> None:
        day_folder = self.storage.get_today_day_folder()
        if day_folder is None:
            self.status_label.setText("Папка сегодняшнего рабочего дня пока не создана.")
            return
        if not self._confirm_risky_action(
            "Обновить итоги дня?",
            "Если вы вручную меняли Итог дня, обновление заменит ваши изменения.",
            "Обновить итоги дня",
        ):
            self.status_label.setText("Обновление итогов дня отменено.")
            return
        self._request_day_summary_update(day_folder, force=True)

    def _request_day_summary_update(self, day_folder: Path, force: bool = False) -> str:
        if self.pipeline_running or self.processing_queue or self.storage.has_unfinished_meeting_processing(day_folder):
            self.storage.mark_day_summary_waiting(day_folder)
            self.day_summary_pending = True
            self.day_summary_force_pending = self.day_summary_force_pending or force
            self.day_summary_day_folder = day_folder
            message = (
                "Итоги дня поставлены в очередь и начнутся после завершения обработки встреч."
            )
            self.status_label.setText(message)
            self._refresh_after_lifecycle_change()
            return message
        self._start_day_summary_pipeline(day_folder, force)
        return self.status_label.text()

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
                self._set_day_summary_pipeline_step("check", "Готово", "Итоги встреч проверены.", "ok")
                self._set_day_summary_pipeline_step("generate", "Готово", "00_day_summary.md готов.", "ok")
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
        self.apply_pending_runtime_settings()

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
            if status == "stopped":
                if metadata.get("recording_path"):
                    return "Готово", "ok"
                return "Без записи", "error"
            if status == "disabled":
                return "Без записи", "error"
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
            if status == "disabled":
                return "Выключено", "skip"
            if status == "skipped":
                return "Ошибка", "error"
            if status:
                return "Ошибка", "error"
        return "Ожидает", "wait"

    def _step_message(self, step: str, metadata: dict[str, object]) -> str:
        if step == "recording":
            if metadata.get("recording_status") == "recording":
                return "OBS ведет запись."
            if metadata.get("recording_status") == "stopped":
                if not metadata.get("recording_path"):
                    return "OBS не вернул путь к записи."
                return "Запись остановлена."
            if metadata.get("recording_status") == "disabled":
                return "OBS запись не выполнена."
            return str(metadata.get("recording_note") or "OBS запись ожидает обработки.")
        if step == "audio":
            if metadata.get("audio_error"):
                return str(metadata["audio_error"])
            if metadata.get("audio_status") == "extracted":
                return "audio.wav извлечен через FFmpeg."
            if metadata.get("audio_status") == "skipped":
                if self._meeting_has_no_recording(metadata):
                    return "Аудио не извлекалось: нет записи."
                return "Аудио не извлекалось."
            if metadata.get("processing_status") == "pending":
                return "Ждет обработки встречи."
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
            if metadata.get("audio_status") == "extracted":
                if metadata.get("processing_status") == "pending":
                    return self._pending_processing_message()
                return "Ждет запуска транскрипции."
            return "Ждет audio.wav."
        if step == "summary":
            if metadata.get("summary_error"):
                return str(metadata["summary_error"])
            if metadata.get("summary_status") == "draft_created":
                return "summary.md готов к ревью."
            if metadata.get("summary_status") == "disabled":
                return "Генерация итогов выключена в настройках."
            if metadata.get("summary_status") == "skipped":
                return "Итоги не сформированы."
            return "Ждет transcript."
        return ""

    def _pending_processing_message(self) -> str:
        if self.pipeline_running:
            return (
                "Ждет обработки встречи: сначала завершается обработка другой встречи."
            )
        return "Ждет обработки встречи."

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
        if normalized in {
            "идет",
            "выполняется",
            "генерация",
            "обработка",
            "проверяется",
        }:
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

    def _apply_readiness_detail_style(self, label: QLabel, state: str) -> None:
        palette = self._theme_palette()
        color = self._status_colors()["error"][1] if state == "error" else palette["text"]
        font_weight = "700" if state == "error" else "500"
        label.setStyleSheet(
            "padding: 0; background: transparent; "
            f"color: {color}; font-weight: {font_weight};"
        )

    def _apply_badge_style(self, label: QLabel, state: str) -> None:
        colors = self._status_colors()
        background, color = colors.get(state, colors["wait"])
        radius = 14 if label.property("readinessBadge") else 10
        label.setStyleSheet(
            f"border-radius: {radius}px; padding: 3px 8px; font-size: 11px; "
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
        self.apply_pending_runtime_settings()

    def open_review(self) -> None:
        self.pages.setCurrentIndex(1)
        self.refresh_review()

    def open_meeting_summary_review(self, meeting_folder: Path) -> None:
        metadata = self.storage.read_meeting_metadata(meeting_folder)
        if not self._meeting_summary_is_ready(meeting_folder, metadata):
            self.status_label.setText("Итоги этой встречи пока не готовы.")
            return
        self.pages.setCurrentIndex(1)
        self.review_day_summary_selected = False
        self.selected_review_meeting_folder = meeting_folder
        self.refresh_review()
        self.review_tabs.setCurrentIndex(0)
        self.review_status_label.setText(f"Открыты итоги встречи: {meeting_folder.name}")

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
                "Выберите «Итоги дня» или встречу, чтобы проверить локальные итоги."
            )
            self.review_status_label.setText("Итоги дня загружены.")
        elif meeting_folders:
            self.load_selected_meeting(self.selected_review_meeting_folder)
            self.review_meetings_hint.setText(
                "Выберите встречу, чтобы проверить итоги и transcript."
            )
            self.review_status_label.setText("Локальные итоги загружены.")
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
            self.review_summary_view.set_markdown("")
            self.meeting_transcript_editor.clear()
            self._refresh_review_buttons()
            return
        self.review_tabs.setTabText(0, "Итоги встречи")
        self.review_tabs.setTabText(1, "Транскрипт")
        self.review_summary_view.set_title("Итоги встречи")
        self.review_summary_view.set_markdown(self.storage.read_meeting_summary(meeting_folder))
        self.meeting_transcript_editor.setPlainText(self._read_meeting_transcript(meeting_folder))
        self._refresh_review_buttons()

    def load_day_summary_review(self) -> None:
        day_folder = self.storage.get_today_day_folder()
        self.review_tabs.setTabText(0, "Итоги встреч")
        self.review_tabs.setTabText(1, "Транскрипт")
        if day_folder is None:
            self.review_summary_view.set_markdown("")
            self.meeting_transcript_editor.clear()
            self._refresh_review_buttons()
            return
        self.review_summary_view.set_title("Итоги дня")
        self.review_summary_view.set_markdown(self.storage.read_day_summary(day_folder))
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
        self.save_review_summary(self.review_summary_view.markdown)

    def save_review_summary(self, content: str) -> None:
        selected_meeting = self._selected_meeting_folder()
        day_folder = self.storage.get_today_day_folder()
        if day_folder is None:
            self.review_status_label.setText("Папка сегодняшнего рабочего дня пока не создана.")
            return
        if self.review_day_summary_selected:
            self.storage.save_day_summary(day_folder, content)
            self.review_status_label.setText("Итог дня сохранен локально.")
            return
        if selected_meeting is None:
            self.review_status_label.setText("Выберите встречу для сохранения итогов.")
            return
        self.storage.save_meeting_summary(selected_meeting, content)
        self.review_status_label.setText("Итог встречи сохранен локально.")

    def save_final_files(self) -> None:
        selected_meeting = self._selected_meeting_folder()
        day_folder = self.storage.get_today_day_folder()
        if self.review_day_summary_selected:
            if day_folder is None:
                self.review_status_label.setText("Папка сегодняшнего рабочего дня пока не создана.")
                return
            self.storage.save_day_summary(day_folder, self.review_summary_view.markdown)
            self.review_status_label.setText("Итог дня сохранен локально.")
            return
        if selected_meeting is None:
            self.review_status_label.setText("Выберите встречу для сохранения итогов.")
            return
        self.storage.save_meeting_summary(selected_meeting, self.review_summary_view.markdown)
        self.review_status_label.setText("Итог встречи сохранен локально.")

    def save_final_summaries(self) -> None:
        self.save_final_files()

    def choose_storage_root_folder(self) -> None:
        current_text = self.settings_storage_root_input.text().strip()
        start_dir = current_text or str(Path.cwd())
        selected_folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку данных",
            start_dir,
        )
        if not selected_folder:
            return
        self.settings_storage_root_input.setText(selected_folder)
        self.settings_status_label.setText(f"Выбрана папка данных: {selected_folder}")

    def _validate_storage_root_from_settings(self) -> tuple[str, Path] | None:
        storage_root_text = (
            self.settings_storage_root_input.text().strip() or "MeetingSummaries"
        )
        storage_root_path = Path(storage_root_text).expanduser()
        try:
            if storage_root_path.exists() and not storage_root_path.is_dir():
                self.settings_status_label.setText(
                    f"Папка данных не сохранена: путь указывает на файл: {storage_root_path}"
                )
                return None
            storage_root_path.mkdir(parents=True, exist_ok=True)
            write_test_path = storage_root_path / ".meeting_day_recorder_write_test"
            write_test_path.write_text("ok", encoding="utf-8")
            write_test_path.unlink(missing_ok=True)
        except OSError as error:
            self.settings_status_label.setText(
                f"Папка данных недоступна для записи: {storage_root_path}. {error}"
            )
            return None
        return str(storage_root_path), storage_root_path

    @staticmethod
    def _write_local_config(config_to_save: dict[str, object]) -> None:
        config_path = Path("config.yaml")
        payload = yaml.safe_dump(
            config_to_save,
            allow_unicode=True,
            sort_keys=False,
        )
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=config_path.parent,
                prefix=f".{config_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_file.write(payload)
                temp_path = Path(temp_file.name)
            temp_path.replace(config_path)
        except OSError:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise

    def _config_with_sidebar_theme(self, theme: str) -> dict[str, object]:
        if self.config.get("_warnings"):
            raise ValueError(
                "сначала исправьте config.yaml или пересохраните настройки."
            )
        config_path = Path("config.yaml")
        if config_path.exists():
            try:
                loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as error:
                raise ValueError(
                    "config.yaml содержит ошибку YAML. Исправьте файл или пересохраните настройки."
                ) from error
            if not isinstance(loaded, dict):
                raise ValueError(
                    "config.yaml должен быть YAML-словарем. Исправьте файл или пересохраните настройки."
                )
            config_to_save = deepcopy(loaded)
        else:
            config_to_save = {}
        config_to_save.pop("_warnings", None)
        ui_config = config_to_save.get("ui")
        if not isinstance(ui_config, dict):
            ui_config = {}
        else:
            ui_config = dict(ui_config)
        ui_config["theme"] = theme
        config_to_save["ui"] = ui_config
        return config_to_save

    def save_settings(self) -> None:
        storage_root = self._validate_storage_root_from_settings()
        if storage_root is None:
            return
        storage_root_text, storage_root_path = storage_root
        config_path = Path("config.yaml")
        try:
            config_to_save = self._settings_config_from_ui(storage_root_text)
        except ValueError as error:
            self.settings_status_label.setText(f"Настройки не сохранены: {error}")
            return
        try:
            self._write_local_config(config_to_save)
        except OSError as error:
            self.settings_status_label.setText(
                f"Настройки не сохранены: не удалось записать config.yaml. {error}"
            )
            return
        readiness_invalidated = self._invalidate_readiness_check_after_settings_change()
        self.config = load_config(config_path)
        self._refresh_all_summary_template_previews()
        self._apply_theme_settings()
        self._apply_runtime_settings_after_save(storage_root_path)
        if readiness_invalidated:
            self._append_settings_status(
                "Проверка готовности выполнялась со старыми настройками. "
                "После завершения будет запущена повторная проверка."
            )
        elif self.isVisible():
            self._append_settings_status("Проверка готовности запущена автоматически.")
        self._schedule_readiness_autocheck("settings")

    def _invalidate_readiness_check_after_settings_change(self) -> bool:
        self.readiness_check_stale = True
        if not self.readiness_check_running:
            return False
        self.readiness_check_request_id += 1
        self.readiness_check_rerun_requested = True
        self.readiness_check_rerun_reason = "settings"
        return True

    def _append_settings_status(self, message: str) -> None:
        current_message = self.settings_status_label.text().strip()
        if current_message:
            self.settings_status_label.setText(f"{current_message} {message}")
            return
        self.settings_status_label.setText(message)

    def _settings_config_from_ui(self, storage_root: str | None = None) -> dict[str, object]:
        if hasattr(self, "settings_current_transcription_backend"):
            self._save_current_transcription_profile(
                self.settings_current_transcription_backend
            )
        return {
            "storage": {
                "root": storage_root
                if storage_root is not None
                else self.settings_storage_root_input.text().strip() or "MeetingSummaries",
            },
            "obs": {
                "websocket_host": self.settings_obs_host_input.text().strip() or "localhost",
                "websocket_port": self._settings_numeric_value(
                    self.settings_obs_port_input,
                    "Порт WebSocket",
                ),
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
                "timeout_seconds": self._settings_numeric_value(
                    self.settings_summary_timeout_input,
                    "Лимит ожидания ответа AI",
                ),
                "max_chars_per_chunk": self._settings_numeric_value(
                    self.settings_summary_chunk_input,
                    "Лимит текста в одном AI-запросе",
                ),
                "templates": self._summary_templates_from_settings(),
            },
            "ui": {
                "theme": self._combo_value(self.settings_theme_select),
                "floating_theme": self._combo_value(self.settings_floating_theme_select),
            },
        }

    def _apply_runtime_settings_after_save(self, storage_root_path: Path) -> None:
        has_processing_work = self._has_processing_work()
        storage_change_deferred = self.storage.root != storage_root_path and (
            self.storage.workday_active or self.storage.meeting_active or has_processing_work
        )
        self.pending_storage_root_path = (
            storage_root_path if storage_change_deferred else None
        )
        if has_processing_work:
            self.pending_runtime_settings = True
        else:
            self.pending_runtime_settings = False
        if has_processing_work:
            storage_message = (
                " Папка данных применится после завершения рабочего дня и текущей обработки."
                if storage_change_deferred
                else ""
            )
            self.settings_status_label.setText(
                "Настройки сохранены. Тема интерфейса применена сразу. "
                "Текущая обработка завершится со старой конфигурацией, "
                f"следующие встречи будут использовать обновленные настройки.{storage_message}"
            )
            return
        self.storage.transcriber = create_transcriber(self._transcription_runtime_config())
        self.storage.summarizer = create_summarizer(self._summary_runtime_config())
        if storage_change_deferred:
            self.settings_status_label.setText(
                "Настройки сохранены. Тема интерфейса применена сразу. "
                "Папка данных применится после завершения рабочего дня. "
                "Остальные настройки будут использоваться для следующих встреч."
            )
            return
        if self.storage.root != storage_root_path:
            self.storage.root = storage_root_path
            self.storage.load_today_state()
        self.pending_storage_root_path = None
        self.settings_status_label.setText(
            "Настройки сохранены. Тема интерфейса применена сразу. "
            "Следующие встречи будут использовать обновленные настройки."
        )
        self._refresh_after_lifecycle_change()

    def apply_pending_runtime_settings(self) -> None:
        applied = False
        if self.pending_runtime_settings and not self._has_processing_work():
            self.storage.transcriber = create_transcriber(self._transcription_runtime_config())
            self.storage.summarizer = create_summarizer(self._summary_runtime_config())
            self.pending_runtime_settings = False
            applied = True
        if (
            self.pending_storage_root_path is not None
            and not self.storage.workday_active
            and not self.storage.meeting_active
            and not self._has_processing_work()
        ):
            self.storage.root = self.pending_storage_root_path
            self.storage.load_today_state()
            self.past_workday_folder = self.storage.find_past_active_workday()
            self.past_workday_recovery_hidden = False
            self.pending_storage_root_path = None
            applied = True
        if applied:
            self.status_label.setText("Отложенные настройки применены.")
            if hasattr(self, "settings_status_label"):
                current_settings_message = self.settings_status_label.text().strip()
                if current_settings_message:
                    self.settings_status_label.setText(
                        f"{current_settings_message} Отложенные настройки применены."
                    )
                else:
                    self.settings_status_label.setText("Отложенные настройки применены.")
            self._refresh_after_lifecycle_change()

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

    def open_past_workday_folder(self) -> None:
        day_folder = self.past_workday_folder
        if day_folder is None:
            self.status_label.setText("Прошлый незавершенный рабочий день не найден.")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(day_folder.resolve()))):
            self.status_label.setText(f"Не удалось открыть папку прошлого дня: {day_folder}")

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
        elif self.pages.currentIndex() == 2 and not self._archive_editor_is_editing():
            self.refresh_archive()

    def _archive_editor_is_editing(self) -> bool:
        return (
            getattr(self, "archive_summary_view", None) is not None
            and self.archive_open_material is not None
            and self.archive_open_material[0] in {"day_summary", "meeting_summary"}
            and self.archive_summary_view.mode == "edit"
        )

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
            if self.storage.meeting_active:
                self.day_status_detail_value.setText(
                    "Нельзя завершить рабочий день, пока идет активная встреча. "
                    "Сначала завершите встречу."
                )
            elif self._has_background_meeting_processing(day_folder):
                self.day_status_detail_value.setText(
                    "Можно завершить рабочий день. Итоги дня начнутся после "
                    "завершения обработки встреч."
                )
            else:
                self.day_status_detail_value.setText(
                    "Можно завершить рабочий день. После завершения будут подготовлены итоги дня."
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
            if self._has_background_meeting_processing(self.storage.get_today_day_folder()):
                self.active_call_detail_value.setText(
                    "Предыдущая встреча обрабатывается в фоне. Новую встречу можно начать."
                )
            else:
                self.active_call_detail_value.setText(
                    "Можно начать новую встречу. Если предыдущая еще обрабатывается, "
                    "она продолжит выполняться в фоне."
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

    def _has_background_meeting_processing(self, day_folder: Path | None) -> bool:
        if self.storage.meeting_active:
            return False
        if self.pipeline_running or self.processing_queue:
            return True
        if day_folder is None:
            return False
        try:
            return self.storage.has_unfinished_meeting_processing(day_folder)
        except MetadataReadError as error:
            if hasattr(self, "status_label"):
                self.status_label.setText(
                    f"Metadata встречи поврежден и сохранен в backup: {error.backup_path}"
                )
            return False

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

        self.check_readiness_button.setEnabled(
            not self.readiness_check_running
            and not self.pipeline_running
            and not self.day_summary_running
        )
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
        self._refresh_past_workday_recovery_card()
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
        self._refresh_floating_control()

    def _has_processing_work(self) -> bool:
        return self._has_meeting_processing_work() or self._has_day_summary_processing_work()

    def _has_meeting_processing_work(self) -> bool:
        return (
            self.pipeline_running
            or bool(self.processing_queue)
        )

    def _has_day_summary_processing_work(self) -> bool:
        return (
            self.day_summary_running
            or self.day_summary_pending
        )

    def _clear_review_editors(self) -> None:
        self.review_summary_view.set_markdown("")
        self.meeting_transcript_editor.clear()
