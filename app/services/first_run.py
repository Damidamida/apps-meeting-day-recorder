import importlib.util
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from openai import AuthenticationError

CURRENT_SETUP_VERSION = 1
DEFAULT_ENV_FILE = ".env"
AITUNNEL_API_KEY_ENV = "AITUNNEL_KEY"
AITUNNEL_BASE_URL_DEFAULT = "https://api.aitunnel.ru/v1/"

FIRST_RUN_STEPS = (
    "data_root",
    "obs",
    "audio",
    "aitunnel",
    "transcription",
    "summary",
    "finish",
)
STEP_TITLES = {
    "data_root": "Папка данных",
    "obs": "OBS",
    "audio": "Аудио",
    "aitunnel": "AI Tunnel",
    "transcription": "Транскрипция",
    "summary": "AI-итоги",
    "finish": "Начать работу",
}
STEP_STATUSES = {"locked", "todo", "checking", "ok", "error"}
TRANSCRIPTION_OPTIONS = (
    ("aitunnel", "AI Tunnel STT"),
    ("faster_whisper", "faster-whisper"),
    ("whisper_cli", "Whisper CLI"),
)
TRANSCRIPTION_MODEL_OPTIONS = (
    ("whisper-large-v3-turbo", "Whisper Large V3 Turbo — 0.13 ₽/мин"),
    ("whisper-large-v3", "Whisper Large V3 — 0.36 ₽/мин"),
    ("whisper-1", "Whisper 1 — 1.15 ₽/мин"),
)
WHISPER_CLI_MODEL_OPTIONS = (
    ("tiny", "tiny"),
    ("base", "base"),
    ("small", "small"),
    ("medium", "medium"),
    ("large", "large"),
    ("turbo", "turbo"),
)
FASTER_WHISPER_MODEL_OPTIONS = (
    ("tiny", "tiny"),
    ("base", "base"),
    ("small", "small"),
    ("medium", "medium"),
    ("large-v3", "large-v3"),
    ("turbo", "turbo"),
)
TRANSCRIPTION_MODEL_OPTIONS_BY_BACKEND = {
    "aitunnel": TRANSCRIPTION_MODEL_OPTIONS,
    "faster_whisper": FASTER_WHISPER_MODEL_OPTIONS,
    "whisper_cli": WHISPER_CLI_MODEL_OPTIONS,
}
SUMMARY_MODEL_OPTIONS = (
    ("gpt-5.4-nano", "GPT 5.4 Nano — 38.4 ₽/1M вход · 240 ₽/1M выход"),
    ("gpt-5.4-mini", "GPT 5.4 Mini — 144 ₽/1M вход · 864 ₽/1M выход"),
    ("__custom__", "Другая модель AI Tunnel"),
)
AITUNNEL_REQUIRED_MESSAGE = "Сначала проверьте ключ AI Tunnel."


@dataclass(frozen=True)
class FirstRunCheckResult:
    ok: bool
    message: str
    details: str = ""


@dataclass(frozen=True)
class FirstRunStepState:
    key: str
    title: str
    status: str
    message: str

    def with_status(self, status: str, message: str = "") -> "FirstRunStepState":
        return FirstRunStepState(
            key=self.key,
            title=self.title,
            status=status if status in STEP_STATUSES else "todo",
            message=message,
        )


@dataclass
class FirstRunState:
    completed: bool
    version: int
    current_step: str
    steps: dict[str, FirstRunStepState]
    values: dict[str, Any]
    completed_at: str


def default_data_root() -> Path:
    return Path.home() / "Documents" / "BK Scribe"


def default_setup_config() -> dict[str, Any]:
    return {
        "completed": False,
        "version": CURRENT_SETUP_VERSION,
        "completed_at": "",
        "data_root_checked": False,
        "obs_checked": False,
        "audio_checked": False,
        "aitunnel_checked": False,
        "transcription_checked": False,
        "summary_checked": False,
        "current_step": "data_root",
        "steps": {
            step: {
                "status": "todo" if index == 0 else "locked",
                "message": "",
            }
            for index, step in enumerate(FIRST_RUN_STEPS)
        },
        "values": {
            "data_root": str(default_data_root()),
            "obs_websocket_host": "localhost",
            "obs_websocket_port": 4455,
            "obs_password_configured": False,
            "transcription_backend": "aitunnel",
            "transcription_model": "whisper-large-v3-turbo",
            "summary_model": "gpt-5.4-nano",
        },
    }


