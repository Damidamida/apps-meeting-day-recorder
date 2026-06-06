import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from app.services.audio import AudioExtractor, skipped_audio_metadata
from app.services.recorder import NoopRecorder, Recorder, RecorderError
from app.services.transcription import (
    LocalWhisperTranscriber,
    Transcriber,
    skipped_transcription_metadata,
    transcription_message,
)
from app.services.summarization import (
    NoopSummarizer,
    Summarizer,
    day_summary_message,
    summary_message,
)


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
    def __init__(
        self,
        root: Path,
        recorder: Recorder | None = None,
        audio_extractor: AudioExtractor | None = None,
        transcriber: Transcriber | None = None,
        summarizer: Summarizer | None = None,
    ) -> None:
        self.root = Path(root)
        self.recorder = recorder or NoopRecorder()
        self.audio_extractor = audio_extractor or AudioExtractor()
        self.transcriber = transcriber or LocalWhisperTranscriber()
        self.summarizer = summarizer or NoopSummarizer()
        self.active_day_folder: Path | None = None
        self.active_meeting_folder: Path | None = None
        self.last_workday_action: str | None = None
        self.last_recorder_message: str | None = None
        self.last_audio_message: str | None = None
        self.last_transcription_message: str | None = None
        self.last_summary_message: str | None = None
        self.last_day_summary_message: str | None = None

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
        return self.end_meeting_pipeline(ended_at)

    def end_meeting_pipeline(
        self,
        ended_at: datetime | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> Path:
        meeting_folder = self.finish_active_meeting_recording(ended_at, progress_callback)
        return self.process_meeting_pipeline(meeting_folder, progress_callback)

    def finish_active_meeting_recording(
        self,
        ended_at: datetime | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> Path:
        if not self.meeting_active:
            raise ValueError("Нет активной встречи для завершения.")

        ended_at = ended_at or datetime.now()
        meeting_folder = self.active_meeting_folder
        metadata = self._read_json(meeting_folder / "meeting_metadata.json")
        self._emit_pipeline(progress_callback, "meeting_ending", "Завершение встречи.")
        if metadata.get("recording_status") == "recording":
            self._emit_pipeline(progress_callback, "recording_stopping", "Останавливаем OBS запись.")
            metadata.update(self._stop_recording())
            self.write_metadata(meeting_folder, metadata)
            self._emit_pipeline(progress_callback, "recording_done", self.last_recorder_message or "")
        else:
            self._emit_pipeline(progress_callback, "recording_skipped", "OBS запись не активна.")

        self.write_placeholder_transcript(meeting_folder)
        self.write_placeholder_transcript_json(meeting_folder)
        self.write_placeholder_summary(meeting_folder)
        started_at = datetime.fromisoformat(metadata["started_at"])
        metadata.update(
            {
                "ended_at": ended_at.isoformat(),
                "duration_seconds": max(0, int((ended_at - started_at).total_seconds())),
                "status": "ended",
                "processing_status": "pending",
            }
        )
        self.write_metadata(meeting_folder, metadata)
        self._sync_day_meeting_metadata(meeting_folder, metadata)
        self.active_meeting_folder = None
        return meeting_folder

    def process_meeting_pipeline(
        self,
        meeting_folder: Path,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> Path:
        metadata = self._read_json(meeting_folder / "meeting_metadata.json")
        metadata.update(
            {
                "processing_status": "running",
                "processing_started_at": datetime.now().isoformat(),
            }
        )
        self.write_metadata(meeting_folder, metadata)
        self._sync_day_meeting_metadata(meeting_folder, metadata)

        self._emit_pipeline(progress_callback, "audio_running", "Извлекаем audio.wav через FFmpeg.")
        metadata.update(self._extract_audio(metadata, meeting_folder))
        self.write_metadata(meeting_folder, metadata)
        self._sync_day_meeting_metadata(meeting_folder, metadata)
        self._emit_pipeline(progress_callback, "audio_done", self.last_audio_message or "")

        self._emit_pipeline(progress_callback, "transcription_running", "Готовим локальный transcript.")
        metadata.update(self._transcribe_audio(metadata, meeting_folder))
        self.write_metadata(meeting_folder, metadata)
        self._sync_day_meeting_metadata(meeting_folder, metadata)
        self._emit_pipeline(
            progress_callback,
            "transcription_done",
            self.last_transcription_message or "",
        )

        self._emit_pipeline(progress_callback, "summary_running", "Готовим черновик итогов.")
        metadata.update(self._summarize_meeting(metadata, meeting_folder))
        self.write_metadata(meeting_folder, metadata)
        self._sync_day_meeting_metadata(meeting_folder, metadata)
        self._emit_pipeline(progress_callback, "summary_done", self.last_summary_message or "")
        metadata.update(
            {
                "processing_status": "completed",
                "processed_at": datetime.now().isoformat(),
            }
        )
        self.write_metadata(meeting_folder, metadata)
        self._sync_day_meeting_metadata(meeting_folder, metadata)
        self._emit_pipeline(progress_callback, "meeting_done", "Обработка встречи завершена.")
        return meeting_folder

    def mark_meeting_for_reprocessing(self, meeting_folder: Path) -> dict[str, Any]:
        metadata = self.read_meeting_metadata(meeting_folder)
        metadata.update(
            {
                "processing_status": "pending",
                "reprocess_requested_at": datetime.now().isoformat(),
            }
        )
        metadata.pop("processing_error", None)
        self.write_metadata(meeting_folder, metadata)
        self._sync_day_meeting_metadata(meeting_folder, metadata)
        return metadata

    @staticmethod
    def _emit_pipeline(
        progress_callback: Callable[[str, str], None] | None,
        event: str,
        message: str,
    ) -> None:
        if progress_callback is not None:
            progress_callback(event, message)

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

    def _extract_audio(self, metadata: dict[str, Any], meeting_folder: Path) -> dict[str, Any]:
        if metadata.get("recording_status") != "stopped" or not metadata.get("recording_path"):
            audio_metadata = skipped_audio_metadata()
        else:
            audio_metadata = self.audio_extractor.extract_audio(
                metadata["recording_path"],
                meeting_folder,
            )
        self.last_audio_message = self._audio_message(audio_metadata)
        return audio_metadata

    @staticmethod
    def _audio_message(metadata: dict[str, Any]) -> str:
        if metadata["audio_status"] == "extracted":
            return "Аудио извлечено."
        return f"Аудио не извлечено: {metadata['audio_error']}"

    def _transcribe_audio(self, metadata: dict[str, Any], meeting_folder: Path) -> dict[str, Any]:
        if metadata.get("audio_status") != "extracted" or not metadata.get("audio_path"):
            transcription_metadata = skipped_transcription_metadata()
        else:
            transcription_metadata = self.transcriber.transcribe(
                metadata["audio_path"],
                meeting_folder,
            )
        self.last_transcription_message = transcription_message(transcription_metadata)
        return transcription_metadata

    def _summarize_meeting(self, metadata: dict[str, Any], meeting_folder: Path) -> dict[str, Any]:
        summary_metadata = self.summarizer.summarize_meeting(meeting_folder, metadata)
        self.last_summary_message = summary_message(summary_metadata)
        return summary_metadata

    def _sync_day_meeting_metadata(self, meeting_folder: Path, metadata: dict[str, Any]) -> None:
        day_metadata_path = meeting_folder.parent / "day_metadata.json"
        if not day_metadata_path.exists():
            return
        day_metadata = self._read_json(day_metadata_path)
        meetings = day_metadata.setdefault("meetings", [])
        entry = {"folder": meeting_folder.name, **metadata}
        for index, meeting in enumerate(meetings):
            if meeting.get("folder") == meeting_folder.name:
                meetings[index] = entry
                break
        else:
            meetings.append(entry)
        self._write_json(day_metadata_path, day_metadata)

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
        self.ensure_day_summary_metadata(day_folder, created_at=ended_at)
        self._read_or_create_text(
            day_folder / "00_tasks_draft.md",
            self._tasks_placeholder(),
        )
        self.active_day_folder = None
        return day_folder

    def ensure_day_summary_metadata(
        self,
        day_folder: Path,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        created_at = created_at or datetime.now()
        metadata_path = self.day_summary_metadata_path(day_folder)
        if metadata_path.exists():
            metadata = self._read_json(metadata_path)
            metadata.setdefault("title", "Итоги дня")
            metadata.setdefault("kind", "day_summary")
            metadata.setdefault("day_folder", day_folder.name)
            metadata.setdefault("created_at", created_at.isoformat())
            metadata.setdefault("day_summary_status", "pending")
            metadata.setdefault("included_meetings", [])
            metadata.setdefault("pipeline", self._default_day_summary_pipeline())
            self._write_json(metadata_path, metadata)
            return metadata

        metadata = {
            "kind": "day_summary",
            "title": "Итоги дня",
            "day_folder": day_folder.name,
            "created_at": created_at.isoformat(),
            "updated_at": created_at.isoformat(),
            "day_summary_status": "pending",
            "included_meetings": [],
            "pipeline": self._default_day_summary_pipeline(),
        }
        self._write_json(metadata_path, metadata)
        return metadata

    def process_day_summary_pipeline(
        self,
        day_folder: Path,
        force: bool = False,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> Path:
        self.ensure_day_summary_metadata(day_folder)
        if self.has_unfinished_meeting_processing(day_folder):
            metadata = self.mark_day_summary_waiting(day_folder)
            self.last_day_summary_message = day_summary_message(metadata)
            self._emit_pipeline(
                progress_callback,
                "day_summary_waiting",
                self.last_day_summary_message,
            )
            return day_folder

        self._emit_pipeline(progress_callback, "day_summary_collecting", "Собираем итоги встреч.")
        meeting_summaries = self.collect_day_meeting_summaries(day_folder)
        metadata = self.read_day_summary_metadata(day_folder)
        included_folders = {
            str(item.get("folder") or "")
            for item in metadata.get("included_meetings", [])
        }
        current_folders = {
            str(item.get("folder") or "")
            for item in meeting_summaries
        }
        has_new_meetings = bool(current_folders - included_folders)
        if not force and metadata.get("day_summary_status") in {"draft_created", "up_to_date"} and not has_new_meetings:
            metadata.update(
                {
                    "day_summary_status": "up_to_date",
                    "updated_at": datetime.now().isoformat(),
                    "pipeline": self._day_summary_pipeline_state("ok", "ok", "skip", "ok"),
                }
            )
            self._write_json(self.day_summary_metadata_path(day_folder), metadata)
            self.last_day_summary_message = day_summary_message(metadata)
            self._emit_pipeline(progress_callback, "day_summary_up_to_date", self.last_day_summary_message)
            return day_folder

        self._emit_pipeline(progress_callback, "day_summary_checking", "Проверяем наличие summary у встреч.")
        metadata.update(
            {
                "day_summary_status": "running",
                "updated_at": datetime.now().isoformat(),
                "pipeline": self._day_summary_pipeline_state("ok", "active", "wait", "wait"),
            }
        )
        self._write_json(self.day_summary_metadata_path(day_folder), metadata)

        self._emit_pipeline(
            progress_callback,
            "day_summary_generating",
            "Готовим итоги дня через внешний AI endpoint.",
        )
        current_summary = self.read_day_summary_draft(day_folder)
        summary_metadata = self.summarizer.summarize_day(
            day_folder,
            current_summary,
            meeting_summaries,
        )
        metadata.update(summary_metadata)
        metadata.update(
            {
                "updated_at": datetime.now().isoformat(),
                "included_meetings": self._included_day_summary_meetings(meeting_summaries),
            }
        )
        status = metadata.get("day_summary_status")
        if status == "draft_created":
            metadata["pipeline"] = self._day_summary_pipeline_state("ok", "ok", "ok", "ok")
        elif status in {"disabled", "openai_unavailable", "failed"}:
            metadata["pipeline"] = self._day_summary_pipeline_state("ok", "ok", "error", "wait")
        else:
            metadata["pipeline"] = self._day_summary_pipeline_state("ok", "ok", "skip", "wait")
        self._write_json(self.day_summary_metadata_path(day_folder), metadata)
        self.last_day_summary_message = day_summary_message(metadata)
        self._emit_pipeline(progress_callback, "day_summary_done", self.last_day_summary_message)
        return day_folder

    def mark_day_summary_waiting(self, day_folder: Path) -> dict[str, Any]:
        metadata = self.ensure_day_summary_metadata(day_folder)
        metadata.update(
            {
                "day_summary_status": "waiting_for_meetings",
                "updated_at": datetime.now().isoformat(),
                "pipeline": self._day_summary_pipeline_state("active", "wait", "wait", "wait"),
            }
        )
        self._write_json(self.day_summary_metadata_path(day_folder), metadata)
        return metadata

    def collect_day_meeting_summaries(self, day_folder: Path) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for meeting_folder in self.list_meeting_folders(day_folder):
            metadata = self.read_meeting_metadata(meeting_folder)
            source, text = self._meeting_summary_source_and_text(meeting_folder)
            items.append(
                {
                    "folder": meeting_folder.name,
                    "title": str(metadata.get("title") or meeting_folder.name),
                    "started_at": str(metadata.get("started_at") or ""),
                    "summary_source": source,
                    "summary_text": text,
                    "summary_missing": source == "missing",
                }
            )
        return items

    def has_unfinished_meeting_processing(self, day_folder: Path) -> bool:
        for meeting_folder in self.list_meeting_folders(day_folder):
            metadata = self.read_meeting_metadata(meeting_folder)
            if metadata.get("status") == "active":
                return True
            if metadata.get("processing_status") in {"pending", "running"}:
                return True
        return False

    def day_summary_exists(self, day_folder: Path | None) -> bool:
        return bool(
            day_folder
            and (
                self.day_summary_metadata_path(day_folder).is_file()
                or (day_folder / "00_day_summary_draft.md").is_file()
            )
        )

    def day_summary_metadata_path(self, day_folder: Path) -> Path:
        return day_folder / "00_day_summary_metadata.json"

    def read_day_summary_metadata(self, day_folder: Path) -> dict[str, Any]:
        metadata_path = self.day_summary_metadata_path(day_folder)
        if metadata_path.exists():
            return self._read_json(metadata_path)
        return self.ensure_day_summary_metadata(day_folder)

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
        return self.list_meeting_folders(day_folder)

    @staticmethod
    def list_meeting_folders(day_folder: Path) -> list[Path]:
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

    def save_day_summary_final(self, day_folder: Path, content: str) -> Path:
        return self._write_text(day_folder / "00_day_summary_final.md", content)

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

    def _meeting_summary_source_and_text(self, meeting_folder: Path) -> tuple[str, str]:
        final_path = meeting_folder / "summary_final.md"
        if final_path.is_file():
            text = final_path.read_text(encoding="utf-8").strip()
            if text and not self._is_meeting_summary_placeholder(text):
                return "final", text

        metadata = self.read_meeting_metadata(meeting_folder)
        if metadata.get("transcription_quality") == "suspect":
            return "missing", ""

        draft_path = meeting_folder / "summary_draft.md"
        if draft_path.is_file():
            text = draft_path.read_text(encoding="utf-8").strip()
            if text and not self._is_meeting_summary_placeholder(text):
                return "draft", text

        return "missing", ""

    @staticmethod
    def _included_day_summary_meetings(meeting_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "folder": item.get("folder"),
                "title": item.get("title"),
                "started_at": item.get("started_at"),
                "summary_source": item.get("summary_source"),
                "summary_missing": item.get("summary_missing", False),
            }
            for item in meeting_summaries
        ]

    @staticmethod
    def _default_day_summary_pipeline() -> dict[str, str]:
        return {
            "collect": "wait",
            "check": "wait",
            "generate": "wait",
            "links": "wait",
        }

    @staticmethod
    def _day_summary_pipeline_state(
        collect: str,
        check: str,
        generate: str,
        links: str,
    ) -> dict[str, str]:
        return {
            "collect": collect,
            "check": check,
            "generate": generate,
            "links": links,
        }

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
    def _is_meeting_summary_placeholder(text: str) -> bool:
        return "Итоги встречи пока не заполнены" in text or "Черновик итогов встречи" in text

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

