import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.services.recorder import NoopRecorder, Recorder, RecorderError


UNSAFE_FOLDER_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")
REPEATED_UNDERSCORES = re.compile(r"_+")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_folder_name(title: str, fallback: str = "meeting") -> str:
    safe_title = UNSAFE_FOLDER_CHARACTERS.sub("_", title.strip())
    safe_title = WHITESPACE.sub("_", safe_title)
    safe_title = REPEATED_UNDERSCORES.sub("_", safe_title).strip(" ._")
    if not safe_title or safe_title.upper() in WINDOWS_RESERVED_NAMES:
        return fallback
    return safe_title


class StorageService:
    def __init__(self, root: Path, recorder: Recorder | None = None) -> None:
        self.root = Path(root)
        self.recorder = recorder or NoopRecorder()
        self.active_day_folder: Path | None = None
        self.active_meeting_folder: Path | None = None
        self.last_workday_action: str | None = None
        self.last_recorder_message: str | None = None

    def create_day_folder(self, workday: date | None = None) -> Path:
        workday = workday or date.today()
        day_folder = self.root / workday.isoformat()
        day_folder.mkdir(parents=True, exist_ok=True)
        return day_folder

    @property
    def workday_active(self) -> bool:
        return self.active_day_folder is not None

    @property
    def meeting_active(self) -> bool:
        return self.active_meeting_folder is not None

    def load_today_state(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        self.active_day_folder = None
        self.active_meeting_folder = None
        self.last_workday_action = None

        day_folder = self.root / now.date().isoformat()
        metadata_path = day_folder / "day_metadata.json"
        if not metadata_path.exists():
            return

        metadata = self._read_json(metadata_path)
        if metadata.get("status") != "active":
            return

        self.active_day_folder = day_folder
        active_meetings = sorted(
            meeting_folder
            for meeting_folder in day_folder.iterdir()
            if meeting_folder.is_dir()
            and self._meeting_has_status(meeting_folder, "active")
        )
        if active_meetings:
            self.active_meeting_folder = active_meetings[-1]

    def start_workday(self, started_at: datetime | None = None) -> Path:
        if self.workday_active:
            raise ValueError("Рабочий день уже активен.")

        started_at = started_at or datetime.now()
        day_folder = self.create_day_folder(started_at.date())
        metadata_path = day_folder / "day_metadata.json"
        if metadata_path.exists():
            metadata = self._read_json(metadata_path)
            if metadata.get("status") == "ended":
                events = metadata.setdefault("events", [])
                ended_at = metadata.get("ended_at")
                ended_event = {"type": "ended", "at": ended_at}
                if ended_at and ended_event not in events:
                    events.append(ended_event)
                events.append(
                    {"type": "reopened", "at": started_at.isoformat()}
                )
                metadata.update({"ended_at": None, "status": "active"})
                self._write_json(metadata_path, metadata)
                self.active_day_folder = day_folder
                self.last_workday_action = "reopened"
                return day_folder
            raise ValueError(f"Метаданные рабочего дня уже существуют: {metadata_path}")
        self._write_json(
            metadata_path,
            {
                "date": started_at.date().isoformat(),
                "started_at": started_at.isoformat(),
                "status": "active",
                "meetings": [],
                "events": [{"type": "started", "at": started_at.isoformat()}],
            },
        )
        self.active_day_folder = day_folder
        self.last_workday_action = "started"
        return day_folder

    def start_meeting(self, title: str, started_at: datetime | None = None) -> Path:
        if not self.workday_active:
            raise ValueError("Сначала начните рабочий день.")
        if self.meeting_active:
            raise ValueError("Встреча уже активна.")
        if not title.strip():
            raise ValueError("Название встречи не может быть пустым.")

        started_at = started_at or datetime.now()
        meeting_folder = self._create_unique_meeting_folder(title, started_at)
        self.write_metadata(
            meeting_folder,
            {
                "title": title.strip(),
                "started_at": started_at.isoformat(),
                "status": "active",
            },
        )
        self.active_meeting_folder = meeting_folder
        self._start_recording(meeting_folder)
        return meeting_folder

    def end_meeting(self, ended_at: datetime | None = None) -> Path:
        if not self.meeting_active:
            raise ValueError("Нет активной встречи для завершения.")

        ended_at = ended_at or datetime.now()
        meeting_folder = self.active_meeting_folder
        metadata = self._read_json(meeting_folder / "meeting_metadata.json")
        if metadata.get("recording_status") == "recording":
            metadata.update(self._stop_recording())
        started_at = datetime.fromisoformat(metadata["started_at"])
        metadata.update(
            {
                "ended_at": ended_at.isoformat(),
                "duration_seconds": max(0, int((ended_at - started_at).total_seconds())),
                "status": "ended",
            }
        )
        self.write_metadata(meeting_folder, metadata)
        self.write_placeholder_transcript(meeting_folder)
        self.write_placeholder_transcript_json(meeting_folder)
        self.write_placeholder_summary(meeting_folder)

        day_metadata_path = self.active_day_folder / "day_metadata.json"
        day_metadata = self._read_json(day_metadata_path)
        day_metadata["meetings"].append(
            {
                "folder": meeting_folder.name,
                **metadata,
            }
        )
        self._write_json(day_metadata_path, day_metadata)
        self.active_meeting_folder = None
        return meeting_folder

    def _start_recording(self, meeting_folder: Path) -> None:
        metadata = self._read_json(meeting_folder / "meeting_metadata.json")
        try:
            result = self.recorder.start_recording(meeting_folder)
            metadata.update(result.metadata)
            self.last_recorder_message = result.message
        except RecorderError as error:
            metadata.update(
                {
                    "recorder": "obs",
                    "recording_status": "start_failed",
                    "recording_note": str(error),
                }
            )
            self.last_recorder_message = str(error)
        self.write_metadata(meeting_folder, metadata)

    def _stop_recording(self) -> dict[str, Any]:
        try:
            result = self.recorder.stop_recording()
            self.last_recorder_message = result.message
            return result.metadata
        except RecorderError as error:
            self.last_recorder_message = str(error)
            return {
                "recording_status": "stop_failed",
                "recording_note": str(error),
            }

    def end_workday(self, ended_at: datetime | None = None) -> Path:
        if not self.workday_active:
            raise ValueError("Нет активного рабочего дня для завершения.")
        if self.meeting_active:
            raise ValueError("Завершите активную встречу перед завершением рабочего дня.")

        ended_at = ended_at or datetime.now()
        day_folder = self.active_day_folder
        metadata_path = day_folder / "day_metadata.json"
        metadata = self._read_json(metadata_path)
        metadata.update(
            {
                "ended_at": ended_at.isoformat(),
                "status": "ended",
            }
        )
        metadata.setdefault("events", []).append(
            {"type": "ended", "at": ended_at.isoformat()}
        )
        self._write_json(metadata_path, metadata)
        self._read_or_create_text(
            day_folder / "00_day_summary_draft.md",
            self._day_summary_placeholder(),
        )
        self._read_or_create_text(
            day_folder / "00_tasks_draft.md",
            self._tasks_placeholder(),
        )
        self.active_day_folder = None
        return day_folder

    def create_meeting_folder(
        self,
        title: str,
        started_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        started_at = started_at or datetime.now()
        day_folder = self.create_day_folder(started_at.date())
        meeting_folder = day_folder / f"{started_at:%H-%M}_{safe_folder_name(title)}"
        meeting_folder.mkdir(parents=True, exist_ok=True)

        meeting_metadata = {
            "title": title,
            "started_at": started_at.isoformat(),
            **(metadata or {}),
        }
        self.write_metadata(meeting_folder, meeting_metadata)
        self.write_placeholder_transcript(meeting_folder)
        self.write_placeholder_transcript_json(meeting_folder)
        self.write_placeholder_summary(meeting_folder)
        return meeting_folder

    def write_metadata(self, meeting_folder: Path, metadata: dict[str, Any]) -> Path:
        path = meeting_folder / "meeting_metadata.json"
        self._write_json(path, metadata)
        return path

    def write_placeholder_transcript(self, meeting_folder: Path) -> Path:
        path = meeting_folder / "transcript.md"
        self._read_or_create_text(path, "# Транскрипт\n\n_Транскрипция пока не реализована._\n")
        return path

    def write_placeholder_transcript_json(self, meeting_folder: Path) -> Path:
        path = meeting_folder / "transcript.json"
        self._write_json(path, {"status": "placeholder", "segments": []})
        return path

    def write_placeholder_summary(self, meeting_folder: Path) -> Path:
        path = meeting_folder / "summary_draft.md"
        self._read_or_create_text(path, self._meeting_summary_placeholder())
        return path

    def get_today_day_folder(self, now: datetime | None = None) -> Path | None:
        now = now or datetime.now()
        day_folder = self.root / now.date().isoformat()
        return day_folder if day_folder.is_dir() else None

    def list_today_meeting_folders(self, now: datetime | None = None) -> list[Path]:
        day_folder = self.get_today_day_folder(now)
        if day_folder is None:
            return []
        return sorted(
            folder
            for folder in day_folder.iterdir()
            if folder.is_dir() and (folder / "meeting_metadata.json").is_file()
        )

    def read_meeting_metadata(self, meeting_folder: Path) -> dict[str, Any]:
        path = meeting_folder / "meeting_metadata.json"
        return self._read_json(path) if path.exists() else {}

    def read_meeting_summary_draft(self, meeting_folder: Path) -> str:
        return self._read_or_create_text(
            meeting_folder / "summary_draft.md",
            self._meeting_summary_placeholder(),
        )

    def save_meeting_summary_draft(self, meeting_folder: Path, content: str) -> Path:
        return self._write_text(meeting_folder / "summary_draft.md", content)

    def read_day_summary_draft(self, day_folder: Path) -> str:
        return self._read_or_create_text(
            day_folder / "00_day_summary_draft.md",
            self._day_summary_placeholder(),
        )

    def save_day_summary_draft(self, day_folder: Path, content: str) -> Path:
        return self._write_text(day_folder / "00_day_summary_draft.md", content)

    def read_tasks_draft(self, day_folder: Path) -> str:
        return self._read_or_create_text(
            day_folder / "00_tasks_draft.md",
            self._tasks_placeholder(),
        )

    def save_tasks_draft(self, day_folder: Path, content: str) -> Path:
        return self._write_text(day_folder / "00_tasks_draft.md", content)

    def save_final_files(
        self,
        meeting_folder: Path,
        meeting_summary: str,
        day_summary: str,
        tasks: str,
    ) -> tuple[Path, Path, Path]:
        day_folder = meeting_folder.parent
        return (
            self._write_text(meeting_folder / "summary_final.md", meeting_summary),
            self._write_text(day_folder / "00_day_summary_final.md", day_summary),
            self._write_text(day_folder / "00_tasks_final.md", tasks),
        )

    def _create_unique_meeting_folder(self, title: str, started_at: datetime) -> Path:
        meeting_name = f"{started_at:%H-%M}_{safe_folder_name(title)}"
        meeting_folder = self.active_day_folder / meeting_name
        suffix = 2
        while meeting_folder.exists():
            meeting_folder = self.active_day_folder / f"{meeting_name}_{suffix}"
            suffix += 1
        meeting_folder.mkdir(parents=True)
        return meeting_folder

    @classmethod
    def _meeting_has_status(cls, meeting_folder: Path, status: str) -> bool:
        metadata_path = meeting_folder / "meeting_metadata.json"
        if not metadata_path.exists():
            return False
        return cls._read_json(metadata_path).get("status") == status

    @staticmethod
    def _meeting_summary_placeholder() -> str:
        return "# Черновик итогов встречи\n\n_Итоги встречи пока не заполнены._\n"

    @staticmethod
    def _day_summary_placeholder() -> str:
        return "# Черновик итогов дня\n\n_Итоги дня пока не заполнены._\n"

    @staticmethod
    def _tasks_placeholder() -> str:
        return "# Черновик задач\n\n_Задачи пока не заполнены._\n"

    @staticmethod
    def _read_or_create_text(path: Path, placeholder: str) -> str:
        if not path.exists():
            path.write_text(placeholder, encoding="utf-8")
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _write_text(path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, content: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(content, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

