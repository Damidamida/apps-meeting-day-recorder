import logging
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.first_run import (
    AITUNNEL_API_KEY_ENV,
    AITUNNEL_BASE_URL_DEFAULT,
    AITUNNEL_REQUIRED_MESSAGE,
    DEFAULT_ENV_FILE,
    FIRST_RUN_STEPS,
    SUMMARY_MODEL_OPTIONS,
    TRANSCRIPTION_OPTIONS,
    FirstRunState,
    check_aitunnel_key,
    check_summary_settings,
    check_transcription_settings,
    default_transcription_model_for_backend,
    mark_setup_completed,
    mark_step_error,
    mark_step_ok,
    mark_step_checking,
    normalize_setup_config,
    setup_config_from_state,
    setup_completed,
    transcription_model_options_for_backend,
    validate_data_root,
)
from app.services.recorder import ObsRecorder

logger = logging.getLogger(__name__)
_STEP_ICON_FONT_LOADED = False
OBS_CHECKING_MESSAGE = "Проверяется..."
OBS_ERROR_MESSAGE = (
    "OBS не подключен. Проверьте адрес, порт, пароль и включенный WebSocket в OBS."
)
AITUNNEL_CHECKING_MESSAGE = "Проверяется ключ..."
AITUNNEL_EMPTY_KEY_MESSAGE = "Введите AI Tunnel key."
SUMMARY_CHECKING_MESSAGE = "Проверяются AI-итоги..."
SUMMARY_ERROR_MESSAGE = "Сервис временно недоступен."


STEP_DESCRIPTIONS = {
    "data_root": "Где будут храниться рабочие дни",
    "obs": "Подключение к записи разговоров",
    "audio": "Проверка извлечения аудио",
    "aitunnel": "Один ключ для транскрипции и итогов",
    "transcription": "Проверка распознавания речи",
    "summary": "Проверка итогов встреч и дня",
    "finish": "Финальная проверка перед стартом",
}


def _step_description(step_key: str) -> str:
    return STEP_DESCRIPTIONS.get(step_key, "Описание шага не задано.")


def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _ensure_step_icon_font_loaded() -> None:
    global _STEP_ICON_FONT_LOADED
    if _STEP_ICON_FONT_LOADED:
        return
    icon_font = Path("C:/Windows/Fonts/segmdl2.ttf")
    if icon_font.is_file():
        QFontDatabase.addApplicationFont(str(icon_font))
    _STEP_ICON_FONT_LOADED = True


