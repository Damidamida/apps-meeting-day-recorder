from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


OBS_UNAVAILABLE_MESSAGE = "OBS недоступен. Проверьте, что OBS запущен и WebSocket включен."


class RecorderError(RuntimeError):
    """A readable recording error that is safe to show in the UI."""


@dataclass(frozen=True)
class RecorderResult:
    metadata: dict[str, Any]
    message: str


class Recorder(Protocol):
    enabled: bool
    status_text: str

    def check_connection(self) -> str: ...

    def start_recording(self, meeting_folder: Path) -> RecorderResult: ...

    def stop_recording(self) -> RecorderResult: ...


class NoopRecorder:
    enabled = False
    status_text = "OBS: тестовый режим без записи"

    def check_connection(self) -> str:
        return self.status_text

    def start_recording(self, meeting_folder: Path) -> RecorderResult:
        del meeting_folder
        return RecorderResult(
            metadata={"recording_status": "disabled"},
            message="Запись OBS не используется. Встреча сохранена локально без записи.",
        )

    def stop_recording(self) -> RecorderResult:
        return RecorderResult(metadata={"recording_status": "disabled"}, message=self.status_text)


class ObsRecorder:
    enabled = True

    def __init__(
        self,
        host: str = "localhost",
        port: int = 4455,
        password: str = "",
        timeout: int = 3,
    ) -> None:
        self.host = host
        self.port = port
        self._password = password
        self.timeout = timeout
        self.status_text = "OBS: недоступен"
        self._client: Any | None = None

    def check_connection(self) -> str:
        try:
            client = self._new_client()
            client.get_version()
        except Exception as error:
            self.status_text = "OBS: недоступен"
            raise RecorderError(OBS_UNAVAILABLE_MESSAGE) from error
        self.status_text = "OBS: подключен"
        return self.status_text

    def start_recording(self, meeting_folder: Path) -> RecorderResult:
        del meeting_folder
        try:
            client = self._new_client()
            client.start_record()
        except Exception as error:
            self.status_text = "OBS: недоступен"
            raise RecorderError(OBS_UNAVAILABLE_MESSAGE) from error
        self._client = client
        self.status_text = "OBS: подключен"
        return RecorderResult(
            metadata={
                "recorder": "obs",
                "recording_status": "recording",
                "recording_started_at": datetime.now().isoformat(),
            },
            message="Запись OBS начата.",
        )

    def stop_recording(self) -> RecorderResult:
        try:
            client = self._client or self._new_client()
            response = client.stop_record()
        except Exception as error:
            self.status_text = "OBS: недоступен"
            raise RecorderError(
                "Не удалось остановить запись OBS. Проверьте OBS и сохраните запись вручную."
            ) from error
        self.status_text = "OBS: подключен"
        metadata: dict[str, Any] = {
            "recorder": "obs",
            "recording_status": "stopped",
            "recording_stopped_at": datetime.now().isoformat(),
        }
        recording_path = getattr(response, "output_path", None)
        if recording_path:
            metadata["recording_path"] = recording_path
        else:
            metadata["recording_note"] = "OBS не вернул путь к файлу записи."
        return RecorderResult(metadata=metadata, message="Запись OBS остановлена.")

    def _new_client(self) -> Any:
        try:
            import obsws_python as obs
        except ImportError as error:
            raise RecorderError(
                "Не установлен пакет obsws-python. Установите зависимости приложения."
            ) from error
        return obs.ReqClient(
            host=self.host,
            port=self.port,
            password=self._password,
            timeout=self.timeout,
        )


def create_recorder(config: dict[str, Any]) -> Recorder:
    return ObsRecorder(
        host=str(config.get("websocket_host", "localhost")),
        port=int(config.get("websocket_port", 4455)),
        password=str(config.get("websocket_password", "")),
    )
