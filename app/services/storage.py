import json
import os
import re
import time
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
CORRUPTED_METADATA_MESSAGE = "Локальный metadata JSON поврежден."
INTERRUPTED_PROCESSING_MESSAGE = "Обработка была прервана при прошлом запуске приложения."
AUTO_HEALED_METADATA_STATUS = "corrupted"
JSON_READ_RETRY_DELAY_SECONDS = 0.02
JSON_READ_RETRY_COUNT = 5
JSON_WRITE_RETRY_DELAY_SECONDS = 0.02
JSON_WRITE_RETRY_COUNT = 5


class MetadataReadError(ValueError):
    def __init__(self, path: Path, backup_path: Path) -> None:
        """
        Exception representing a corrupted metadata JSON file that was moved to a backup location.
        
        Parameters:
            path (Path): The original path of the corrupted metadata file.
            backup_path (Path): The path where the corrupted file was saved.
        
        Attributes:
            path (Path): The original metadata file path.
            backup_path (Path): The backup path where the corrupted file was stored.
        """
        super().__init__(f"{CORRUPTED_METADATA_MESSAGE} Файл сохранен в backup: {backup_path}")
        self.path = path
        self.backup_path = backup_path


def safe_folder_name(title: str, fallback: str = "meeting") -> str:
    """
    Sanitizes a string for safe use as a filesystem folder name.
    
    Performs character replacement and normalization, then falls back if the result is empty or a Windows reserved device name.
    
    Parameters:
        title (str): Input title to sanitize.
        fallback (str): Value to return when the sanitized result would be empty or is a Windows reserved name.
    
    Returns:
        str: A filesystem-safe folder name (or `fallback` if the sanitized name is empty or reserved).
    """
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
        """
        Restore in-memory active day and meeting state from today's metadata file.
        
        If today's day_metadata.json is missing, corrupted, or has a status other than "active",
        the method clears any active state and returns. If the day is active, sets
        active_day_folder to today's folder and, if any meetings are marked active, sets
        active_meeting_folder to the most-recent active meeting folder (sorted order).
        
        Parameters:
            now (datetime | None): Optional current time used to determine today's folder;
                defaults to datetime.now() when not provided.
        """
        now = now or datetime.now()
        self.active_day_folder = None
        self.active_meeting_folder = None
        self.last_workday_action = None

        day_folder = self.root / now.date().isoformat()
        metadata_path = day_folder / "day_metadata.json"
        if not metadata_path.exists():
            return

        try:
            metadata = self._read_json(metadata_path)
        except MetadataReadError:
            return
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

    def find_past_active_workday(self, now: datetime | None = None) -> Path | None:
        now = now or datetime.now()
        today = now.date()
        candidates: list[tuple[date, Path]] = []
        if not self.root.is_dir():
            return None
        for day_folder in self.root.iterdir():
            if not day_folder.is_dir():
                continue
            metadata_path = day_folder / "day_metadata.json"
            if not metadata_path.is_file():
                continue
            try:
                metadata = self._read_json(metadata_path)
                workday = date.fromisoformat(str(metadata.get("date") or day_folder.name))
            except (MetadataReadError, ValueError):
                continue
            if workday < today and metadata.get("status") == "active":
                candidates.append((workday, day_folder))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def start_workday(self, started_at: datetime | None = None) -> Path:
        if self.workday_active:
            raise ValueError("Рабочий день уже активен.")

        started_at = started_at or datetime.now()
        day_folder = self.create_day_folder(started_at.date())
        metadata_path = day_folder / "day_metadata.json"
        if metadata_path.exists():
            try:
                metadata = self._read_json(metadata_path)
            except MetadataReadError:
                metadata = self._auto_healed_metadata()
            if self._is_auto_healed_metadata(metadata):
                self._write_json(metadata_path, self._new_day_metadata(started_at))
                self.active_day_folder = day_folder
                self.last_workday_action = "started"
                return day_folder
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
            self._new_day_metadata(started_at),
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
        """
        Run the meeting processing pipeline for a meeting folder: perform audio extraction, transcription, and summary generation (skipping stages already ready unless forced), while updating metadata and emitting pipeline events.
        
        Parameters:
            meeting_folder (Path): Path to the meeting folder containing or to receive meeting metadata and outputs.
            progress_callback (Callable[[str, str], None] | None): Optional callback invoked with (event, message) for pipeline progress events.
        
        Returns:
            Path: The processed meeting folder path.
        
        Side effects:
            - Writes and updates meeting metadata and synchronizes day-level metadata.
            - Emits pipeline events via the provided progress_callback when present.
            - Clears `processing_force_reprocess` and `processing_recovery_status` from metadata on completion.
        """
        metadata = self._read_json(meeting_folder / "meeting_metadata.json")
        force_reprocess = bool(metadata.get("processing_force_reprocess"))
        metadata.update(
            {
                "processing_status": "running",
                "processing_started_at": datetime.now().isoformat(),
            }
        )
        self.write_metadata(meeting_folder, metadata)
        self._sync_day_meeting_metadata(meeting_folder, metadata)

        try:
            self._emit_pipeline(progress_callback, "audio_running", "Извлекаем audio.wav через FFmpeg.")
            if self._audio_is_ready(metadata) and not force_reprocess:
                self.last_audio_message = "Аудио уже извлечено."
            else:
                metadata.update(self._extract_audio(metadata, meeting_folder))
            self.write_metadata(meeting_folder, metadata)
            self._sync_day_meeting_metadata(meeting_folder, metadata)
            self._emit_pipeline(progress_callback, "audio_done", self.last_audio_message or "")

            self._emit_pipeline(
                progress_callback,
                "transcription_running",
                str(getattr(self.transcriber, "running_message", "Готовим transcript.")),
            )
            if self._transcript_is_ready(metadata, meeting_folder) and not force_reprocess:
                self.last_transcription_message = "Транскрипт уже готов."
            else:
                metadata.update(self._transcribe_audio(metadata, meeting_folder, progress_callback))
            self.write_metadata(meeting_folder, metadata)
            self._sync_day_meeting_metadata(meeting_folder, metadata)
            self._emit_pipeline(
                progress_callback,
                "transcription_done",
                self.last_transcription_message or "",
            )

            self._emit_pipeline(progress_callback, "summary_running", "Готовим черновик итогов.")
            if self._summary_is_ready(metadata, meeting_folder) and not force_reprocess:
                self.last_summary_message = "Черновик итогов уже готов."
            else:
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
            metadata.pop("processing_force_reprocess", None)
            metadata.pop("processing_recovery_status", None)
            self.write_metadata(meeting_folder, metadata)
            self._sync_day_meeting_metadata(meeting_folder, metadata)
            self._emit_pipeline(progress_callback, "meeting_done", "Обработка встречи завершена.")
        except Exception as error:
            metadata.update(
                {
                    "processing_status": "failed",
                    "processing_error": str(error),
                    "processing_failed_at": datetime.now().isoformat(),
                }
            )
            metadata.pop("processing_force_reprocess", None)
            metadata.pop("processing_recovery_status", None)
            self.write_metadata(meeting_folder, metadata)
            self._sync_day_meeting_metadata(meeting_folder, metadata)
            raise
        return meeting_folder

    def mark_meeting_for_reprocessing(self, meeting_folder: Path) -> dict[str, Any]:
        """
        Mark a meeting to be reprocessed by the processing pipeline.
        
        Updates the meeting metadata to request reprocessing by setting `processing_status` to `"pending"`, recording `reprocess_requested_at` with the current timestamp, enabling `processing_force_reprocess`, and removing any existing `processing_error`. Persists the updated metadata and synchronizes the day-level meeting entry.
        
        Returns:
            dict: The updated meeting metadata.
        """
        metadata = self.read_meeting_metadata(meeting_folder)
        metadata.update(
            {
                "processing_status": "pending",
                "reprocess_requested_at": datetime.now().isoformat(),
                "processing_force_reprocess": True,
            }
        )
        metadata.pop("processing_error", None)
        self.write_metadata(meeting_folder, metadata)
        self._sync_day_meeting_metadata(meeting_folder, metadata)
        return metadata

    def recover_interrupted_meeting_processing(
        self,
        day_folder: Path,
        recovered_at: datetime | None = None,
    ) -> list[Path]:
        """
        Mark meetings under a day folder that were interrupted during processing as pending recovery.
        
        Parameters:
        	day_folder (Path): Path to the day folder containing meeting subfolders.
        	recovered_at (datetime | None): Timestamp to record as the recovery time; uses current time if omitted.
        
        Returns:
        	recovered (list[Path]): List of meeting folder paths that were updated for recovery.
        """
        recovered_at = recovered_at or datetime.now()
        recovered: list[Path] = []
        for meeting_folder in self.list_meeting_folders(day_folder):
            try:
                metadata = self.read_meeting_metadata(meeting_folder)
            except MetadataReadError:
                continue
            if (
                metadata.get("status") == "ended"
                and metadata.get("processing_status") == "running"
            ):
                metadata.update(
                    {
                        "processing_status": "pending",
                        "processing_recovery_status": "recovered",
                        "processing_recovered_at": recovered_at.isoformat(),
                        "processing_recovery_reason": INTERRUPTED_PROCESSING_MESSAGE,
                    }
                )
                metadata.pop("processing_force_reprocess", None)
                self.write_metadata(meeting_folder, metadata)
                self._sync_day_meeting_metadata(meeting_folder, metadata)
                recovered.append(meeting_folder)
        return recovered

    def list_pending_meeting_processing_folders(self, day_folder: Path) -> list[Path]:
        pending: list[Path] = []
        for meeting_folder in self.list_meeting_folders(day_folder):
            try:
                metadata = self.read_meeting_metadata(meeting_folder)
            except MetadataReadError:
                continue
            if (
                metadata.get("status") == "ended"
                and metadata.get("processing_status") == "pending"
            ):
                pending.append(meeting_folder)
        return pending

    @staticmethod
    def _emit_pipeline(
        progress_callback: Callable[[str, str], None] | None,
        event: str,
        message: str,
    ) -> None:
        """
        Invoke a pipeline progress callback with an event and message if a callback is provided.
        
        Parameters:
            progress_callback (Callable[[str, str], None] | None): Optional callback to receive pipeline events.
            event (str): Short event identifier (e.g., "audio_running", "transcription_done").
            message (str): Human-readable status message for the event.
        """
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

    def _transcribe_audio(
        self,
        metadata: dict[str, Any],
        meeting_folder: Path,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        if metadata.get("audio_status") != "extracted" or not metadata.get("audio_path"):
            transcription_metadata = skipped_transcription_metadata()
        else:
            transcription_metadata = self.transcriber.transcribe(
                metadata["audio_path"],
                meeting_folder,
                progress_callback=progress_callback,
            )
        self.last_transcription_message = transcription_message(transcription_metadata)
        return transcription_metadata

    def _summarize_meeting(self, metadata: dict[str, Any], meeting_folder: Path) -> dict[str, Any]:
        """
        Generate a meeting summary and update the service's last summary message.
        
        Calls the configured summarizer to produce summary metadata for the meeting and sets
        self.last_summary_message based on the returned metadata.
        
        Returns:
            summary_metadata (dict[str, Any]): Metadata produced by the summarizer for the meeting.
        """
        summary_metadata = self.summarizer.summarize_meeting(meeting_folder, metadata)
        self.last_summary_message = summary_message(summary_metadata)
        return summary_metadata

    @staticmethod
    def _audio_is_ready(metadata: dict[str, Any]) -> bool:
        """
        Determine whether extracted audio is present and points to an existing file.
        
        Parameters:
            metadata (dict): Meeting metadata expected to contain the keys
                `"audio_status"` (str) and `"audio_path"` (str or path-like).
        
        Returns:
            True if `metadata["audio_status"]` equals `"extracted"`, `audio_path` is set, and the path refers to an existing file; False otherwise.
        """
        audio_path = metadata.get("audio_path")
        return (
            metadata.get("audio_status") == "extracted"
            and bool(audio_path)
            and Path(str(audio_path)).is_file()
        )

    @staticmethod
    def _transcript_is_ready(metadata: dict[str, Any], meeting_folder: Path) -> bool:
        """
        Determines whether a meeting's transcription is marked complete and both transcript files exist.
        
        Parameters:
            metadata (dict): Meeting metadata; the function checks `metadata["transcription_status"]` and optionally uses
                `metadata["transcript_path"]` and `metadata["transcript_json_path"]` if present.
            meeting_folder (Path): Folder used as the default location for `transcript.md` and `transcript.json` when
                corresponding keys are not provided in `metadata`.
        
        Returns:
            `true` if `metadata["transcription_status"] == "completed"` and both the transcript markdown and JSON files
            exist at their resolved paths, `false` otherwise.
        """
        transcript_path = Path(str(metadata.get("transcript_path") or meeting_folder / "transcript.md"))
        transcript_json_path = Path(
            str(metadata.get("transcript_json_path") or meeting_folder / "transcript.json")
        )
        if metadata.get("transcription_status") != "completed":
            return False
        if not transcript_path.is_file() or not transcript_json_path.is_file():
            return False
        try:
            transcript_json = StorageService._read_json(transcript_json_path)
        except MetadataReadError:
            return False
        return transcript_json.get("status") == "completed"

    @staticmethod
    def _summary_is_ready(metadata: dict[str, Any], meeting_folder: Path) -> bool:
        """
        Determines whether a meeting's summary draft is present and marked as created.
        
        Parameters:
            metadata (dict[str, Any]): Meeting metadata; may contain `summary_status` and optional `summary_path`.
            meeting_folder (Path): Folder used as the default location for `summary_draft.md` when `summary_path` is not provided.
        
        Returns:
            bool: `true` if `metadata["summary_status"]` equals `"draft_created"` and the resolved summary path points to an existing file, `false` otherwise.
        """
        summary_path = Path(str(metadata.get("summary_path") or meeting_folder / "summary_draft.md"))
        if metadata.get("summary_status") != "draft_created" or not summary_path.is_file():
            return False
        try:
            summary_text = summary_path.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        return bool(summary_text) and not StorageService._is_meeting_summary_placeholder(summary_text)

    def meeting_summary_is_ready(
        self,
        meeting_folder: Path,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if metadata is None:
            metadata = self.read_meeting_metadata(meeting_folder)
        return self._summary_is_ready(metadata, meeting_folder)

    def _sync_day_meeting_metadata(self, meeting_folder: Path, metadata: dict[str, Any]) -> None:
        """
        Update the parent day's day_metadata.json to reflect the provided meeting metadata.
        
        If a day_metadata.json file exists in the meeting folder's parent, this replaces the meeting entry with the same folder name or appends a new entry, and writes the updated JSON back to disk. If the day metadata file is missing, the function does nothing.
        
        Parameters:
            meeting_folder (Path): Path to the meeting folder whose entry should be updated.
            metadata (dict[str, Any]): Meeting metadata to store; merged into the day's meetings entry under the `folder` key.
        """
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

    def end_workday_folder(
        self,
        day_folder: Path,
        ended_at: datetime | None = None,
    ) -> Path:
        ended_at = ended_at or datetime.now()
        day_folder = Path(day_folder)
        metadata_path = day_folder / "day_metadata.json"
        if not metadata_path.is_file():
            raise ValueError("Метаданные рабочего дня не найдены.")
        metadata = self._read_json(metadata_path)
        if metadata.get("status") != "active":
            raise ValueError("Рабочий день уже завершен или недоступен.")
        active_meetings = [
            meeting_folder
            for meeting_folder in self.list_meeting_folders(day_folder)
            if self._meeting_has_status(meeting_folder, "active")
        ]
        if active_meetings:
            raise ValueError(
                "Завершите активную встречу перед завершением рабочего дня."
            )

        ended_event = {"type": "ended", "at": ended_at.isoformat()}
        events = metadata.setdefault("events", [])
        metadata.update(
            {
                "ended_at": ended_at.isoformat(),
                "status": "ended",
            }
        )
        if ended_event not in events:
            events.append(ended_event)
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
        if self.active_day_folder == day_folder:
            self.active_day_folder = None
            self.active_meeting_folder = None
        return day_folder

    def ensure_day_summary_metadata(
        self,
        day_folder: Path,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        created_at = created_at or datetime.now()
        metadata_path = self.day_summary_metadata_path(day_folder)
        if metadata_path.exists():
            try:
                metadata = self._read_json(metadata_path)
            except MetadataReadError:
                metadata = self._new_day_summary_metadata(day_folder, created_at)
                self._write_json(metadata_path, metadata)
                return metadata
            if self._is_auto_healed_metadata(metadata):
                metadata = self._new_day_summary_metadata(day_folder, created_at)
                self._write_json(metadata_path, metadata)
                return metadata
            metadata.setdefault("title", "Итоги дня")
            metadata.setdefault("kind", "day_summary")
            metadata.setdefault("day_folder", day_folder.name)
            metadata.setdefault("created_at", created_at.isoformat())
            metadata.setdefault("day_summary_status", "pending")
            metadata.setdefault("included_meetings", [])
            metadata.setdefault("pipeline", self._default_day_summary_pipeline())
            self._write_json(metadata_path, metadata)
            return metadata

        metadata = self._new_day_summary_metadata(day_folder, created_at)
        self._write_json(metadata_path, metadata)
        return metadata

    @staticmethod
    def _new_day_summary_metadata(day_folder: Path, created_at: datetime) -> dict[str, Any]:
        return {
            "kind": "day_summary",
            "title": "Итоги дня",
            "day_folder": day_folder.name,
            "created_at": created_at.isoformat(),
            "updated_at": created_at.isoformat(),
            "day_summary_status": "pending",
            "included_meetings": [],
            "pipeline": StorageService._default_day_summary_pipeline(),
        }

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
            try:
                metadata = self.read_meeting_metadata(meeting_folder)
            except MetadataReadError:
                continue
            if self._is_auto_healed_metadata(metadata):
                continue
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
            try:
                metadata = self._read_json(metadata_path)
            except MetadataReadError:
                return self.ensure_day_summary_metadata(day_folder)
            if self._is_auto_healed_metadata(metadata):
                return self.ensure_day_summary_metadata(day_folder)
            return metadata
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

    def read_day_metadata(self, day_folder: Path) -> dict[str, Any]:
        metadata_path = Path(day_folder) / "day_metadata.json"
        return self._read_json(metadata_path) if metadata_path.exists() else {}

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
        """
        Check whether a meeting's stored metadata `status` equals the provided status.
        
        Parameters:
            meeting_folder (Path): Path to the meeting folder containing `meeting_metadata.json`.
            status (str): Expected status value to compare against the metadata's `"status"` field.
        
        Returns:
            `true` if the meeting's metadata `status` equals `status`, `false` otherwise. Corrupted or missing metadata is treated as not matching.
        """
        metadata_path = meeting_folder / "meeting_metadata.json"
        if not metadata_path.exists():
            return False
        try:
            return cls._read_json(metadata_path).get("status") == status
        except MetadataReadError:
            return False

    @staticmethod
    def _meeting_summary_placeholder() -> str:
        """
        Return the default placeholder text used for a meeting summary draft.
        
        Returns:
            placeholder (str): Markdown-formatted placeholder indicating the meeting summary has not been filled.
        """
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
        """
        Read and parse JSON from the given path, with retries and corruption recovery.
        
        If the file contains invalid JSON, the original file is moved to a timestamped ".corrupt-..." backup, the original path is overwritten with marker metadata (`{"status": "corrupted", "__auto_healed": true}`), and a MetadataReadError referencing the original and backup paths is raised. Transient PermissionError conditions are retried a small number of times; if retries are exhausted the PermissionError is propagated.
        
        Parameters:
            path (Path): Path to the JSON file to read.
        
        Returns:
            dict[str, Any]: The parsed JSON object.
        
        Raises:
            MetadataReadError: If the file contains invalid JSON (the corrupted file is backed up and replaced).
            PermissionError: If the file cannot be read due to permission errors after retrying.
            RuntimeError: If the internal read-retry loop ends unexpectedly.
        """
        for attempt in range(JSON_READ_RETRY_COUNT):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except PermissionError:
                if attempt == JSON_READ_RETRY_COUNT - 1:
                    raise
                time.sleep(JSON_READ_RETRY_DELAY_SECONDS)
            except json.JSONDecodeError as error:
                backup_path = StorageService._backup_corrupted_json(path)
                StorageService._write_json(path, StorageService._auto_healed_metadata())
                raise MetadataReadError(path, backup_path) from error
        raise RuntimeError("JSON read retry loop ended unexpectedly.")

    @staticmethod
    def _auto_healed_metadata() -> dict[str, Any]:
        return {
            "status": AUTO_HEALED_METADATA_STATUS,
            "__auto_healed": True,
        }

    @staticmethod
    def _is_auto_healed_metadata(metadata: dict[str, Any]) -> bool:
        return metadata == {} or bool(metadata.get("__auto_healed"))

    @staticmethod
    def _new_day_metadata(started_at: datetime) -> dict[str, Any]:
        return {
            "date": started_at.date().isoformat(),
            "started_at": started_at.isoformat(),
            "status": "active",
            "meetings": [],
            "events": [{"type": "started", "at": started_at.isoformat()}],
        }

    @staticmethod
    def _write_json(path: Path, content: dict[str, Any]) -> None:
        """
        Atomically write JSON content to a file path, using a temporary file and retrying on PermissionError.
        
        Writes the JSON-serialized `content` (pretty-printed, UTF-8) to a uniquely named temporary file in the same directory, then atomically replaces `path` with the temp file using `os.replace`. Parent directories are created if missing. On `PermissionError` the replace is retried up to the configured retry count with the configured delay between attempts.
        
        Parameters:
            path (Path): Destination file path for the JSON content.
            content (dict[str, Any]): JSON-serializable mapping to write.
        
        Raises:
            PermissionError: If the final replace attempt fails due to permission issues.
            RuntimeError: If the retry loop exits unexpectedly (should not occur under normal operation).
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.{timestamp}.tmp")
        temp_path.write_text(
            json.dumps(content, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        for attempt in range(JSON_WRITE_RETRY_COUNT):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError:
                if attempt == JSON_WRITE_RETRY_COUNT - 1:
                    raise
                time.sleep(JSON_WRITE_RETRY_DELAY_SECONDS)
        raise RuntimeError("JSON write retry loop ended unexpectedly.")

    @staticmethod
    def _backup_corrupted_json(path: Path) -> Path:
        """
        Move a corrupted JSON file to a timestamped backup alongside the original and return its new path.
        
        Parameters:
            path (Path): Path to the corrupted JSON file to back up.
        
        Returns:
            Path: The path to which the corrupted file was moved (named "<stem>.corrupt-<timestamp><suffix>").
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = path.with_name(f"{path.stem}.corrupt-{timestamp}{path.suffix}")
        os.replace(path, backup_path)
        return backup_path