def transcription_model_options_for_backend(backend: str) -> tuple[tuple[str, str], ...]:
    return TRANSCRIPTION_MODEL_OPTIONS_BY_BACKEND.get(
        backend,
        WHISPER_CLI_MODEL_OPTIONS,
    )


def default_transcription_model_for_backend(backend: str) -> str:
    options = transcription_model_options_for_backend(backend)
    if any(value == "base" for value, _label in options):
        return "base"
    return options[0][0]


def _transcription_model_from_config(transcription: dict[str, Any], backend: str) -> str:
    backends = transcription.get("backends")
    backend_config = backends.get(backend) if isinstance(backends, dict) else None
    if isinstance(backend_config, dict) and str(backend_config.get("model") or "").strip():
        return str(backend_config["model"]).strip()
    model = str(transcription.get("model") or "").strip()
    return model or default_transcription_model_for_backend(backend)


def _transcription_model_is_supported(backend: str, model: str) -> bool:
    return model in {value for value, _label in transcription_model_options_for_backend(backend)}


def normalize_setup_config(value: Any) -> FirstRunState:
    if not isinstance(value, dict):
        value = {}
    default = default_setup_config()
    raw_steps = value.get("steps")
    if not isinstance(raw_steps, dict):
        raw_steps = {}
    steps: dict[str, FirstRunStepState] = {}
    previous_ok = True
    for index, key in enumerate(FIRST_RUN_STEPS):
        raw_step = raw_steps.get(key)
        if not isinstance(raw_step, dict):
            raw_step = {}
        checked_flag = value.get(f"{key}_checked")
        if key == "finish":
            checked_flag = bool(value.get("completed")) and all(
                bool(value.get(f"{item}_checked"))
                for item in FIRST_RUN_STEPS
                if item != "finish"
            )
        raw_status = str(raw_step.get("status") or "").strip()
        if checked_flag is True:
            status = "ok"
        else:
            status = raw_status
        if status not in STEP_STATUSES:
            status = "todo" if index == 0 else "locked"
        if not previous_ok and status != "ok":
            status = "locked"
        if previous_ok and status == "locked":
            status = "todo"
        message = str(raw_step.get("message") or "")
        if checked_flag is True and raw_status != "ok":
            message = "Готово"
        steps[key] = FirstRunStepState(key, STEP_TITLES[key], status, message)
        previous_ok = status == "ok"

    current_step = str(value.get("current_step") or default["current_step"])
    current_state = FirstRunState(False, CURRENT_SETUP_VERSION, current_step, steps, {}, "")
    if current_step not in FIRST_RUN_STEPS or not can_open_step(
        current_state,
        current_step,
    ) or steps[current_step].status == "ok":
        current_step = _first_available_step(steps)

    raw_values = value.get("values")
    values = dict(default["values"])
    if isinstance(raw_values, dict):
        values.update(raw_values)

    state = FirstRunState(
        completed=bool(value.get("completed", False)),
        version=_safe_setup_version(value.get("version")),
        current_step=current_step,
        steps=steps,
        values=values,
        completed_at=str(value.get("completed_at") or ""),
    )
    return state


def normalize_setup_config_dict(value: Any) -> dict[str, Any]:
    return setup_config_from_state(normalize_setup_config(value))


def setup_config_from_state(state: FirstRunState) -> dict[str, Any]:
    return {
        "completed": bool(state.completed),
        "version": int(state.version),
        "completed_at": state.completed_at,
        "data_root_checked": state.steps["data_root"].status == "ok",
        "obs_checked": state.steps["obs"].status == "ok",
        "audio_checked": state.steps["audio"].status == "ok",
        "aitunnel_checked": state.steps["aitunnel"].status == "ok",
        "transcription_checked": state.steps["transcription"].status == "ok",
        "summary_checked": state.steps["summary"].status == "ok",
        "current_step": state.current_step,
        "steps": {
            key: {"status": step.status, "message": step.message}
            for key, step in state.steps.items()
        },
        "values": dict(state.values),
    }


