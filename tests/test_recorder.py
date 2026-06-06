import json
from datetime import datetime
from pathlib import Path

from app.services.recorder import (
    OBS_UNAVAILABLE_MESSAGE,
    NoopRecorder,
    RecorderError,
    RecorderResult,
)
from app.services.storage import StorageService


class FakeRecorder:
    enabled = True
    status_text = "OBS: подключен"

    def check_connection(self) -> str:
        return self.status_text

    def start_recording(self, meeting_folder: Path) -> RecorderResult:
        assert meeting_folder.is_dir()
        return RecorderResult(
            metadata={
                "recorder": "obs",
                "recording_status": "recording",
                "recording_started_at": "2026-06-01T09:15:01",
            },
            message="Запись OBS начата.",
        )

    def stop_recording(self) -> RecorderResult:
        return RecorderResult(
            metadata={
                "recorder": "obs",
                "recording_status": "stopped",
                "recording_stopped_at": "2026-06-01T09:45:01",
                "recording_path": "C:/recordings/meeting.mkv",
            },
            message="Запись OBS остановлена.",
        )


class FailingRecorder(FakeRecorder):
    status_text = "OBS: недоступен"

    def check_connection(self) -> str:
        raise RecorderError(OBS_UNAVAILABLE_MESSAGE)

    def start_recording(self, meeting_folder: Path) -> RecorderResult:
        del meeting_folder
        raise RecorderError(OBS_UNAVAILABLE_MESSAGE)


class StopFailingRecorder(FakeRecorder):
    def stop_recording(self) -> RecorderResult:
        raise RecorderError("Не удалось остановить запись OBS.")


class FakeAudioExtractor:
    def extract_audio(self, recording_path: str, meeting_folder: Path) -> dict[str, str]:
        assert recording_path == "C:/recordings/meeting.mkv"
        (meeting_folder / "audio.wav").touch()
        return {
            "audio_status": "extracted",
            "audio_path": str(meeting_folder / "audio.wav"),
            "audio_extracted_at": "2026-06-01T09:45:02",
        }


class FakeTranscriber:
    def __init__(self) -> None:
        self.called = False

    def transcribe(
        self,
        audio_path: str,
        meeting_folder: Path,
        progress_callback=None,
    ) -> dict[str, str]:
        del progress_callback
        self.called = True
        assert audio_path == str(meeting_folder / "audio.wav")
        (meeting_folder / "transcript.md").write_text(
            "# Транскрипт\n\nТекст встречи\n",
            encoding="utf-8",
        )
        (meeting_folder / "transcript.json").write_text(
            '{"status": "completed", "provider": "test", "text": "Текст встречи", "segments": []}\n',
            encoding="utf-8",
        )
        return {
            "transcription_status": "completed",
            "transcription_provider": "local_whisper_cli",
            "transcript_path": str(meeting_folder / "transcript.md"),
            "transcript_json_path": str(meeting_folder / "transcript.json"),
            "transcribed_at": "2026-06-01T09:45:03",
        }


def test_noop_recorder_does_not_require_obs(tmp_path) -> None:
    recorder = NoopRecorder()

    assert recorder.check_connection() == "OBS: выключен в настройках"
    assert recorder.start_recording(tmp_path).metadata == {"recording_status": "disabled"}


def test_storage_lifecycle_works_with_obs_disabled(tmp_path) -> None:
    storage = StorageService(tmp_path, NoopRecorder())
    storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Локальная встреча", datetime(2026, 6, 1, 9, 15))

    storage.end_meeting(datetime(2026, 6, 1, 9, 45))

    metadata = json.loads((meeting_folder / "meeting_metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "ended"
    assert metadata["recording_status"] == "disabled"
    assert metadata["audio_status"] == "skipped"
    assert metadata["audio_error"] == "Путь к записи отсутствует."
    assert (meeting_folder / "transcript.md").is_file()


def test_fake_recorder_updates_meeting_metadata(tmp_path) -> None:
    transcriber = FakeTranscriber()
    storage = StorageService(tmp_path, FakeRecorder(), FakeAudioExtractor(), transcriber)
    storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Записываемая встреча", datetime(2026, 6, 1, 9, 15))

    started_metadata = storage.read_meeting_metadata(meeting_folder)
    assert started_metadata["recorder"] == "obs"
    assert started_metadata["recording_status"] == "recording"
    assert started_metadata["recording_started_at"] == "2026-06-01T09:15:01"

    storage.end_meeting(datetime(2026, 6, 1, 9, 45))

    ended_metadata = storage.read_meeting_metadata(meeting_folder)
    assert ended_metadata["recording_status"] == "stopped"
    assert ended_metadata["recording_stopped_at"] == "2026-06-01T09:45:01"
    assert ended_metadata["recording_path"] == "C:/recordings/meeting.mkv"
    assert ended_metadata["audio_status"] == "extracted"
    assert ended_metadata["audio_path"] == str(meeting_folder / "audio.wav")
    assert ended_metadata["audio_extracted_at"] == "2026-06-01T09:45:02"
    assert transcriber.called
    assert ended_metadata["transcription_status"] == "completed"
    assert ended_metadata["transcription_provider"] == "local_whisper_cli"
    assert ended_metadata["transcript_path"] == str(meeting_folder / "transcript.md")
    assert ended_metadata["transcript_json_path"] == str(meeting_folder / "transcript.json")
    assert ended_metadata["transcribed_at"] == "2026-06-01T09:45:03"
    assert storage.last_transcription_message == "Транскрипция завершена."


def test_recorder_failure_keeps_local_meeting_and_readable_error(tmp_path) -> None:
    storage = StorageService(tmp_path, FailingRecorder())
    storage.start_workday(datetime(2026, 6, 1, 8, 30))

    meeting_folder = storage.start_meeting("Встреча без OBS", datetime(2026, 6, 1, 9, 15))

    metadata = storage.read_meeting_metadata(meeting_folder)
    assert storage.meeting_active
    assert metadata["status"] == "active"
    assert metadata["recording_status"] == "start_failed"
    assert metadata["recording_note"] == OBS_UNAVAILABLE_MESSAGE
    assert storage.last_recorder_message == OBS_UNAVAILABLE_MESSAGE


def test_stop_recording_failure_keeps_metadata_and_placeholder_files(tmp_path) -> None:
    storage = StorageService(tmp_path, StopFailingRecorder())
    storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Встреча со сбоем", datetime(2026, 6, 1, 9, 15))

    storage.end_meeting(datetime(2026, 6, 1, 9, 45))

    metadata = storage.read_meeting_metadata(meeting_folder)
    assert not storage.meeting_active
    assert metadata["status"] == "ended"
    assert metadata["recording_status"] == "stop_failed"
    assert metadata["recording_note"] == "Не удалось остановить запись OBS."
    assert metadata["audio_status"] == "skipped"
    assert metadata["transcription_status"] == "skipped"
    assert (meeting_folder / "transcript.md").is_file()
    assert (meeting_folder / "transcript.json").is_file()
    assert (meeting_folder / "summary_draft.md").is_file()
