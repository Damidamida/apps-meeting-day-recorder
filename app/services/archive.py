from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.services.storage import MetadataReadError, StorageService

ArchiveMatchKind = Literal[
    "Дата",
    "Название встречи",
    "Итоги дня",
    "Итоги встречи",
    "Транскрипт",
]


@dataclass(frozen=True)
class ArchiveDateFilter:
    start: date | None = None
    end: date | None = None

    def includes(self, workday: date) -> bool:
        if self.start is not None and workday < self.start:
            return False
        if self.end is not None and workday > self.end:
            return False
        return True

    @classmethod
    def week(cls, now: datetime) -> "ArchiveDateFilter":
        return cls(start=now.date() - timedelta(days=7), end=now.date())

    @classmethod
    def month(cls, now: datetime) -> "ArchiveDateFilter":
        return cls(start=now.date() - timedelta(days=31), end=now.date())


@dataclass(frozen=True)
class ArchiveMeeting:
    folder: Path
    day_folder: Path
    title: str
    started_at: str
    status_label: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ArchiveDay:
    folder: Path
    workday: date
    status_label: str
    detail_text: str
    meeting_count: int
    has_day_summary: bool
    has_unfinished_processing: bool
    metadata: dict[str, object] = field(default_factory=dict)
    meetings: list[ArchiveMeeting] = field(default_factory=list)


@dataclass(frozen=True)
class ArchiveSearchMatch:
    kind: ArchiveMatchKind
    day_folder: Path
    meeting_folder: Path | None
    title: str
    snippet: str


def build_archive_days(
    storage: StorageService,
    now: datetime | None = None,
    date_filter: ArchiveDateFilter | None = None,
) -> list[ArchiveDay]:
    now = now or datetime.now()
    days: list[ArchiveDay] = []
    for day_folder in storage.list_past_workday_folders(now):
        try:
            metadata = storage.read_day_metadata(day_folder)
            workday = date.fromisoformat(str(metadata.get("date") or day_folder.name))
        except (MetadataReadError, ValueError):
            continue
        if date_filter is not None and not date_filter.includes(workday):
            continue
        meetings = _archive_meetings(storage, day_folder)
        has_summary = storage.day_summary_exists(day_folder)
        try:
            has_unfinished = storage.has_unfinished_meeting_processing(day_folder)
            status_label = _day_status_label(storage, day_folder, metadata, has_summary, has_unfinished)
        except MetadataReadError:
            has_unfinished = False
            status_label = "Требует внимания"
        if not has_unfinished and any(meeting.status_label == "Требует внимания" for meeting in meetings):
            status_label = "Требует внимания"
        days.append(
            ArchiveDay(
                folder=day_folder,
                workday=workday,
                status_label=status_label,
                detail_text=_meeting_count_text(len(meetings)),
                meeting_count=len(meetings),
                has_day_summary=has_summary,
                has_unfinished_processing=has_unfinished,
                metadata=metadata,
                meetings=meetings,
            )
        )
    return days


def search_archive(days: list[ArchiveDay], query: str) -> list[ArchiveSearchMatch]:
    normalized = query.casefold().strip()
    if not normalized:
        return []

    matches: list[ArchiveSearchMatch] = []
    for archive_day in days:
        day_text = archive_day.workday.isoformat()
        if normalized in day_text.casefold():
            matches.append(ArchiveSearchMatch("Дата", archive_day.folder, None, day_text, day_text))

        _append_file_match(
            matches,
            "Итоги дня",
            archive_day.folder,
            None,
            "Итоги дня",
            archive_day.folder / "00_day_summary_draft.md",
            normalized,
        )
        _append_file_match(
            matches,
            "Итоги дня",
            archive_day.folder,
            None,
            "Итоги дня",
            archive_day.folder / "00_day_summary_final.md",
            normalized,
        )

        for meeting in archive_day.meetings:
            if normalized in meeting.title.casefold():
                matches.append(
                    ArchiveSearchMatch(
                        "Название встречи",
                        archive_day.folder,
                        meeting.folder,
                        meeting.title,
                        meeting.title,
                    )
                )
            _append_file_match(
                matches,
                "Итоги встречи",
                archive_day.folder,
                meeting.folder,
                meeting.title,
                meeting.folder / "summary_draft.md",
                normalized,
            )
            _append_file_match(
                matches,
                "Итоги встречи",
                archive_day.folder,
                meeting.folder,
                meeting.title,
                meeting.folder / "summary_final.md",
                normalized,
            )
            _append_file_match(
                matches,
                "Транскрипт",
                archive_day.folder,
                meeting.folder,
                meeting.title,
                meeting.folder / "transcript.md",
                normalized,
            )
    return matches