def can_open_step(state: FirstRunState, step_key: str) -> bool:
    if step_key not in FIRST_RUN_STEPS:
        return False
    target_index = FIRST_RUN_STEPS.index(step_key)
    return all(
        state.steps[FIRST_RUN_STEPS[index]].status == "ok"
        for index in range(target_index)
    )


def mark_step_ok(state: FirstRunState, step_key: str, message: str) -> FirstRunState:
    return _mark_step(state, step_key, "ok", message)


def mark_step_error(state: FirstRunState, step_key: str, message: str) -> FirstRunState:
    return _mark_step(state, step_key, "error", message)


def mark_step_checking(state: FirstRunState, step_key: str, message: str) -> FirstRunState:
    return _mark_step(state, step_key, "checking", message)


def reset_from_step(state: FirstRunState, step_key: str) -> FirstRunState:
    if step_key not in FIRST_RUN_STEPS:
        return state
    steps = dict(state.steps)
    start = FIRST_RUN_STEPS.index(step_key)
    for index, key in enumerate(FIRST_RUN_STEPS[start:], start=start):
        status = "todo" if index == start else "locked"
        steps[key] = steps[key].with_status(status, "")
    return FirstRunState(
        completed=False,
        version=state.version,
        current_step=step_key,
        steps=steps,
        values=dict(state.values),
        completed_at="",
    )


def setup_completed(state: FirstRunState) -> bool:
    return all(state.steps[key].status == "ok" for key in FIRST_RUN_STEPS)


def should_show_wizard_on_startup(state: FirstRunState) -> bool:
    return state.completed is not True or state.version < CURRENT_SETUP_VERSION


def mark_setup_completed(state: FirstRunState) -> FirstRunState:
    if not setup_completed(state):
        return state
    return FirstRunState(
        completed=True,
        version=CURRENT_SETUP_VERSION,
        current_step="finish",
        steps=dict(state.steps),
        values=dict(state.values),
        completed_at=datetime.now().isoformat(timespec="seconds"),
    )


def validate_data_root(path: Path) -> FirstRunCheckResult:
    expanded = path.expanduser()
    if expanded.exists() and not expanded.is_dir():
        return FirstRunCheckResult(False, "Выбранный путь указывает на файл, а не на папку.")
    try:
        expanded.mkdir(parents=True, exist_ok=True)
        probe = expanded / ".bk_scribe_setup_check_write"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return FirstRunCheckResult(False, "Не удалось записать проверочный файл в папку данных.")
    return FirstRunCheckResult(True, "Папка данных готова.")


def resolve_first_run_env_file(config: dict[str, Any]) -> Path:
    secrets = config.get("secrets") if isinstance(config.get("secrets"), dict) else {}
    summary = config.get("summary") if isinstance(config.get("summary"), dict) else {}
    transcription = (
        config.get("transcription") if isinstance(config.get("transcription"), dict) else {}
    )
    raw_path = (
        secrets.get("env_file")
        or summary.get("env_file")
        or transcription.get("env_file")
        or DEFAULT_ENV_FILE
    )
    return Path(str(raw_path)).expanduser()


def _quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_env_secret(env_file: Path, name: str, value: str) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    written = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            existing_name = stripped.split("=", 1)[0].strip()
            if existing_name == name:
                output.append(f"{name}={_quote_env_value(value)}")
                written = True
                continue
        output.append(line)
    if not written:
        output.append(f"{name}={_quote_env_value(value)}")
    env_file.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def check_aitunnel_key(
    key: str,
    config: dict[str, Any],
    client_factory: Callable[..., Any] | None = None,
) -> FirstRunCheckResult:
    key = key.strip()
    if not key:
        return FirstRunCheckResult(False, "Введите AI Tunnel key.")
    client_factory = client_factory or _default_openai_client_factory
    try:
        client = client_factory(
            api_key=key,
            base_url=AITUNNEL_BASE_URL_DEFAULT,
            timeout=20,
        )
        client.models.list()
    except AuthenticationError:
        return FirstRunCheckResult(False, "Ключ не подошел.")
    except Exception:
        return FirstRunCheckResult(False, "Сервис временно недоступен.")

    write_env_secret(resolve_first_run_env_file(config), AITUNNEL_API_KEY_ENV, key)
    return FirstRunCheckResult(True, "Ключ AI Tunnel проверен.")


