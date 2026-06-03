from collections.abc import Callable
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
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


class MainWindow(QMainWindow):
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
        self.pipeline_labels: dict[str, QLabel] = {}
        self.pipeline_step_rows: dict[str, QWidget] = {}
        self._apply_app_style()

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.pages.addWidget(self._create_workday_page())
        self.pages.addWidget(self._create_review_page())
        self.pages.addWidget(self._create_placeholder_page(
            "Архив",
            "Здесь позже появится read-only просмотр прошлых рабочих дней и встреч.",
        ))
        self.pages.addWidget(self._create_placeholder_page(
            "Настройки",
            "Здесь позже будут собраны безопасные локальные настройки и диагностика.",
        ))
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
        self.refresh_buttons()
        self.refresh_status()

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
            QLabel#pipelineStepTitle {
                color: #3a1408;
                font-weight: 800;
                min-width: 145px;
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
            QListWidget,
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
    def _create_card(title: str, body_layout) -> QWidget:
        card = QWidget()
        card.setObjectName("card")
        layout = QVBoxLayout()
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        layout.addWidget(title_label)
        layout.addLayout(body_layout)

        card.setLayout(layout)
        return card

    def _create_pipeline_step(self, key: str, title: str) -> QWidget:
        row = QWidget()
        row.setObjectName("pipelineStep")
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("pipelineStepTitle")
        status_label = QLabel()
        status_label.setObjectName("pipelineStatus")
        status_label.setWordWrap(True)
        status_label.setMinimumHeight(28)

        row_layout.addWidget(title_label)
        row_layout.addWidget(status_label, 1)
        row.setLayout(row_layout)

        self.pipeline_labels[key] = status_label
        self.pipeline_step_rows[key] = row
        return row

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
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(
            self._create_page_header(
                "Рабочий день",
                f"Выбранная дата: сегодня, {date.today().strftime('%d.%m.%Y')}",
            )
        )

        readiness_layout = QFormLayout()
        readiness_layout.setHorizontalSpacing(18)
        readiness_layout.setVerticalSpacing(8)
        for component in [
            "OBS",
            "FFmpeg",
            "Whisper",
            "Summary",
            "API key",
            "Summary endpoint",
            "Папка данных",
        ]:
            label = QLabel("Не проверено")
            label.setWordWrap(True)
            self._apply_status_style(label, "wait")
            self.readiness_labels[component] = label
            readiness_layout.addRow(f"{component}:", label)
        layout.addWidget(self._create_card("Готовность системы", readiness_layout))

        status_layout = QFormLayout()
        status_layout.setHorizontalSpacing(18)
        status_layout.setVerticalSpacing(8)
        self.workday_status_value = QLabel()
        self.meeting_status_value = QLabel()
        self.day_folder_value = QLabel()
        self.active_meeting_value = QLabel()
        self.obs_status_value = QLabel(self.recorder.status_text)
        status_layout.addRow("Статус рабочего дня:", self.workday_status_value)
        status_layout.addRow("Статус встречи:", self.meeting_status_value)
        status_layout.addRow("Папка дня:", self.day_folder_value)
        status_layout.addRow("Активная встреча:", self.active_meeting_value)
        status_layout.addRow("Статус OBS:", self.obs_status_value)

        active_call_layout = QVBoxLayout()
        active_call_layout.setSpacing(8)
        self.active_call_title_value = QLabel()
        self.active_call_title_value.setObjectName("heroValue")
        self.active_call_detail_value = QLabel()
        self.active_call_detail_value.setObjectName("sectionHint")
        self.active_call_detail_value.setWordWrap(True)
        active_call_layout.addWidget(self.active_call_title_value)
        active_call_layout.addWidget(self.active_call_detail_value)
        active_call_layout.addStretch()

        day_overview_layout = QHBoxLayout()
        day_overview_layout.setSpacing(14)
        day_overview_layout.addWidget(self._create_card("Состояние дня", status_layout), 1)
        day_overview_layout.addWidget(self._create_card("Активный созвон", active_call_layout), 1)
        layout.addLayout(day_overview_layout)

        meetings_layout = QVBoxLayout()
        meetings_layout.setSpacing(8)
        self.today_meetings_value = QLabel()
        self.today_meetings_value.setObjectName("sectionHint")
        self.today_meetings_value.setWordWrap(True)
        meetings_layout.addWidget(self.today_meetings_value)
        layout.addWidget(self._create_card("Встречи за день", meetings_layout))

        pipeline_layout = QVBoxLayout()
        pipeline_layout.setSpacing(8)
        pipeline_hint = QLabel(
            "Показывает локальную обработку последней завершенной встречи: запись, аудио, transcript и итоги."
        )
        pipeline_hint.setObjectName("sectionHint")
        pipeline_hint.setWordWrap(True)
        pipeline_layout.addWidget(pipeline_hint)
        for key, title in [
            ("meeting", "Созвон"),
            ("recording", "OBS запись"),
            ("audio", "Извлечение аудио"),
            ("transcription", "Транскрипция"),
            ("summary", "Генерация итогов"),
            ("done", "Готово"),
        ]:
            pipeline_layout.addWidget(self._create_pipeline_step(key, title))
        layout.addWidget(self._create_card("Pipeline встречи", pipeline_layout))

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)
        self.check_readiness_button = self._add_button(
            actions_layout, "Проверить готовность", self.check_readiness
        )
        self.start_workday_button = self._add_button(
            actions_layout, "Начать рабочий день", self.start_workday
        )
        self.start_meeting_button = self._add_button(
            actions_layout, "Начать встречу", self.start_meeting
        )
        self.end_meeting_button = self._add_button(
            actions_layout, "Завершить встречу", self.end_meeting
        )
        self.end_workday_button = self._add_button(
            actions_layout, "Завершить рабочий день", self.end_workday
        )
        self.open_day_folder_button = self._add_button(
            actions_layout, "Открыть папку дня", self.open_day_folder
        )
        self._add_button(actions_layout, "Проверить OBS", self.check_obs)
        actions_layout.addStretch()
        layout.addWidget(self._create_card("Действия", actions_layout))

        self.status_label = QLabel(self._startup_status())
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("padding: 8px; background: #f3f4f6;")
        layout.addWidget(self.status_label)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _create_review_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addWidget(
            self._create_page_header(
                "Ревью",
                "Проверьте черновики итогов встреч, итогов дня и задач перед сохранением финальных файлов.",
            )
        )

        content_layout = QHBoxLayout()
        meetings_layout = QVBoxLayout()
        self.meeting_list = QListWidget()
        self.meeting_list.currentItemChanged.connect(self.load_selected_meeting)
        meetings_layout.addWidget(self.meeting_list)
        meetings_group = self._create_card("Встречи за сегодня", meetings_layout)
        meetings_group.setMinimumWidth(260)
        content_layout.addWidget(meetings_group)

        self.review_tabs = QTabWidget()
        self.meeting_summary_editor = QPlainTextEdit()
        self.day_summary_editor = QPlainTextEdit()
        self.tasks_editor = QPlainTextEdit()
        self.review_tabs.addTab(self.meeting_summary_editor, "Итоги встречи")
        self.review_tabs.addTab(self.day_summary_editor, "Итоги дня")
        self.review_tabs.addTab(self.tasks_editor, "Задачи")
        content_layout.addWidget(self.review_tabs, 1)
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

    @staticmethod
    def _create_help_page() -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Справка")
        title.setObjectName("pageTitle")
        help_text = QLabel(
            "Текущий сценарий MVP:\n\n"
            "1. Начать рабочий день.\n"
            "2. Начать встречу.\n"
            "3. Завершить встречу.\n"
            "4. Завершить рабочий день.\n"
            "5. Открыть ревью.\n"
            "6. Проверить черновики и сохранить финальные файлы.\n\n"
            "OBS можно включить в локальном config.yaml. По умолчанию запись выключена.\n"
            "FFmpeg используется локально для извлечения audio.wav из OBS-записи.\n"
            "Транскрипция может выполняться локально через Whisper CLI, если он доступен в PATH.\n"
            "Генерация итогов через OpenAI по умолчанию выключена и использует только текстовый транскрипт."
        )
        help_text.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(help_text)
        layout.addStretch()
        page.setLayout(layout)
        return page

    @staticmethod
    def _add_button(layout, label: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(label)
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
        if self.pipeline_running:
            message = f"{message} Предыдущая встреча еще обрабатывается в фоне."
        else:
            self._set_pipeline_step("meeting", "Готово", "Созвон начат.", "ok")
            self._set_pipeline_step("recording", "Выполняется", "OBS ведет запись или шаг пропущен.", "active")
            self._set_pipeline_step("audio", "Ожидает", "Ждет завершение встречи.", "wait")
            self._set_pipeline_step("transcription", "Ожидает", "Ждет audio.wav.", "wait")
            self._set_pipeline_step("summary", "Ожидает", "Ждет transcript.", "wait")
            self._set_pipeline_step("done", "Ожидает", "Встреча еще идет.", "wait")
        self.status_label.setText(message)
        self._refresh_after_lifecycle_change()

    def end_meeting(self) -> None:
        if not self.storage.meeting_active:
            self.status_label.setText("Нет активной встречи для завершения.")
            return
        finishing_meeting_folder = self.storage.active_meeting_folder
        processing_already_running = self.pipeline_running
        if not self.pipeline_running:
            self.pipeline_meeting_folder = finishing_meeting_folder
        self.pipeline_completed = False
        if not self.pipeline_running:
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
        self._set_pipeline_step(step, label, default_message, state)
        self.status_label.setText(default_message)

    def _on_pipeline_finished(self, meeting_folder_text: str) -> None:
        meeting_folder = self.pipeline_meeting_folder or Path(meeting_folder_text)
        metadata = self.storage.read_meeting_metadata(meeting_folder)
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
        self.pipeline_meeting_folder = None
        self._set_pipeline_step("done", "Ошибка", message, "error")
        self.status_label.setText(f"Фоновая обработка встречи не выполнена: {message}")
        self.refresh_buttons()

    def _on_pipeline_thread_finished(self) -> None:
        self.pipeline_thread = None
        self.pipeline_worker = None
        self._start_next_pipeline()

    def _set_pipeline_step(self, step: str, label: str, message: str, state: str) -> None:
        widget = self.pipeline_labels[step]
        widget.setText(f"{label}: {message}")
        self._apply_status_style(widget, state)

    def _read_pipeline_metadata(self) -> dict[str, object]:
        if self.pipeline_meeting_folder is None:
            return {}
        return self.storage.read_meeting_metadata(self.pipeline_meeting_folder)

    def _refresh_pipeline_from_metadata(self, metadata: dict[str, object]) -> None:
        self._set_pipeline_step("meeting", "Готово", "Созвон завершен.", "ok")
        for step in ["recording", "audio", "transcription", "summary"]:
            label, state = self._step_status_from_metadata(step, metadata)
            self._set_pipeline_step(step, label, self._step_message(step, metadata), state)
        self._set_pipeline_step("done", "Готово", "Metadata обновлена.", "ok")

    def _step_status_from_metadata(
        self,
        step: str,
        metadata: dict[str, object],
    ) -> tuple[str, str]:
        status = str(metadata.get(f"{step}_status") or "")
        if step == "recording":
            status = str(metadata.get("recording_status") or "")
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
                f"padding: 2px 0; background: transparent; color: {color};"
            )
            return
        label.setStyleSheet(
            f"padding: 5px 8px; border-radius: 6px; background: {background}; color: {color};"
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
        self.meeting_list.clear()
        day_folder = self.storage.get_today_day_folder()
        if day_folder is None:
            self._clear_review_editors()
            self.review_status_label.setText("Папка сегодняшнего рабочего дня пока не создана.")
            self._refresh_review_buttons()
            return

        self.day_summary_editor.setPlainText(self.storage.read_day_summary_draft(day_folder))
        self.tasks_editor.setPlainText(self.storage.read_tasks_draft(day_folder))
        meeting_folders = self.storage.list_today_meeting_folders()
        for meeting_folder in meeting_folders:
            metadata = self.storage.read_meeting_metadata(meeting_folder)
            item = QListWidgetItem(metadata.get("title") or meeting_folder.name)
            item.setData(Qt.ItemDataRole.UserRole, str(meeting_folder))
            self.meeting_list.addItem(item)

        if meeting_folders:
            self.meeting_list.setCurrentRow(0)
            self.review_status_label.setText("Локальные файлы ревью загружены.")
        else:
            self.meeting_summary_editor.clear()
            self.review_status_label.setText("За сегодня пока нет встреч.")
        self._refresh_review_buttons()

    def load_selected_meeting(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None = None,
    ) -> None:
        del previous
        if current is None:
            self.meeting_summary_editor.clear()
            self._refresh_review_buttons()
            return
        meeting_folder = Path(current.data(Qt.ItemDataRole.UserRole))
        self.meeting_summary_editor.setPlainText(
            self.storage.read_meeting_summary_draft(meeting_folder)
        )
        self._refresh_review_buttons()

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
        self.storage.save_day_summary_draft(day_folder, self.day_summary_editor.toPlainText())
        self.storage.save_tasks_draft(day_folder, self.tasks_editor.toPlainText())
        self.review_status_label.setText("Черновики сохранены локально.")

    def save_final_files(self) -> None:
        selected_meeting = self._selected_meeting_folder()
        if selected_meeting is None:
            self.review_status_label.setText("Выберите встречу для сохранения финальных файлов.")
            return
        self.storage.save_final_files(
            selected_meeting,
            self.meeting_summary_editor.toPlainText(),
            self.day_summary_editor.toPlainText(),
            self.tasks_editor.toPlainText(),
        )
        self.review_status_label.setText("Финальные файлы сохранены локально. Черновики не удалены.")

    def save_final_summaries(self) -> None:
        self.save_final_files()

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

    def _selected_meeting_folder(self) -> Path | None:
        current = self.meeting_list.currentItem()
        if current is None:
            return None
        return Path(current.data(Qt.ItemDataRole.UserRole))

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
        self.refresh_buttons()
        self.refresh_status()
        if self.pages.currentIndex() == 1:
            self.refresh_review()

    def refresh_buttons(self) -> None:
        self.check_readiness_button.setEnabled(not self.pipeline_running)
        self.start_workday_button.setEnabled(
            not self._has_processing_work() and not self.storage.workday_active
        )
        self.start_meeting_button.setEnabled(
            self.storage.workday_active and not self.storage.meeting_active
        )
        self.end_meeting_button.setEnabled(self.storage.meeting_active)
        self.end_workday_button.setEnabled(
            not self._has_processing_work()
            and self.storage.workday_active
            and not self.storage.meeting_active
        )
        self.open_day_folder_button.setEnabled(
            self.storage.get_today_day_folder() is not None
        )
        self._refresh_review_buttons()

    def refresh_status(self) -> None:
        day_folder = self.storage.get_today_day_folder()
        self.workday_status_value.setText("активен" if self.storage.workday_active else "не активен")
        self.meeting_status_value.setText("активна" if self.storage.meeting_active else "не активна")
        self.day_folder_value.setText(str(day_folder) if day_folder else "не создана")
        self.active_meeting_value.setText(
            self.storage.active_meeting_folder.name if self.storage.meeting_active else "нет"
        )
        self.obs_status_value.setText(self.recorder.status_text)
        if self.storage.meeting_active and self.storage.active_meeting_folder is not None:
            self.active_call_title_value.setText(self.storage.active_meeting_folder.name)
            self.active_call_detail_value.setText(
                "Встреча идет сейчас. Управление записью доступно в блоке действий."
            )
        elif self.pipeline_running:
            self.active_call_title_value.setText("Нет активного созвона")
            self.active_call_detail_value.setText(
                "Можно начать следующую встречу, пока предыдущая обрабатывается в фоне."
            )
        else:
            self.active_call_title_value.setText("Нет активного созвона")
            self.active_call_detail_value.setText(
                "Когда рабочий день активен, нажмите «Начать встречу» в блоке действий."
            )
        meeting_count = len(self.storage.list_today_meeting_folders()) if day_folder else 0
        if meeting_count == 0:
            self.today_meetings_value.setText("За выбранный день пока нет созданных встреч.")
        else:
            self.today_meetings_value.setText(
                f"Создано встреч за день: {meeting_count}. Детали доступны на экране ревью."
            )
        if not self.storage.meeting_active and not self.pipeline_running and not self.pipeline_completed:
            self._set_pipeline_step("meeting", "Ожидает", "Созвон не начат.", "wait")
            self._set_pipeline_step("recording", "Ожидает", "Созвон не начат.", "wait")
            self._set_pipeline_step("audio", "Ожидает", "Созвон не начат.", "wait")
            self._set_pipeline_step("transcription", "Ожидает", "Созвон не начат.", "wait")
            self._set_pipeline_step("summary", "Ожидает", "Созвон не начат.", "wait")
            self._set_pipeline_step("done", "Ожидает", "Созвон не начат.", "wait")

    def _refresh_review_buttons(self) -> None:
        has_day_folder = self.storage.get_today_day_folder() is not None
        has_selected_meeting = self._selected_meeting_folder() is not None
        self.review_open_folder_button.setEnabled(has_day_folder)
        self.save_drafts_button.setEnabled(
            has_day_folder and has_selected_meeting and not self._has_processing_work()
        )
        self.save_final_files_button.setEnabled(
            has_day_folder and has_selected_meeting and not self._has_processing_work()
        )

    def _has_processing_work(self) -> bool:
        return self.pipeline_running or bool(self.processing_queue)

    def _clear_review_editors(self) -> None:
        self.meeting_summary_editor.clear()
        self.day_summary_editor.clear()
        self.tasks_editor.clear()
