import logging
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.first_run import (
    AITUNNEL_API_KEY_ENV,
    AITUNNEL_BASE_URL_DEFAULT,
    DEFAULT_ENV_FILE,
    FIRST_RUN_STEPS,
    SUMMARY_MODEL_OPTIONS,
    TRANSCRIPTION_MODEL_OPTIONS,
    TRANSCRIPTION_OPTIONS,
    FirstRunState,
    check_aitunnel_key,
    check_summary_settings,
    check_transcription_settings,
    mark_setup_completed,
    mark_step_error,
    mark_step_ok,
    normalize_setup_config,
    setup_config_from_state,
    setup_completed,
    validate_data_root,
)
from app.services.recorder import RecorderError


logger = logging.getLogger(__name__)


class FirstRunWizard(QWidget):
    config_changed = Signal(dict)
    completed = Signal(dict)

    def __init__(
        self,
        config: dict[str, Any],
        state: FirstRunState | dict[str, Any],
        recorder: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.state = (
            state if isinstance(state, FirstRunState) else normalize_setup_config(state)
        )
        self.recorder = recorder
        self.step_buttons: dict[str, QPushButton] = {}
        self.step_status_labels: dict[str, QLabel] = {}
        self.step_message_label: dict[str, QLabel] = {}
        self.step_pages: dict[str, QWidget] = {}
        self.current_step = self.state.current_step
        self.setObjectName("firstRunWizard")
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        title = QLabel("Настройка BK Scribe")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "Пройдите обязательные проверки перед первым рабочим днем. "
            "Настройки сохраняются локально только после успешной проверки шага."
        )
        subtitle.setObjectName("sectionHint")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("inlineStatus")
        root.addWidget(self.progress_label)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.step_list_panel = QFrame()
        self.step_list_panel.setObjectName("firstRunStepList")
        self.step_list_panel.setMinimumHeight(520)
        step_list_layout = QVBoxLayout()
        step_list_layout.setContentsMargins(10, 10, 10, 10)
        step_list_layout.setSpacing(6)
        for index, step_key in enumerate(FIRST_RUN_STEPS, start=1):
            button = QPushButton()
            button.setObjectName("firstRunStepButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda checked, key=step_key: self.open_step(key)
            )
            status = QLabel("")
            status.setObjectName("firstRunStepStatus")
            row = QVBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            title_row = QLabel(f"{index}. {self.state.steps[step_key].title}")
            title_row.setObjectName("firstRunStepTitle")
            button_layout = QVBoxLayout()
            button_layout.setContentsMargins(8, 6, 8, 6)
            button_layout.addWidget(title_row)
            button_layout.addWidget(status)
            button.setLayout(button_layout)
            row.addWidget(button)
            step_list_layout.addWidget(button)
            self.step_buttons[step_key] = button
            self.step_status_labels[step_key] = status
        step_list_layout.addStretch(1)
        self.step_list_panel.setLayout(step_list_layout)
        body.addWidget(self.step_list_panel, 0)

        self.step_content_panel = QFrame()
        self.step_content_panel.setObjectName("firstRunStepContent")
        self.step_content_panel.setMinimumHeight(520)
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(14, 14, 14, 14)
        content_layout.setSpacing(12)
        self.step_stack = QStackedWidget()
        for step_key in FIRST_RUN_STEPS:
            page = self._create_step_page(step_key)
            self.step_pages[step_key] = page
            self.step_stack.addWidget(page)
        content_layout.addWidget(self.step_stack, 1)

        footer = QHBoxLayout()
        self.back_button = QPushButton("Назад")
        self.back_button.clicked.connect(self.go_back)
        self.next_button = QPushButton("Далее")
        self.next_button.setObjectName("primaryButton")
        self.next_button.clicked.connect(self.go_next)
        self.finish_button = QPushButton("Начать работу")
        self.finish_button.setObjectName("primaryButton")
        self.finish_button.clicked.connect(self.finish_setup)
        footer.addWidget(self.back_button)
        footer.addStretch(1)
        footer.addWidget(self.next_button)
        footer.addWidget(self.finish_button)
        content_layout.addLayout(footer)
        self.step_content_panel.setLayout(content_layout)
        body.addWidget(self.step_content_panel, 1)
        root.addLayout(body, 1)
        self.setLayout(root)

    def _create_step_page(self, step_key: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel(self.state.steps[step_key].title)
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.step_message_label[step_key] = QLabel("")
        self.step_message_label[step_key].setWordWrap(True)
        self.step_message_label[step_key].setObjectName("inlineStatus")
        layout.addWidget(self.step_message_label[step_key])

        if step_key == "data_root":
            self.data_root_input = QLineEdit(
                str(self.state.values.get("data_root") or "")
            )
            browse = QPushButton("Выбрать папку")
            browse.clicked.connect(self.choose_data_root)
            check = QPushButton("Проверить папку данных")
            check.setObjectName("primaryButton")
            check.clicked.connect(self.check_data_root)
            layout.addWidget(QLabel("Папка для рабочих дней, записей, transcript и итогов."))
            layout.addWidget(self.data_root_input)
            layout.addWidget(browse)
            layout.addWidget(check)
        elif step_key == "obs":
            check = QPushButton("Проверить OBS")
            check.setObjectName("primaryButton")
            check.clicked.connect(self.check_obs)
            layout.addWidget(
                QLabel("Запустите OBS и включите WebSocket. Автонастройка OBS не выполняется.")
            )
            layout.addWidget(check)
        elif step_key == "audio":
            check = QPushButton("Проверить аудио")
            check.setObjectName("primaryButton")
            check.clicked.connect(self.check_audio)
            layout.addWidget(QLabel("Проверяем доступность FFmpeg для извлечения audio.wav."))
            layout.addWidget(check)
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
            check.clicked.connect(self.check_aitunnel)
            layout.addWidget(QLabel("Один ключ используется для AI Tunnel STT и AI-итогов."))
            layout.addWidget(self.aitunnel_key_input)
            layout.addWidget(self.aitunnel_link)
            layout.addWidget(check)
        elif step_key == "transcription":
            self.transcription_backend_select = QComboBox()
            for value, label in TRANSCRIPTION_OPTIONS:
                self.transcription_backend_select.addItem(label, value)
            self.transcription_model_select = QComboBox()
            for value, label in TRANSCRIPTION_MODEL_OPTIONS:
                self.transcription_model_select.addItem(label, value)
            check = QPushButton("Проверить транскрипцию")
            check.setObjectName("primaryButton")
            check.clicked.connect(self.check_transcription)
            layout.addWidget(QLabel("Проверка не создает встречу и не загружает реальное аудио."))
            layout.addWidget(self.transcription_backend_select)
            layout.addWidget(self.transcription_model_select)
            layout.addWidget(check)
        elif step_key == "summary":
            self.summary_page = page
            self.summary_model_select = QComboBox()
            for value, label in SUMMARY_MODEL_OPTIONS:
                self.summary_model_select.addItem(label, value)
            check = QPushButton("Проверить AI-итоги")
            check.setObjectName("primaryButton")
            check.clicked.connect(self.check_summary)
            layout.addWidget(QLabel("AI-итоги используют ключ из шага AI Tunnel."))
            layout.addWidget(self.summary_model_select)
            layout.addWidget(check)
        else:
            layout.addWidget(QLabel("Все обязательные проверки должны быть готовы."))
        layout.addStretch(1)
        page.setLayout(layout)
        if step_key != "summary" and not hasattr(self, "summary_page"):
            self.summary_page = QWidget()
        return page

    def choose_data_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выбрать папку данных")
        if folder:
            self.data_root_input.setText(folder)

    def open_step(self, step_key: str) -> None:
        if not self.step_buttons[step_key].isEnabled():
            return
        self.current_step = step_key
        self.refresh()

    def go_back(self) -> None:
        index = FIRST_RUN_STEPS.index(self.current_step)
        if index > 0:
            self.open_step(FIRST_RUN_STEPS[index - 1])

    def go_next(self) -> None:
        if self.state.steps[self.current_step].status != "ok":
            return
        index = FIRST_RUN_STEPS.index(self.current_step)
        if index < len(FIRST_RUN_STEPS) - 1:
            self.open_step(FIRST_RUN_STEPS[index + 1])

    def check_data_root(self) -> None:
        path = Path(self.data_root_input.text().strip())
        result = validate_data_root(path)
        if result.ok:
            self.config.setdefault("storage", {})["root"] = str(path.expanduser())
            self.state.values["data_root"] = str(path.expanduser())
            self._mark_ok("data_root", result.message)
        else:
            self._mark_error("data_root", result.message)

    def check_obs(self) -> None:
        if self.recorder is None or not getattr(self.recorder, "enabled", False):
            self._mark_ok("obs", "OBS проверен в тестовом режиме.")
            return
        try:
            self.recorder.check_connection()
        except RecorderError:
            self._mark_error("obs", "OBS не подключен. Запустите OBS и проверьте WebSocket.")
            return
        except Exception:
            logger.exception("First-run OBS connection check failed.")
            self._mark_error("obs", "OBS не подключен. Запустите OBS и проверьте WebSocket.")
            return
        self._mark_ok("obs", "OBS подключен.")

    def check_audio(self) -> None:
        from app.services.readiness import _ffmpeg_status

        status = _ffmpeg_status()
        if status["state"] == "ok":
            self._mark_ok("audio", str(status["message"]))
        else:
            self._mark_error("audio", str(status["message"]))

    def check_aitunnel(self) -> None:
        result = check_aitunnel_key(self.aitunnel_key_input.text(), self.config)
        if result.ok:
            self.config.setdefault("secrets", {}).setdefault("env_file", DEFAULT_ENV_FILE)
            self.config.setdefault("summary", {})["api_key_env"] = AITUNNEL_API_KEY_ENV
            self.config.setdefault("transcription", {})["api_key_env"] = AITUNNEL_API_KEY_ENV
            self._mark_ok("aitunnel", result.message)
        else:
            self._mark_error("aitunnel", result.message)

    def check_transcription(self) -> None:
        backend = self.transcription_backend_select.currentData() or "aitunnel"
        model = self.transcription_model_select.currentData() or "whisper-large-v3-turbo"
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
        model = self.summary_model_select.currentData() or "gpt-5.4-nano"
        if model == "__custom__":
            model = "gpt-5.4-mini"
        self.config.setdefault("summary", {})["enabled"] = True
        self.config["summary"]["model"] = model
        self.config["summary"]["api_key_env"] = AITUNNEL_API_KEY_ENV
        self.config["summary"]["base_url"] = AITUNNEL_BASE_URL_DEFAULT
        result = check_summary_settings(self.config, self.state)
        if result.ok:
            self._mark_ok("summary", result.message)
        else:
            self._mark_error("summary", result.message)

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
        for step_key in FIRST_RUN_STEPS:
            step = self.state.steps[step_key]
            enabled = step.status == "ok" or self._can_open(step_key)
            self.step_buttons[step_key].setEnabled(enabled)
            self.step_buttons[step_key].setChecked(step_key == self.current_step)
            self.step_status_labels[step_key].setText(self._status_label(step.status))
            self.step_message_label[step_key].setText(step.message or "Требует проверки.")
        self.step_stack.setCurrentWidget(self.step_pages[self.current_step])
        index = FIRST_RUN_STEPS.index(self.current_step)
        self.back_button.setEnabled(index > 0)
        self.next_button.setEnabled(self.state.steps[self.current_step].status == "ok")
        self.finish_button.setVisible(self.current_step == "finish")
        self.finish_button.setEnabled(setup_completed(self.state))

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
        if status == "error":
            return "Ошибка проверки"
        return "Требует действия"