def _archive_meetings(storage: StorageService, day_folder: Path) -> list[ArchiveMeeting]:
    meetings: list[ArchiveMeeting] = []
    folders = sorted(
        storage.list_meeting_folders(day_folder),
        key=lambda folder: _meeting_sort_key(storage, folder),
        reverse=True,
    )
    for meeting_folder in folders:
        try:
            metadata = storage.read_meeting_metadata(meeting_folder)
        except MetadataReadError:
            meetings.append(
                ArchiveMeeting(
                    folder=meeting_folder,
                    day_folder=day_folder,
                    title=meeting_folder.name,
                    started_at="",
                    status_label="Требует внимания",
                    metadata={"status": "corrupted"},
                )
            )
            continue
        meetings.append(
            ArchiveMeeting(
                folder=meeting_folder,
                day_folder=day_folder,
                title=str(metadata.get("title") or meeting_folder.name),
                started_at=str(metadata.get("started_at") or ""),
                status_label=_meeting_status_label(metadata),
                metadata=metadata,
            )
        )
    return meetings


def _meeting_sort_key(storage: StorageService, meeting_folder: Path) -> tuple[str, str]:
    try:
        metadata = storage.read_meeting_metadata(meeting_folder)
    except MetadataReadError:
        return "", meeting_folder.name
    return str(metadata.get("started_at") or ""), meeting_folder.name


def _day_status_label(
    storage: StorageService,
    day_folder: Path,
    metadata: dict[str, object],
    has_summary: bool,
    has_unfinished: bool,
) -> str:
    if metadata.get("status") == "active":
        return "Незавершен"
    if has_unfinished:
        return "Обработка дня"
    try:
        day_summary_metadata = storage.read_day_summary_metadata(day_folder)
    except MetadataReadError:
        return "Требует внимания"
    day_summary_status = day_summary_metadata.get("day_summary_status")
    if day_summary_status in {"draft_created", "up_to_date"} or has_summary:
        return "Итоги готовы"
    if day_summary_status in {"failed", "openai_unavailable"}:
        return "Требует внимания"
    return "В очереди"


def _meeting_status_label(metadata: dict[str, object]) -> str:
    processing_status = metadata.get("processing_status")
    if metadata.get("__auto_healed") or metadata.get("status") == "corrupted":
        return "Требует внимания"
    if metadata.get("status") == "active":
        return "Активна"
    if processing_status in {"pending", "running"}:
        return "В очереди"
    if processing_status == "failed":
        return "Требует внимания"
    if metadata.get("summary_status") == "draft_created":
        return "Итоги готовы"
    return "Без итогов"


def _append_file_match(
    matches: list[ArchiveSearchMatch],
    kind: ArchiveMatchKind,
    day_folder: Path,
    meeting_folder: Path | None,
    title: str,
    path: Path,
    normalized_query: str,
) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    index = text.casefold().find(normalized_query)
    if index < 0:
        return
    matches.append(
        ArchiveSearchMatch(
            kind=kind,
            day_folder=day_folder,
            meeting_folder=meeting_folder,
            title=title,
            snippet=_snippet(text, index, len(normalized_query)),
        )
    )


def _snippet(text: str, index: int, query_length: int) -> str:
    start = max(0, index - 40)
    end = min(len(text), index + query_length + 80)
    snippet = " ".join(text[start:end].split())
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet += "..."
    return snippet


def _meeting_count_text(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        suffix = "встреча"
    elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        suffix = "встречи"
    else:
        suffix = "встреч"
    return f"{count} {suffix}"
