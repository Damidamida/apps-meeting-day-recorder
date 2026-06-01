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

    def create_day_folder(self, workday: date | None = None) -> Path:
        workday = workday or date.today()
        day_folder = self.root / workday.isoformat()
        day_folder.mkdir(parents=True, exist_ok=True)
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
        self.write_placeholder_summary(meeting_folder)
        return meeting_folder

    def write_metadata(self, meeting_folder: Path, metadata: dict[str, Any]) -> Path:
        path = meeting_folder / "meeting_metadata.json"
        path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def write_placeholder_transcript(self, meeting_folder: Path) -> Path:
        path = meeting_folder / "transcript.md"
        path.write_text("# Transcript\n\n_Transcription is not implemented yet._\n", encoding="utf-8")
        return path

    def write_placeholder_summary(self, meeting_folder: Path) -> Path:
        path = meeting_folder / "summary_draft.md"
        path.write_text("# Summary draft\n\n_Summarization is not implemented yet._\n", encoding="utf-8")
        return path

