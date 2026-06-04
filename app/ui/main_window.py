from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import yaml

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
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
    QVBoxLayout,
    QWidget,
)

from app.config import load_config
from app.services.readiness import check_readiness
from app.services.recorder import Recorder, RecorderError, create_recorder
from app.services.storage import StorageService
from app.services.summarization import create_summarizer
from app.services.transcription import create_transcriber


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


class ClickableFrame(QFrame):
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    READINESS_CARD_EXPANDED_HEIGHT = 276
    READINESS_CARD_COLLAPSED_HEIGHT = 86
    READINESS_GRID_HEIGHT = 182
    PIPELINE_STEPS = [
        ("meeting", "Созвон"),
        ("recording", "OBS запись"),
        ("audio", "Извлечение аудио"),
        ("transcription", "Транскрипция"),
        ("summary", "Генерация итогов"),
        ("done", "Готово"),
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
        self.nav_buttons: dict[int, QPushButton] = {}
        self.pipeline_running = False
        self.pipeline_completed = False
        self.pipeline_meeting_folder: Path | None = None
        self.processing_queue: list[Path] = []
        self.pipeline_thread: QThread | None = None
        self.pipeline_worker: MeetingPipelineWorker | None = None
        self.recorder = recorder or (
            storage.recorder if storage else create_recorder(self.config["obs"])
        )
        self.storage = storage or StorageService(
            Path(self.config["storage"]["root"]),
            self.recorder,
            transcriber=create_transcriber(self.config["transcription"]),
            summarizer=create_summarizer(self.config["summary"]),
        )
        self.storage.load_today_state()
        self.readiness_labels: dict[str, QLabel] = {}
        self.readiness_badges: dict[str, QLabel] = {}
        self.readiness_tiles: dict[str, QWidget] = {}
        self.pipeline_labels: dict[str, QLabel] = {}
        self.pipeline_step_titles: dict[str, QLabel] = {}
        self.selected_workday_meeting_folder: Path | None = None
        self.selected_review_meeting_folder: Path | None = None
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
        self._refresh_navigation_state(self.pages.currentIndex())
        self.refresh_status()
        self.refresh_buttons()
        self.active_call_timer = QTimer(self)
        self.active_call_timer.setInterval(1000)
        self.active_call_timer.timeout.connect(self._refresh_active_call_display)
        self.active_call_timer.start()

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

    def _apply_app_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f6efe6;
                color: #3a1408;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QWidget#appRoot,
            QWidget#content,
            QStackedWidget#pages {
                background: #f6efe6;
            }
            QWidget#sidebar {
                background: #fffdf8;
                border-right: 1px solid #ead8c6;
            }
            QLabel#brand {
                color: #ff6f1a;
                font-size: 18px;
                font-weight: 800;
                padding: 2px 14px 18px 14px;
                border-bottom: 1px solid #f1e5d8;
            }
            QPushButton#navButton {
                background: transparent;
                color: #7b4b35;
                border: 0;
                border-bottom: 1px solid #f1e5d8;
                border-radius: 0;
                padding: 14px 18px;
                text-align: left;
                font-weight: 700;
            }
            QPushButton#navButton:hover {
                background: #fff8ef;
                color: #ff6f1a;
            }
            QPushButton#navButton:checked {
                background: #fff3e6;
                color: #ff6f1a;
                border-left: 3px solid #ff6f1a;
                padding-left: 15px;
            }
            QLabel#pageTitle {
                color: #3a1408;
                font-size: 26px;
                font-weight: 800;
            }
            QLabel#pageSubtitle {
                color: #8a6a58;
            }
            QLabel#emptyState {
                background: #fffdf8;
                color: #7b4b35;
                border: 1px solid #ead8c6;
                border-radius: 8px;
                padding: 18px;
            }
            QWidget#card {
                background: #fffdf8;
                border: 1px solid #ead8c6;
                border-radius: 8px;
            }
            QLabel#cardTitle {
                color: #3a1408;
                font-size: 14px;
                font-weight: 800;
            }
            QLabel#sectionHint {
                color: #8a6a58;
            }
            QLabel#heroValue {
                color: #3a1408;
                font-size: 18px;
                font-weight: 800;
            }
            QLabel#callTimer {
                color: #d9280f;
                font-size: 28px;
                font-weight: 800;
            }
            QFrame#meetingCard {
                background: #fffdf8;
                border: 1px solid #ead8c6;
                border-radius: 8px;
            }
            QFrame#activeMeetingCard {
                background: #fff3e6;
                border: 1px solid #ffb98a;
                border-radius: 8px;
            }
            QLabel#meetingHeaderLabel {
                background: transparent;
                border: 0;
                color: #3a1408;
                font-size: 14px;
                font-weight: 800;
                padding: 0;
                min-height: 22px;
            }
            QFrame#readinessTile {
                background: #fffdf8;
                border: 1px solid #ead8c6;
                border-radius: 8px;
                min-height: 82px;
                max-height: 82px;
                min-width: 300px;
            }
            QLabel#readinessTitle {
                color: #3a1408;
                font-weight: 800;
            }
            QLabel#readinessMessage {
                color: #8a6a58;
                min-height: 30px;
            }
            QLabel#statusBadge {
                border-radius: 10px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 800;
            }
            QLabel#pipelineStepTitle {
                color: #3a1408;
                font-weight: 800;
            }
            QPushButton {
                background: #fffdf8;
                color: #3a1408;
                border: 1px solid #ead8c6;
                border-radius: 6px;
                padding: 8px 12px;
                min-height: 28px;
                font-weight: 600;
            }
            QPushButton:hover {
                border-color: #ff6f1a;
                color: #ff6f1a;
            }
            QPushButton:disabled {
                background: #f3e8dc;
                color: #b49a89;
                border-color: #ead8c6;
            }
            QPushButton#primaryButton {
                background: #ff6f1a;
                color: #ffffff;
                border: 1px solid #ff6f1a;
            }
            QPushButton#primaryButton:hover {
                background: #f45a00;
                color: #ffffff;
                border-color: #f45a00;
            }
            QPushButton#dangerButton {
                background: #d9280f;
                color: #ffffff;
                border: 1px solid #d9280f;
            }
            QPushButton#dangerButton:hover {
                background: #b91c1c;
                color: #ffffff;
                border-color: #b91c1c;
            }
            QPushButton#headerPrimaryButton {
                background: #ff6f1a;
                color: #ffffff;
                border: 1px solid #ff6f1a;
                border-radius: 6px;
                padding: 4px 12px;
                min-height: 24px;
                max-height: 34px;
                font-weight: 700;
            }
            QPushButton#headerPrimaryButton:hover {
                background: #f45a00;
                color: #ffffff;
                border-color: #f45a00;
            }
            QPushButton#headerButton {
                background: #fffdf8;
                color: #7b4b35;
                border: 1px solid #ead8c6;
                border-radius: 6px;
                padding: 4px 12px;
                min-height: 24px;
                max-height: 34px;
                font-weight: 600;
            }
            QPlainTextEdit {
                background: #fffdf8;
                color: #3a1408;
                border: 1px solid #ead8c6;
                border-radius: 8px;
                padding: 8px;
            }
            QTabWidget::pane {
                border: 1px solid #ead8c6;
                border-radius: 8px;
                background: #fffdf8;
            }
            QTabBar::tab {
                background: #f3e8dc;
                color: #8a6a58;
                padding: 8px 12px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: #fffdf8;
                color: #3a1408;
                font-weight: 700;
            }
            """
        )

    @staticmethod
    def _create_card(title: str, body_layout, header_actions: list[QWidget] | None = None) -> QWidget:
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

    def _create_pipeline_step_labels(self, key: str, title: str) -> tuple[QLabel, QLabel]:
        title_label = QLabel(title)
        title_label.setObjectName("pipelineStepTitle")
        status_label = QLabel()
        status_label.setObjectName("pipelineStatus")
        status_label.setWordWrap(False)
        status_label.setMinimumHeight(28)
        status_label.setMinimumWidth(420)
        status_label.setMaximumWidth(900)

        self.pipeline_step_titles[key] = title_label
        self.pipeline_labels[key] = status_label
        return title_label, status_label

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
        self.pipeline_step_titles = {}
        self.workday_meeting_cards = {}
        meeting_folders = self.storage.list_today_meeting_folders()
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
            pipeline_layout = QFormLayout()
            pipeline_layout.setHorizontalSpacing(18)
            pipeline_layout.setVerticalSpacing(8)
            for key, title in self.PIPELINE_STEPS:
                title_label, status_label = self._create_pipeline_step_labels(key, title)
                pipeline_layout.addRow(title_label, status_label)
            card_layout.addLayout(pipeline_layout)
            self._refresh_pipeline_from_metadata(metadata)

        card.setLayout(card_layout)
        return card

    def select_workday_meeting(self, meeting_folder: Path) -> None:
        self.selected_workday_meeting_folder = meeting_folder
        self._refresh_workday_meetings()
        self.refresh_buttons()

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
        if metadata.get("summary_status") == "draft_created":
            return "Итоги готовы", "ok"
        return "Завершена", "ok"

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
                "Дождитесь завершения обработки встречи. Сейчас обновляются локальные файлы."
            )
            return
        super().closeEvent(event)

    def _create_workday_page(self) -> QWidget:
        page = QWidget()
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
        status_layout.setSpacing(12)
        status_form = QFormLayout()
        status_form.setHorizontalSpacing(18)
        status_form.setVerticalSpacing(8)
        self.workday_status_value = QLabel()
        self.meeting_status_value = QLabel()
        self.day_folder_value = QLabel()
        self.active_meeting_value = QLabel()
        self.obs_status_value = QLabel(self.recorder.status_text)
        status_form.addRow("Статус рабочего дня:", self.workday_status_value)
        status_form.addRow("Статус встречи:", self.meeting_status_value)
        status_form.addRow("Папка дня:", self.day_folder_value)
        status_form.addRow("Активная встреча:", self.active_meeting_value)
        status_form.addRow("Статус OBS:", self.obs_status_value)
        status_layout.addLayout(status_form)
        status_layout.addStretch(1)
        day_actions_layout = QHBoxLayout()
        day_actions_layout.setSpacing(8)
        self.workday_action_button = self._add_button(
            day_actions_layout, "Начать рабочий день", self.start_workday, "primaryButton"
        )
        self.start_workday_button = self.workday_action_button
        self.end_workday_button = self.workday_action_button
        day_actions_layout.addStretch(1)
        status_layout.addLayout(day_actions_layout)

        active_call_layout = QVBoxLayout()
        active_call_layout.setSpacing(10)
        active_call_header = QHBoxLayout()
        active_call_header.setContentsMargins(0, 0, 0, 0)
        self.active_call_title_value = QLabel()
        self.active_call_title_value.setObjectName("heroValue")
        self.active_call_badge = QLabel("Ожидает")
        self.active_call_badge.setObjectName("statusBadge")
        self._apply_badge_style(self.active_call_badge, "wait")
        active_call_header.addWidget(self.active_call_title_value)
        active_call_header.addStretch(1)
        active_call_header.addWidget(self.active_call_badge)
        self.active_call_detail_value = QLabel()
        self.active_call_detail_value.setObjectName("sectionHint")
        self.active_call_detail_value.setWordWrap(True)
        self.active_call_timer_value = QLabel("00:00:00")
        self.active_call_timer_value.setObjectName("callTimer")
        active_call_buttons = QHBoxLayout()
        active_call_buttons.setSpacing(8)
        self.start_meeting_button = self._add_button(
            active_call_buttons, "Начать встречу", self.start_meeting, "primaryButton"
        )
        self.end_meeting_button = self._add_button(
            active_call_buttons, "Завершить встречу", self.end_meeting, "dangerButton"
        )
        active_call_buttons.addStretch(1)
        active_call_layout.addLayout(active_call_header)
        active_call_layout.addWidget(self.active_call_detail_value)
        active_call_layout.addWidget(self.active_call_timer_value, 0, Qt.AlignmentFlag.AlignRight)
        active_call_layout.addStretch(1)
        active_call_layout.addLayout(active_call_buttons)

        day_overview_layout = QHBoxLayout()
        day_overview_layout.setSpacing(14)
        day_overview_layout.addWidget(self._create_card("Состояние дня", status_layout), 1)
        day_overview_layout.addWidget(self._create_card("Активный созвон", active_call_layout), 1)
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
        self.status_label.setStyleSheet("padding: 8px; background: #f3f4f6;")
        layout.addWidget(self.status_label)
        page.setLayout(layout)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("workdayScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(page)
        self.workday_scroll_area = scroll_area
        return scroll_area

    def _create_review_page(self) -> QWidget:
        page = QWidget()
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
        self.meeting_transcript_editor = QPlainTextEdit()
        self.meeting_transcript_editor.setReadOnly(True)
        self.day_summary_editor = QPlainTextEdit()
        self.review_tabs.addTab(self.meeting_summary_editor, "Итоги встречи")
        self.review_tabs.addTab(self.meeting_transcript_editor, "Транскрипт")
        review_content_layout.addWidget(self.review_tabs, 2)

        day_summary_layout = QVBoxLayout()
        day_summary_layout.setSpacing(8)
        self.day_summary_status_label = QLabel(
            "Итоги дня появятся после завершения рабочего дня."
        )
        self.day_summary_status_label.setObjectName("sectionHint")
        self.day_summary_status_label.setWordWrap(True)
        day_summary_layout.addWidget(self.day_summary_status_label)
        day_summary_layout.addWidget(self.day_summary_editor)
        review_content_layout.addWidget(self._create_card("Итоги дня", day_summary_layout), 1)
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
        self.review_status_label.setStyleSheet("padding: 8px; background: #f3f4f6;")
        layout.addWidget(self.review_status_label)
        page.setLayout(layout)
        return page

    def _create_settings_page(self) -> QWidget:
        page = QWidget()
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

        transcription_layout = QFormLayout()
        transcription_layout.setHorizontalSpacing(18)
        transcription_layout.setVerticalSpacing(8)
        self.settings_transcription_backend_select = QComboBox()
        self.settings_transcription_backend_select.addItems(["whisper_cli", "faster_whisper"])
        self._set_combo_value(
            self.settings_transcription_backend_select,
            str(self.config["transcription"]["backend"]),
        )
        self.settings_transcription_model_input = QLineEdit(str(self.config["transcription"]["model"]))
        self.settings_transcription_language_input = QLineEdit(str(self.config["transcription"]["language"]))
        self.settings_transcription_device_input = QLineEdit(str(self.config["transcription"]["device"]))
        self.settings_transcription_compute_type_input = QLineEdit(
            str(self.config["transcription"]["compute_type"])
        )
        self.settings_transcription_command_input = QLineEdit(
            str(self.config["transcription"]["whisper_command"])
        )
        transcription_layout.addRow("Backend:", self.settings_transcription_backend_select)
        transcription_layout.addRow("Модель:", self.settings_transcription_model_input)
        transcription_layout.addRow("Язык:", self.settings_transcription_language_input)
        transcription_layout.addRow("Устройство:", self.settings_transcription_device_input)
        transcription_layout.addRow("Compute type:", self.settings_transcription_compute_type_input)
        transcription_layout.addRow("Whisper command:", self.settings_transcription_command_input)
        layout.addWidget(self._create_card("Транскрипция", transcription_layout))

        summary_layout = QFormLayout()
        summary_layout.setHorizontalSpacing(18)
        summary_layout.setVerticalSpacing(8)
        self.settings_summary_enabled_checkbox = QCheckBox("Генерация итогов включена")
        self.settings_summary_enabled_checkbox.setChecked(bool(self.config["summary"]["enabled"]))
        self.settings_summary_model_input = QLineEdit(str(self.config["summary"]["model"]))
        self.settings_summary_api_key_env_input = QLineEdit(str(self.config["summary"]["api_key_env"]))
        self.settings_summary_base_url_input = QLineEdit(str(self.config["summary"]["base_url"]))
        self.settings_summary_env_file_input = QLineEdit(str(self.config["summary"]["env_file"]))
        self.settings_summary_timeout_input = QSpinBox()
        self.settings_summary_timeout_input.setRange(1, 3600)
        self.settings_summary_timeout_input.setValue(int(self.config["summary"]["timeout_seconds"]))
        self.settings_summary_chunk_input = QSpinBox()
        self.settings_summary_chunk_input.setRange(1000, 200000)
        self.settings_summary_chunk_input.setValue(int(self.config["summary"]["max_chars_per_chunk"]))
        summary_layout.addRow("", self.settings_summary_enabled_checkbox)
        summary_layout.addRow("Модель:", self.settings_summary_model_input)
        summary_layout.addRow("Переменная API key:", self.settings_summary_api_key_env_input)
        summary_layout.addRow("Base URL:", self.settings_summary_base_url_input)
        summary_layout.addRow(".env файл:", self.settings_summary_env_file_input)
        summary_layout.addRow("Timeout, секунд:", self.settings_summary_timeout_input)
        summary_layout.addRow("Символов на chunk:", self.settings_summary_chunk_input)
        layout.addWidget(self._create_card("Summary", summary_layout))

        ui_layout = QFormLayout()
        ui_layout.setHorizontalSpacing(18)
        ui_layout.setVerticalSpacing(8)
        self.settings_theme_select = QComboBox()
        self.settings_theme_select.addItems(["light", "dark_later"])
        self._set_combo_value(
            self.settings_theme_select,
            str(self.config.get("ui", {}).get("theme", "light")),
        )
        theme_hint = QLabel("Темная тема зарезервирована для будущей реализации.")
        theme_hint.setObjectName("sectionHint")
        theme_hint.setWordWrap(True)
        ui_layout.addRow("Тема:", self.settings_theme_select)
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
        self.settings_status_label.setStyleSheet("padding: 8px; background: #f3f4f6;")
        layout.addWidget(self.settings_status_label)

        page.setLayout(layout)
        scroll_area = QScrollArea()
        scroll_area.setObjectName("settingsScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(page)
        return scroll_area

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _create_archive_page(self) -> QWidget:
        page = QWidget()
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
            "endpoint / ProxyAPI отправляется только текст transcript. `config.yaml`, `.env`, записи, "
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
        title, accepted = QInputDialog.getText(self, "Начать встречу", "Название встречи:")
        if not accepted:
            self.status_label.setText("Создание встречи отменено.")
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
        self._refresh_after_lifecycle_change()

    def _on_pipeline_failed(self, message: str) -> None:
        self.pipeline_running = False
        failed_meeting_folder = self.pipeline_meeting_folder
        self.pipeline_meeting_folder = None
        if self._is_workday_pipeline_visible(failed_meeting_folder):
            self._set_pipeline_step("done", "Ошибка", message, "error")
        self.status_label.setText(f"Фоновая обработка встречи не выполнена: {message}")
        self.refresh_buttons()

    def _on_pipeline_thread_finished(self) -> None:
        self.pipeline_thread = None
        self.pipeline_worker = None
        self._start_next_pipeline()

    def _set_pipeline_step(self, step: str, label: str, message: str, state: str) -> None:
        widget = self.pipeline_labels.get(step)
        if widget is None:
            return
        widget.setText(f"{label}: {message}")
        self._apply_status_style(widget, state)

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
            return str(metadata.get("recording_note") or metadata.get("recording_path") or "OBS запись обработана.")
        if step == "audio":
            return str(metadata.get("audio_error") or metadata.get("audio_path") or "Аудио обработано.")
        if step == "transcription":
            return str(
                metadata.get("transcription_error")
                or metadata.get("transcript_path")
                or "Транскрипция обработана."
            )
        if step == "summary":
            return str(metadata.get("summary_error") or metadata.get("summary_path") or "Итоги обработаны.")
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
    def _apply_status_style(label: QLabel, state: str) -> None:
        label.setMinimumHeight(28)
        colors = {
            "ok": ("#dcfce7", "#166534"),
            "active": ("#dbeafe", "#1d4ed8"),
            "wait": ("#f3f4f6", "#4b5563"),
            "skip": ("#f5f5f5", "#525252"),
            "skipped": ("#f5f5f5", "#525252"),
            "error": ("#fee2e2", "#991b1b"),
        }
        background, color = colors.get(state, colors["wait"])
        if label.objectName() == "pipelineStatus":
            label.setStyleSheet(
                f"padding: 0; background: transparent; color: {color};"
            )
            return
        if label.objectName() == "readinessMessage":
            label.setStyleSheet("padding: 0; background: transparent; color: #8a6a58;")
            return
        label.setStyleSheet(
            f"padding: 5px 8px; border-radius: 6px; background: {background}; color: {color};"
        )

    @staticmethod
    def _apply_badge_style(label: QLabel, state: str) -> None:
        colors = {
            "ok": ("#dcfce7", "#166534"),
            "active": ("#dbeafe", "#1d4ed8"),
            "wait": ("#f3e8dc", "#7b4b35"),
            "skip": ("#fef3c7", "#92400e"),
            "skipped": ("#fef3c7", "#92400e"),
            "error": ("#fee2e2", "#991b1b"),
        }
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
        self.status_label.setText(f"Рабочий день завершен. Черновики сохранены: {day_folder}")
        self._refresh_after_lifecycle_change()

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
            self._refresh_review_buttons()
            return

        self._refresh_day_summary_review(day_folder)
        meeting_folders = self.storage.list_today_meeting_folders()
        if (
            self.selected_review_meeting_folder is None
            or self.selected_review_meeting_folder not in meeting_folders
        ):
            self.selected_review_meeting_folder = meeting_folders[0] if meeting_folders else None
        for meeting_folder in meeting_folders:
            self.review_meeting_cards_layout.addWidget(
                self._create_review_meeting_card(
                    meeting_folder,
                    meeting_folder == self.selected_review_meeting_folder,
                )
            )

        if meeting_folders:
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
        self.selected_review_meeting_folder = meeting_folder
        self.refresh_review()

    def load_selected_meeting(self, meeting_folder: Path | None = None) -> None:
        if meeting_folder is None:
            self.meeting_summary_editor.clear()
            self.meeting_transcript_editor.clear()
            self._refresh_review_buttons()
            return
        self.meeting_summary_editor.setPlainText(
            self.storage.read_meeting_summary_draft(meeting_folder)
        )
        self.meeting_transcript_editor.setPlainText(self._read_meeting_transcript(meeting_folder))
        self._refresh_review_buttons()

    def _refresh_day_summary_review(self, day_folder: Path) -> None:
        day_summary_path = day_folder / "00_day_summary_draft.md"
        if day_summary_path.is_file():
            self.day_summary_editor.setEnabled(True)
            self.day_summary_editor.setPlainText(day_summary_path.read_text(encoding="utf-8"))
            self.day_summary_status_label.setText(
                "Итоги дня загружены из локального черновика."
            )
            return
        self.day_summary_editor.clear()
        self.day_summary_editor.setEnabled(False)
        self.day_summary_status_label.setText(
            "Итоги дня появятся после завершения рабочего дня. AI-выжимка из всех встреч будет отдельным будущим PR."
        )

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
        self.settings_status_label.setText(
            "Настройки сохранены в config.yaml. "
            "Для OBS, transcription, summary и папки данных перезапустите приложение, "
            "чтобы все сервисы точно использовали новые значения."
        )

    def _settings_config_from_ui(self) -> dict[str, object]:
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
            "transcription": {
                "backend": self.settings_transcription_backend_select.currentText(),
                "model": self.settings_transcription_model_input.text().strip() or "base",
                "language": self.settings_transcription_language_input.text().strip() or "ru",
                "device": self.settings_transcription_device_input.text().strip() or "cpu",
                "compute_type": self.settings_transcription_compute_type_input.text().strip() or "int8",
                "whisper_command": (
                    self.settings_transcription_command_input.text().strip() or "whisper"
                ),
            },
            "summary": {
                "enabled": self.settings_summary_enabled_checkbox.isChecked(),
                "provider": "openai",
                "model": self.settings_summary_model_input.text().strip() or "gpt-5.4-mini",
                "api_key_env": (
                    self.settings_summary_api_key_env_input.text().strip() or "OPENAI_API_KEY"
                ),
                "base_url": self.settings_summary_base_url_input.text().strip(),
                "env_file": self.settings_summary_env_file_input.text().strip(),
                "timeout_seconds": self.settings_summary_timeout_input.value(),
                "max_chars_per_chunk": self.settings_summary_chunk_input.value(),
            },
            "ui": {
                "theme": self.settings_theme_select.currentText(),
            },
        }

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

    def _refresh_active_call_display(self) -> None:
        if not hasattr(self, "active_call_title_value"):
            return
        if self.storage.meeting_active and self.storage.active_meeting_folder is not None:
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
            return

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
        self.check_readiness_button.setEnabled(not self.pipeline_running)
        self._configure_workday_action_button()
        self.start_meeting_button.setEnabled(
            self.storage.workday_active and not self.storage.meeting_active
        )
        self.end_meeting_button.setEnabled(self.storage.meeting_active)
        self.start_meeting_button.setVisible(
            self.storage.workday_active and not self.storage.meeting_active
        )
        self.end_meeting_button.setVisible(self.storage.meeting_active)
        self.open_day_folder_button.setEnabled(
            self.storage.get_today_day_folder() is not None
        )
        self.open_day_folder_button.setVisible(
            self.storage.get_today_day_folder() is not None
            and not self.storage.list_today_meeting_folders()
        )
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
            not self._has_processing_work()
            and (
                not self.storage.workday_active
                or (self.storage.workday_active and not self.storage.meeting_active)
            )
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
        self.review_open_folder_button.setEnabled(has_day_folder)
        self.save_drafts_button.setEnabled(
            has_day_folder and has_selected_meeting and not self._has_processing_work()
        )
        self.save_final_files_button.setEnabled(
            has_day_folder
            and has_selected_meeting
            and has_day_summary
            and not self._has_processing_work()
        )

    def _has_processing_work(self) -> bool:
        return self.pipeline_running or bool(self.processing_queue)

    def _clear_review_editors(self) -> None:
        self.meeting_summary_editor.clear()
        self.day_summary_editor.clear()
        self.meeting_transcript_editor.clear()