class StepCard(QFrame):
    clicked = Signal(str)

    def __init__(self, step_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _ensure_step_icon_font_loaded()
        self.step_key = step_key
        self.setObjectName("firstRunStepCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(286)
        self.setMinimumHeight(76)
        self.setMaximumHeight(82)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout()
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        self.number_label = QLabel("")
        self.number_label.setObjectName("firstRunStepNumber")
        self.number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.number_label.setFixedSize(28, 28)
        layout.addWidget(self.number_label, 0, Qt.AlignmentFlag.AlignTop)

        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        self.title_label = QLabel("")
        self.title_label.setObjectName("firstRunStepTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.note_label = QLabel("")
        self.note_label.setObjectName("firstRunStepNote")
        self.note_label.setWordWrap(True)
        self.note_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.note_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        text_layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignTop)
        text_layout.addWidget(self.note_label, 0, Qt.AlignmentFlag.AlignTop)
        text_layout.addStretch(1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("firstRunStepStatusIcon")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(False)
        self.status_label.setMinimumHeight(20)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        content_layout.addLayout(text_layout, 1)
        content_layout.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(content_layout, 1)
        self.setLayout(layout)

    def click(self) -> None:
        if self.isEnabled():
            self.clicked.emit(self.step_key)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.clicked.emit(self.step_key)
        super().mouseReleaseEvent(event)

    def set_card_state(
        self,
        *,
        index: int,
        title: str,
        note: str,
        status_text: str,
        state: str,
        active: bool,
        enabled: bool,
    ) -> None:
        self.number_label.setText(str(index))
        self.title_label.setText(title)
        self.note_label.setText(note)
        self.status_label.setText(status_text)
        if state in {"locked", "done", "error"}:
            self.status_label.setFont(QFont("Segoe MDL2 Assets", 10))
        else:
            self.status_label.setFont(QFont())
        self.status_label.setVisible(bool(status_text))
        self.status_label.setToolTip(self._status_tooltip(state, status_text))
        locked = state == "locked"
        for widget in (
            self,
            self.number_label,
            self.title_label,
            self.note_label,
            self.status_label,
        ):
            widget.setProperty("state", state)
            widget.setProperty("active", active)
            widget.setProperty("locked", locked)
        self.setEnabled(enabled)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if enabled
            else Qt.CursorShape.ForbiddenCursor
        )
        for widget in (
            self,
            self.number_label,
            self.title_label,
            self.note_label,
            self.status_label,
        ):
            _repolish(widget)

    @staticmethod
    def _status_tooltip(state: str, status_text: str) -> str:
        if state == "locked":
            return "Заблокировано"
        if state == "done":
            return "Готово"
        if state == "error":
            return "Ошибка проверки"
        return status_text


class FirstRunCheckWorker(QObject):
    finished = Signal(bool, str)

    def __init__(
        self,
        check: Callable[[], tuple[bool, str]],
        *,
        fallback_message: str,
        log_message: str,
    ) -> None:
        super().__init__()
        self.check = check
        self.fallback_message = fallback_message
        self.log_message = log_message

    @Slot()
    def run(self) -> None:
        try:
            ok, message = self.check()
        except Exception:
            logger.exception(self.log_message)
            self.finished.emit(False, self.fallback_message)
            return
        self.finished.emit(ok, message)


class FirstRunWizard(QWidget):
    config_changed = Signal(dict)
    completed = Signal(dict)

    def __init__(
        self,
        config: dict[str, Any],
        state: FirstRunState | dict[str, Any],
        recorder: Any | None = None,
        parent: QWidget | None = None,
        obs_recorder_factory: Callable[..., Any] | None = None,
        aitunnel_client_factory: Callable[..., Any] | None = None,
        summary_client_factory: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.state = (
            state if isinstance(state, FirstRunState) else normalize_setup_config(state)
        )
        self.recorder = recorder
        self.obs_recorder_factory = obs_recorder_factory or ObsRecorder
        self.pending_obs_config: dict[str, Any] | None = None
        self.aitunnel_client_factory = aitunnel_client_factory
        self.summary_client_factory = summary_client_factory
        self.step_buttons: dict[str, StepCard] = {}
        self.step_status_labels: dict[str, QLabel] = {}
        self.step_indexes: dict[str, int] = {}
        self.step_message_label: dict[str, QLabel] = {}
        self.step_pages: dict[str, QWidget] = {}
        self.obs_check_button: QPushButton | None = None
        self.obs_check_thread: QThread | None = None
        self.obs_check_worker: FirstRunCheckWorker | None = None
        self.obs_check_running = False
        self.aitunnel_check_button: QPushButton | None = None
        self.aitunnel_check_thread: QThread | None = None
        self.aitunnel_check_worker: FirstRunCheckWorker | None = None
        self.aitunnel_check_running = False
        self.summary_check_button: QPushButton | None = None
        self.summary_check_thread: QThread | None = None
        self.summary_check_worker: FirstRunCheckWorker | None = None
        self.summary_check_running = False
        self.finish_button: QPushButton | None = None
        self.current_step = self.state.current_step
        self.setObjectName("firstRunWizard")
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("firstRunWizardShell")
        shell_layout = QVBoxLayout()
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(12)

        title = QLabel("Настройка BK Scribe")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Пройдите обязательные проверки перед первым рабочим днем. "
            "Настройки сохраняются локально только после успешной проверки шага."
        )
        subtitle.setObjectName("sectionHint")
        subtitle.setWordWrap(True)

        heading_layout = QVBoxLayout()
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(8)
        heading_layout.addWidget(title)
        heading_layout.addWidget(subtitle)
        shell_layout.addLayout(heading_layout)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("firstRunProgressPill")

        intro = QFrame()
        intro.setObjectName("firstRunWizardIntro")
        intro_layout = QHBoxLayout()
        intro_layout.setContentsMargins(0, 0, 0, 0)
        intro_layout.setSpacing(14)
        intro_note = QLabel(
            "Рабочий день откроется после успешной проверки OBS, аудио, AI Tunnel, транскрипции и AI-итогов."
        )
        intro_note.setObjectName("firstRunIntroNote")
        intro_note.setWordWrap(True)
        intro_layout.addWidget(intro_note, 1)
        intro_layout.addWidget(self.progress_label, 0, Qt.AlignmentFlag.AlignTop)
        intro.setLayout(intro_layout)
        shell_layout.addWidget(intro)

        body_frame = QFrame()
        body_frame.setObjectName("firstRunWizardBody")
        body_frame.setMinimumHeight(560)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        self.step_list_panel = QFrame()
        self.step_list_panel.setObjectName("firstRunStepList")
        self.step_list_panel.setMinimumWidth(320)
        self.step_list_panel.setMaximumWidth(340)
        self.step_list_panel.setMinimumHeight(560)
        step_list_layout = QVBoxLayout()
        step_list_layout.setContentsMargins(8, 8, 8, 8)
        step_list_layout.setSpacing(6)
        for index, step_key in enumerate(FIRST_RUN_STEPS, start=1):
            card = StepCard(step_key)
            card.clicked.connect(self.open_step)
            step_list_layout.addWidget(card)
            self.step_buttons[step_key] = card
            self.step_status_labels[step_key] = card.status_label
            self.step_indexes[step_key] = index
        step_list_layout.addStretch(1)
        self.step_list_panel.setLayout(step_list_layout)
        body.addWidget(self.step_list_panel, 0)

        self.step_content_panel = QFrame()
        self.step_content_panel.setObjectName("firstRunStepContent")
        self.step_content_panel.setMinimumWidth(620)
        self.step_content_panel.setMinimumHeight(560)
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        panel_header = QFrame()
        panel_header.setObjectName("firstRunPanelHeader")
        panel_header_layout = QHBoxLayout()
        panel_header_layout.setContentsMargins(16, 14, 16, 14)
        panel_header_layout.setSpacing(16)
        header_text_layout = QVBoxLayout()
        header_text_layout.setContentsMargins(0, 0, 0, 0)
        header_text_layout.setSpacing(5)
        self.current_step_title = QLabel("")
        self.current_step_title.setObjectName("firstRunPanelTitle")
        self.current_step_title.setWordWrap(True)
        self.current_step_hint = QLabel("")
        self.current_step_hint.setObjectName("firstRunPanelHint")
        self.current_step_hint.setWordWrap(True)
        header_text_layout.addWidget(self.current_step_title)
        header_text_layout.addWidget(self.current_step_hint)
        self.current_step_status = QLabel("")
        self.current_step_status.setObjectName("firstRunStatusBadge")
        panel_header_layout.addLayout(header_text_layout, 1)
        panel_header_layout.addWidget(self.current_step_status, 0, Qt.AlignmentFlag.AlignTop)
        panel_header.setLayout(panel_header_layout)
        content_layout.addWidget(panel_header)

        panel_body = QFrame()
        panel_body.setObjectName("firstRunPanelBody")
        panel_body_layout = QVBoxLayout()
        panel_body_layout.setContentsMargins(16, 16, 16, 16)
        panel_body_layout.setSpacing(12)
        self.step_stack = QStackedWidget()
        for step_key in FIRST_RUN_STEPS:
            page = self._create_step_page(step_key)
            self.step_pages[step_key] = page
            self.step_stack.addWidget(page)
        panel_body_layout.addWidget(self.step_stack, 1)
        panel_body.setLayout(panel_body_layout)
        content_layout.addWidget(panel_body, 1)

        self.step_content_panel.setLayout(content_layout)
        body.addWidget(self.step_content_panel, 1)
        body_frame.setLayout(body)
        shell_layout.addWidget(body_frame, 1)
        shell.setLayout(shell_layout)
        root.addWidget(shell, 1)
        self.setLayout(root)

    def _create_step_page(self, step_key: str) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout()
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        form_card = QFrame()
        form_card.setObjectName("firstRunFormCard")
        form_card.setMaximumWidth(920)
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        self.step_message_label[step_key] = QLabel("")
        self.step_message_label[step_key].setWordWrap(True)
        self.step_message_label[step_key].setObjectName("inlineStatus")
        layout.addWidget(self.step_message_label[step_key])

        if step_key == "data_root":
            self.data_root_input = QLineEdit(
                str(self.state.values.get("data_root") or "")
            )
            browse = QPushButton("Выбрать папку")
            browse.setMaximumWidth(180)
            browse.clicked.connect(self.choose_data_root)
            check = QPushButton("Проверить папку данных")
            check.setObjectName("primaryButton")
            check.setMaximumWidth(230)
            check.clicked.connect(self.check_data_root)
            description = QLabel("Папка для рабочих дней, записей, transcript и итогов.")
            description.setObjectName("sectionHint")
            description.setWordWrap(True)
            actions = QHBoxLayout()
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(10)
            actions.addWidget(browse)
            actions.addWidget(check)
            actions.addStretch(1)
            layout.addWidget(description)
            layout.addWidget(self.data_root_input)
            layout.addLayout(actions)
        elif step_key == "obs":
            obs_settings = self._stored_obs_settings()
            self.obs_host_input = QLineEdit()
            self.obs_host_input.setObjectName("firstRunObsHostInput")
            self.obs_host_input.setText(obs_settings["websocket_host"])
            self.obs_host_input.setPlaceholderText("localhost")
            self.obs_host_input.setMaximumWidth(360)
            self.obs_port_input = QSpinBox()
            self.obs_port_input.setObjectName("firstRunObsPortInput")
            self.obs_port_input.setRange(1, 65535)
            self.obs_port_input.setValue(obs_settings["websocket_port"])
            self.obs_port_input.setMaximumWidth(140)
            self.obs_password_input = QLineEdit()
            self.obs_password_input.setObjectName("firstRunObsPasswordInput")
            self.obs_password_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.obs_password_input.setText(obs_settings["websocket_password"])
            self.obs_password_input.setPlaceholderText("Пароль OBS WebSocket")
            self.obs_password_input.setMaximumWidth(360)
            check = QPushButton("Проверить OBS")
            check.setObjectName("primaryButton")
            check.setMaximumWidth(180)
            check.clicked.connect(self.check_obs)
            self.obs_check_button = check
            description = QLabel(
                "Запустите OBS и включите WebSocket. Автонастройка OBS не выполняется."
            )
            description.setObjectName("sectionHint")
            description.setWordWrap(True)
            obs_form = QFrame()
            obs_form.setObjectName("firstRunObsForm")
            obs_form_layout = QFormLayout()
            obs_form_layout.setContentsMargins(0, 0, 0, 0)
            obs_form_layout.setSpacing(8)
            obs_form_layout.addRow("Адрес WebSocket", self.obs_host_input)
            obs_form_layout.addRow("Порт WebSocket", self.obs_port_input)
            obs_form_layout.addRow("Пароль WebSocket", self.obs_password_input)
            obs_form.setLayout(obs_form_layout)
            actions = QHBoxLayout()
            actions.addWidget(check)
            actions.addStretch(1)
            layout.addWidget(description)
            layout.addWidget(obs_form)
            layout.addLayout(actions)
        elif step_key == "audio":
            check = QPushButton("Проверить аудио")
            check.setObjectName("primaryButton")
            check.setMaximumWidth(190)
            check.clicked.connect(self.check_audio)
            description = QLabel("Проверяем доступность FFmpeg для извлечения audio.wav.")
            description.setObjectName("sectionHint")
            description.setWordWrap(True)
            actions = QHBoxLayout()
            actions.addWidget(check)
            actions.addStretch(1)
            layout.addWidget(description)
            layout.addLayout(actions)
        elif step_key == "aitunnel":
            self.aitunnel_key_input = QLineEdit()
            self.aitunnel_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.aitunnel_key_input.setPlaceholderText("Вставьте AI Tunnel key")
            self.aitunnel_link = QLabel(
                '<a href="https://aitunnel.ru/">Получить ключ на https://aitunnel.ru/</a>'
            )
            self.aitunnel_link.setOpenExternalLinks(True)
            check = QPushButton("Проверить ключ AI Tunnel")
            check.setObjectName("primaryButton")
            check.setMaximumWidth(230)
            check.clicked.connect(self.check_aitunnel)
            self.aitunnel_check_button = check
            description = QLabel("Один ключ используется для AI Tunnel STT и AI-итогов.")
            description.setObjectName("sectionHint")
            description.setWordWrap(True)
            actions = QHBoxLayout()
            actions.addWidget(check)
            actions.addStretch(1)
            layout.addWidget(description)
            layout.addWidget(self.aitunnel_key_input)
            layout.addWidget(self.aitunnel_link)
            layout.addLayout(actions)
        elif step_key == "transcription":
            self.transcription_backend_select = QComboBox()
            for value, label in TRANSCRIPTION_OPTIONS:
                self.transcription_backend_select.addItem(label, value)
            self.transcription_model_select = QComboBox()
            self.transcription_model_select.setEditable(False)
            self._set_combo_value(
                self.transcription_backend_select,
                self._stored_transcription_backend(),
            )
            self.transcription_backend_select.currentIndexChanged.connect(
                self._refresh_transcription_model_options
            )
            self._refresh_transcription_model_options()
            check = QPushButton("Проверить транскрипцию")
            check.setObjectName("primaryButton")
            check.setMaximumWidth(230)
            check.clicked.connect(self.check_transcription)
            description = QLabel("Проверка не создает встречу и не загружает реальное аудио.")
            description.setObjectName("sectionHint")
            description.setWordWrap(True)
            actions = QHBoxLayout()
            actions.addWidget(check)
            actions.addStretch(1)
            layout.addWidget(description)
            layout.addWidget(self.transcription_backend_select)
            layout.addWidget(self.transcription_model_select)
            layout.addLayout(actions)
        elif step_key == "summary":
            self.summary_page = page
            self.summary_model_select = QComboBox()
            for value, label in SUMMARY_MODEL_OPTIONS:
                self.summary_model_select.addItem(label, value)
            self._set_combo_value(self.summary_model_select, self._stored_summary_model())
            check = QPushButton("Проверить AI-итоги")
            check.setObjectName("primaryButton")
            check.setMaximumWidth(190)
            check.clicked.connect(self.check_summary)
            self.summary_check_button = check
            description = QLabel("AI-итоги используют ключ из шага AI Tunnel.")
            description.setObjectName("sectionHint")
            description.setWordWrap(True)
            actions = QHBoxLayout()
            actions.addWidget(check)
            actions.addStretch(1)
            layout.addWidget(description)
            layout.addWidget(self.summary_model_select)
            layout.addLayout(actions)
        else:
            description = QLabel("Все обязательные проверки должны быть готовы.")
            description.setObjectName("sectionHint")
            description.setWordWrap(True)
            finish_hint = QLabel(
                "Рабочий день откроется после успешной настройки всех обязательных пунктов."
            )
            finish_hint.setObjectName("sectionHint")
            finish_hint.setWordWrap(True)
            self.finish_button = QPushButton("Начать работу")
            self.finish_button.setObjectName("primaryButton")
            self.finish_button.setMaximumWidth(180)
            self.finish_button.clicked.connect(self.finish_setup)
            actions = QHBoxLayout()
            actions.addWidget(self.finish_button)
            actions.addStretch(1)
            layout.addWidget(description)
            layout.addWidget(finish_hint)
            layout.addLayout(actions)
        layout.addStretch(1)
        form_card.setLayout(layout)
        page_layout.addWidget(form_card, 0, Qt.AlignmentFlag.AlignTop)
        page_layout.addStretch(1)
        page.setLayout(page_layout)
        if step_key != "summary" and not hasattr(self, "summary_page"):
            self.summary_page = QWidget()
        return page

    def choose_data_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выбрать папку данных")
        if folder:
            self.data_root_input.setText(folder)

    def _stored_obs_settings(self) -> dict[str, Any]:
        obs = self.config.get("obs")
        obs = obs if isinstance(obs, dict) else {}
        values = self.state.values if isinstance(self.state.values, dict) else {}
        return {
            "websocket_host": str(
                obs.get("websocket_host")
                or values.get("obs_websocket_host")
                or "localhost"
            ).strip()
            or "localhost",
            "websocket_port": self._safe_obs_port(
                obs.get("websocket_port") or values.get("obs_websocket_port") or 4455
            ),
            "websocket_password": str(obs.get("websocket_password") or ""),
        }

    @staticmethod
    def _safe_obs_port(value: Any) -> int:
        try:
            port = int(value)
        except (TypeError, ValueError):
            return 4455
        return port if 1 <= port <= 65535 else 4455

    def _current_obs_settings(self) -> dict[str, Any]:
        return {
            "websocket_host": self.obs_host_input.text().strip() or "localhost",
            "websocket_port": int(self.obs_port_input.value()),
            "websocket_password": self.obs_password_input.text(),
        }

    def open_step(self, step_key: str) -> None:
        if not self.step_buttons[step_key].isEnabled():
            return
        self.current_step = step_key
        self.refresh()

    def check_data_root(self) -> None:
        path = Path(self.data_root_input.text().strip())
        result = validate_data_root(path)
        if result.ok:
            self.config.setdefault("storage", {})["root"] = str(path.expanduser())
            self.state.values["data_root"] = str(path.expanduser())
            self._mark_ok("data_root", result.message)
        else:
            self._mark_error("data_root", result.message)

    def _start_background_check(
        self,
        worker: FirstRunCheckWorker,
        finished_slot: Callable[[bool, str], None],
        clear_slot: Callable[[], None],
    ) -> tuple[QThread, FirstRunCheckWorker]:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(finished_slot)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(clear_slot)
        thread.start()
        return thread, worker

    def check_obs(self) -> None:
        if self.obs_check_running:
            return
        obs_config = self._current_obs_settings()
        self.pending_obs_config = obs_config
        self.obs_check_running = True
        self.state = mark_step_checking(self.state, "obs", OBS_CHECKING_MESSAGE)
        self.current_step = "obs"
        self.refresh()

        def run_obs_check() -> tuple[bool, str]:
            recorder = self.obs_recorder_factory(
                host=obs_config["websocket_host"],
                port=obs_config["websocket_port"],
                password=obs_config["websocket_password"],
            )
            check_recording_api = getattr(recorder, "check_recording_api", None)
            if callable(check_recording_api):
                check_recording_api()
            else:
                recorder.check_connection()
            return True, "OBS подключен."

        worker = FirstRunCheckWorker(
            run_obs_check,
            fallback_message=OBS_ERROR_MESSAGE,
            log_message="First-run OBS connection check failed.",
        )
        self.obs_check_thread, self.obs_check_worker = self._start_background_check(
            worker,
            self._on_obs_check_finished,
            self._clear_obs_check_thread,
        )

    @Slot(bool, str)
    def _on_obs_check_finished(self, ok: bool, message: str) -> None:
        self.obs_check_running = False
        if ok:
            if self.pending_obs_config is not None:
                self.config.setdefault("obs", {}).update(self.pending_obs_config)
                self.state.values["obs_websocket_host"] = self.pending_obs_config[
                    "websocket_host"
                ]
                self.state.values["obs_websocket_port"] = self.pending_obs_config[
                    "websocket_port"
                ]
                self.state.values["obs_password_configured"] = bool(
                    self.pending_obs_config["websocket_password"]
                )
            self._mark_ok("obs", message)
        else:
            self._mark_error("obs", message)
        self.pending_obs_config = None
        self._refresh_obs_check_button()

    def _clear_obs_check_thread(self) -> None:
        self.obs_check_thread = None
        self.obs_check_worker = None

    def check_audio(self) -> None:
        from app.services.readiness import _ffmpeg_status

        status = _ffmpeg_status()
        if status["state"] == "ok":
            self._mark_ok("audio", str(status["message"]))
        else:
            self._mark_error("audio", str(status["message"]))

    def check_aitunnel(self) -> None:
        if self.aitunnel_check_running:
            return
        key = self.aitunnel_key_input.text().strip()
        if not key:
            self._mark_error("aitunnel", AITUNNEL_EMPTY_KEY_MESSAGE)
            return

        self.aitunnel_check_running = True
        self.state = mark_step_checking(
            self.state,
            "aitunnel",
            AITUNNEL_CHECKING_MESSAGE,
        )
        self.current_step = "aitunnel"
        self.refresh()

        def run_aitunnel_check() -> tuple[bool, str]:
            result = check_aitunnel_key(
                key,
                self.config,
                client_factory=self.aitunnel_client_factory,
            )
            return result.ok, result.message

        worker = FirstRunCheckWorker(
            run_aitunnel_check,
            fallback_message=SUMMARY_ERROR_MESSAGE,
            log_message="First-run AI Tunnel key check failed.",
        )
        (
            self.aitunnel_check_thread,
            self.aitunnel_check_worker,
        ) = self._start_background_check(
            worker,
            self._on_aitunnel_check_finished,
            self._clear_aitunnel_check_thread,
        )

    @Slot(bool, str)
    def _on_aitunnel_check_finished(self, ok: bool, message: str) -> None:
        self.aitunnel_check_running = False
        if ok:
            secrets = self.config.setdefault("secrets", {})
            if not str(secrets.get("env_file") or "").strip():
                secrets["env_file"] = DEFAULT_ENV_FILE
            self.config.setdefault("summary", {})["api_key_env"] = AITUNNEL_API_KEY_ENV
            self.config.setdefault("transcription", {})["api_key_env"] = AITUNNEL_API_KEY_ENV
            self._mark_ok("aitunnel", message)
        else:
            self._mark_error("aitunnel", message)
        self._refresh_aitunnel_check_button()

    def _clear_aitunnel_check_thread(self) -> None:
        self.aitunnel_check_thread = None
        self.aitunnel_check_worker = None

    def _set_combo_value(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index < 0:
            index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _stored_transcription_backend(self) -> str:
        stored_backend = str(self.state.values.get("transcription_backend") or "").strip()
        if stored_backend:
            return stored_backend
        transcription = self.config.get("transcription")
        if isinstance(transcription, dict):
            backend = str(transcription.get("backend") or "").strip()
            if backend:
                return backend
        return "aitunnel"

    def _stored_transcription_model(self, backend: str) -> str:
        stored_backend = str(self.state.values.get("transcription_backend") or "").strip()
        stored_model = str(self.state.values.get("transcription_model") or "").strip()
        if stored_model and (not stored_backend or stored_backend == backend):
            return stored_model
        transcription = self.config.get("transcription")
        if isinstance(transcription, dict):
            backends = transcription.get("backends")
            backend_config = backends.get(backend) if isinstance(backends, dict) else None
            if isinstance(backend_config, dict):
                model = str(backend_config.get("model") or "").strip()
                if model:
                    return model
            model = str(transcription.get("model") or "").strip()
            if model:
                return model
        return ""

    def _stored_summary_model(self) -> str:
        stored_model = str(self.state.values.get("summary_model") or "").strip()
        if stored_model:
            return stored_model
        if not self.state.completed:
            return "gpt-5.4-nano"
        summary = self.config.get("summary")
        if isinstance(summary, dict):
            model = str(summary.get("model") or "").strip()
            if model:
                return model
        return "gpt-5.4-nano"

    def _refresh_transcription_model_options(self) -> None:
        backend = self.transcription_backend_select.currentData() or "aitunnel"
        current_model = self.transcription_model_select.currentData()
        options = transcription_model_options_for_backend(str(backend))
        values = {value for value, _label in options}
        selected_model = (
            current_model
            if isinstance(current_model, str) and current_model in values
            else self._stored_transcription_model(str(backend))
        )
        if selected_model not in values:
            selected_model = default_transcription_model_for_backend(str(backend))

        self.transcription_model_select.clear()
        for value, label in options:
            self.transcription_model_select.addItem(label, value)
        self._set_combo_value(self.transcription_model_select, str(selected_model))

    def check_transcription(self) -> None:
        backend = self.transcription_backend_select.currentData() or "aitunnel"
        model = self.transcription_model_select.currentData()
        if not isinstance(model, str) or not model:
            model = default_transcription_model_for_backend(str(backend))
        self.state.values["transcription_backend"] = backend
        self.state.values["transcription_model"] = model
        self.config.setdefault("transcription", {})["backend"] = backend
        self.config["transcription"]["model"] = model
        self.config["transcription"].setdefault("backends", {}).setdefault(backend, {})[
            "model"
        ] = model
        result = check_transcription_settings(self.config, self.state)
        if result.ok:
            self._mark_ok("transcription", result.message)
        else:
            self._mark_error("transcription", result.message)

    def check_summary(self) -> None:
        if self.summary_check_running:
            return
        if self.state.steps["aitunnel"].status != "ok":
            self._mark_error("summary", AITUNNEL_REQUIRED_MESSAGE)
            return
        model = self.summary_model_select.currentData() or "gpt-5.4-nano"
        model = str(model)
        self.config.setdefault("summary", {})["enabled"] = True
        self.config["summary"]["model"] = model
        self.config["summary"]["api_key_env"] = AITUNNEL_API_KEY_ENV
        self.config["summary"]["base_url"] = AITUNNEL_BASE_URL_DEFAULT
        self.state.values["summary_model"] = model

        self.summary_check_running = True
        self.state = mark_step_checking(self.state, "summary", SUMMARY_CHECKING_MESSAGE)
        self.current_step = "summary"
        self.refresh()

        def run_summary_check() -> tuple[bool, str]:
            result = check_summary_settings(
                self.config,
                self.state,
                client_factory=self.summary_client_factory,
            )
            return result.ok, result.message

        worker = FirstRunCheckWorker(
            run_summary_check,
            fallback_message=SUMMARY_ERROR_MESSAGE,
            log_message="First-run summary connection check failed.",
        )
        (
            self.summary_check_thread,
            self.summary_check_worker,
        ) = self._start_background_check(
            worker,
            self._on_summary_check_finished,
            self._clear_summary_check_thread,
        )

    @Slot(bool, str)
    def _on_summary_check_finished(self, ok: bool, message: str) -> None:
        self.summary_check_running = False
        if ok:
            self._mark_ok("summary", message)
        else:
            self._mark_error("summary", message)
        self._refresh_summary_check_button()

    def _clear_summary_check_thread(self) -> None:
        self.summary_check_thread = None
        self.summary_check_worker = None

    def finish_setup(self) -> None:
        if setup_completed(self.state):
            self.state = mark_setup_completed(self.state)
            self.config["setup"] = setup_config_from_state(self.state)
            self.completed.emit(dict(self.config))

    def _mark_ok(self, step_key: str, message: str) -> None:
        self.state = mark_step_ok(self.state, step_key, message)
        if step_key == "summary":
            self.state = mark_step_ok(self.state, "finish", "Все проверки готовы.")
        self.config["setup"] = setup_config_from_state(self.state)
        self.config_changed.emit(dict(self.config))
        self.current_step = self.state.current_step
        self.refresh()

    def _mark_error(self, step_key: str, message: str) -> None:
        self.state = mark_step_error(self.state, step_key, message)
        self.current_step = step_key
        self.refresh()

    def refresh(self) -> None:
        ready = sum(1 for step in FIRST_RUN_STEPS if self.state.steps[step].status == "ok")
        self.progress_label.setText(f"{ready} из {len(FIRST_RUN_STEPS)} готово")
        current = self.state.steps[self.current_step]
        self.current_step_title.setText(current.title)
        self.current_step_hint.setText(_step_description(self.current_step))
        self.current_step_status.setText(self._status_label(current.status))
        self.current_step_status.setProperty(
            "state", self._visual_state(self.current_step, current.status)
        )
        _repolish(self.current_step_status)
        for step_key in FIRST_RUN_STEPS:
            step = self.state.steps[step_key]
            enabled = step.status == "ok" or self._can_open(step_key)
            self.step_buttons[step_key].set_card_state(
                index=self.step_indexes[step_key],
                title=step.title,
                note=_step_description(step_key),
                status_text=self._step_card_status_text(
                    self._visual_state(step_key, step.status), step.status
                ),
                state=self._visual_state(step_key, step.status),
                active=step_key == self.current_step,
                enabled=enabled,
            )
            self.step_message_label[step_key].setText(step.message or "Требует проверки.")
        self.step_stack.setCurrentWidget(self.step_pages[self.current_step])
        if self.finish_button is not None:
            self.finish_button.setEnabled(setup_completed(self.state))
        self._refresh_obs_check_button()
        self._refresh_aitunnel_check_button()
        self._refresh_summary_check_button()

    def _refresh_obs_check_button(self) -> None:
        if self.obs_check_button is not None:
            self.obs_check_button.setEnabled(not self.obs_check_running)

    def _refresh_aitunnel_check_button(self) -> None:
        if self.aitunnel_check_button is not None:
            self.aitunnel_check_button.setEnabled(not self.aitunnel_check_running)

    def _refresh_summary_check_button(self) -> None:
        if self.summary_check_button is not None:
            self.summary_check_button.setEnabled(not self.summary_check_running)

    def _can_open(self, step_key: str) -> bool:
        index = FIRST_RUN_STEPS.index(step_key)
        return all(
            self.state.steps[FIRST_RUN_STEPS[item]].status == "ok"
            for item in range(index)
        )

    @staticmethod
    def _status_label(status: str) -> str:
        if status == "ok":
            return "Готово"
        if status == "locked":
            return "Заблокировано"
        if status == "checking":
            return OBS_CHECKING_MESSAGE
        if status == "error":
            return "Ошибка проверки"
        return "Требует действия"

    def _visual_state(self, step_key: str, status: str) -> str:
        if status == "ok":
            return "done"
        if status == "error":
            return "error"
        if step_key == self.current_step:
            return "active"
        if status == "locked" or not self._can_open(step_key):
            return "locked"
        return "todo"

    def _step_card_status_text(self, visual_state: str, status: str) -> str:
        if visual_state == "locked":
            return "\ue72e"
        if visual_state == "done":
            return "\ue73e"
        if visual_state == "error":
            return "\ue783"
        if visual_state in {"active", "todo"}:
            return ""
        return self._status_label(status)
