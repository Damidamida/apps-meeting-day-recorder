import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any


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
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.active_day_folder: Path | None = None
        self.active_meeting_folder: Path | None = None

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
            raise ValueError("A workday is already active.")

        started_at = started_at or datetime.now()
        day_folder = self.create_day_folder(started_at.date())
        metadata_path = day_folder / "day_metadata.json"
        if metadata_path.exists():
            metadata = self._read_json(metadata_path)
            if metadata.get("status") == "ended":
                raise ValueError("Today's workday is already ended.")
            raise ValueError(f"Workday metadata already exists: {metadata_path}")
        self._write_json(
            metadata_path,
            {
                "date": started_at.date().isoformat(),
                "started_at": started_at.isoformat(),
                "status": "active",
                "meetings": [],
            },
        )
        self.active_day_folder = day_folder
        return day_folder

    def start_meeting(self, title: str, started_at: datetime | None = None) -> Path:
        if not self.workday_active:
            raise ValueError("Start a workday before starting a meeting.")
        if self.meeting_active:
            raise ValueError("A meeting is already active.")
        if not title.strip():
            raise ValueError("Meeting title cannot be empty.")

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
        return meeting_folder

    def end_meeting(self, ended_at: datetime | None = None) -> Path:
        if not self.meeting_active:
            raise ValueError("There is no active meeting to end.")

        ended_at = ended_at or datetime.now()
        meeting_folder = self.active_meeting_folder
        metadata = self._read_json(meeting_folder / "meeting_metadata.json")
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

    def end_workday(self, ended_at: datetime | None = None) -> Path:
        if not self.workday_active:
            raise ValueError("There is no active workday to end.")
        if self.meeting_active:
            raise ValueError("End the active meeting before ending the workday.")

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
        self._write_json(metadata_path, metadata)
        (day_folder / "00_day_summary_draft.md").write_text(
            "# Day summary draft\n\n_Summary generation is not implemented yet._\n",
            encoding="utf-8",
        )
        (day_folder / "00_tasks_draft.md").write_text(
            "# Tasks draft\n\n_Task extraction is not implemented yet._\n",
            encoding="utf-8",
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
        path.write_text("# Transcript\n\n_Transcription is not implemented yet._\n", encoding="utf-8")
        return path

    def write_placeholder_transcript_json(self, meeting_folder: Path) -> Path:
        path = meeting_folder / "transcript.json"
        self._write_json(path, {"status": "placeholder", "segments": []})
        return path

    def write_placeholder_summary(self, meeting_folder: Path) -> Path:
        path = meeting_folder / "summary_draft.md"
        path.write_text("# Summary draft\n\n_Summarization is not implemented yet._\n", encoding="utf-8")
        return path

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
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, content: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(content, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