def check_transcription_settings(
    config: dict[str, Any],
    setup_state: FirstRunState,
) -> FirstRunCheckResult:
    transcription = config.get("transcription")
    transcription = transcription if isinstance(transcription, dict) else {}
    backend = str(transcription.get("backend") or "aitunnel")
    model = _transcription_model_from_config(transcription, backend)
    if backend == "aitunnel":
        if setup_state.steps["aitunnel"].status != "ok":
            return FirstRunCheckResult(False, AITUNNEL_REQUIRED_MESSAGE)
        if not _transcription_model_is_supported(backend, model):
            return FirstRunCheckResult(False, "Выберите модель транскрипции из списка.")
        return FirstRunCheckResult(True, "Транскрипция AI Tunnel STT готова.")
    if not _transcription_model_is_supported(backend, model):
        return FirstRunCheckResult(False, "Выберите модель транскрипции из списка.")
    if backend == "faster_whisper":
        if importlib.util.find_spec("faster_whisper") is None:
            return FirstRunCheckResult(False, "faster-whisper не установлен.")
        return FirstRunCheckResult(True, "faster-whisper готов.")
    command = str(transcription.get("whisper_command") or "whisper")
    if shutil.which(command) is None:
        return FirstRunCheckResult(False, "Whisper CLI не найден.")
    return FirstRunCheckResult(True, "Whisper CLI готов.")


def check_summary_settings(
    config: dict[str, Any],
    setup_state: FirstRunState,
    client_factory: Callable[..., Any] | None = None,
) -> FirstRunCheckResult:
    from app.services.summarization import smoke_test_summary_connection

    if setup_state.steps["aitunnel"].status != "ok":
        return FirstRunCheckResult(False, AITUNNEL_REQUIRED_MESSAGE)
    summary = config.get("summary")
    summary = dict(summary) if isinstance(summary, dict) else {}
    summary["enabled"] = True
    if not str(summary.get("env_file") or "").strip():
        summary["env_file"] = str(config.get("secrets", {}).get("env_file") or "")
    return smoke_test_summary_connection(summary, client_factory=client_factory)


def _mark_step(
    state: FirstRunState,
    step_key: str,
    status: str,
    message: str,
) -> FirstRunState:
    if step_key not in FIRST_RUN_STEPS:
        return state
    steps = dict(state.steps)
    steps[step_key] = steps[step_key].with_status(status, message)
    if status != "ok":
        start = FIRST_RUN_STEPS.index(step_key) + 1
        for key in FIRST_RUN_STEPS[start:]:
            steps[key] = steps[key].with_status("locked", "")
        return FirstRunState(
            completed=False,
            version=state.version,
            current_step=step_key,
            steps=steps,
            values=dict(state.values),
            completed_at="",
        )
    next_index = min(FIRST_RUN_STEPS.index(step_key) + 1, len(FIRST_RUN_STEPS) - 1)
    next_key = FIRST_RUN_STEPS[next_index]
    if steps[next_key].status == "locked":
        steps[next_key] = steps[next_key].with_status("todo", "")
    return FirstRunState(
        completed=False,
        version=state.version,
        current_step=next_key,
        steps=steps,
        values=dict(state.values),
        completed_at="",
    )


def _first_available_step(steps: dict[str, FirstRunStepState]) -> str:
    for key in FIRST_RUN_STEPS:
        if steps[key].status != "ok" and can_open_step(
            FirstRunState(False, CURRENT_SETUP_VERSION, key, steps, {}, ""),
            key,
        ):
            return key
    return "finish"


def _safe_setup_version(value: Any) -> int:
    try:
        version = int(value)
    except (TypeError, ValueError):
        return CURRENT_SETUP_VERSION
    return max(0, version)


def _default_openai_client_factory(**kwargs: Any) -> Any:
    from openai import OpenAI

    return OpenAI(**kwargs)
