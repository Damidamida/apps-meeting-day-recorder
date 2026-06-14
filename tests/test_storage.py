import json
from datetime import date, datetime, timedelta

import pytest

from app.services.recorder import RecorderResult
from app.services import storage as storage_module
from app.services.storage import StorageService, safe_folder_name


def test_safe_folder_name_generation() -> None:
    assert safe_folder_name('  Weekly sync: "Alpha" / Beta?  ') == "Weekly_sync_Alpha_Beta"
    assert safe_folder_name("CON") == "meeting"
    assert safe_folder_name("...") == "meeting"


def test_day_folder_creation(tmp_path) -> None:
    storage = StorageService(tmp_path)

    day_folder = storage.create_day_folder(date(2026, 6, 1))

    assert day_folder == tmp_path / "2026-06-01"
    assert day_folder.is_dir()


def test_meeting_folder_creation(tmp_path) -> None:
    storage = StorageService(tmp_path)
    started_at = datetime(2026, 6, 1, 9, 5)

    meeting_folder = storage.create_meeting_folder("Planning / sync", started_at)

    assert meeting_folder == tmp_path / "2026-06-01" / "09-05_Planning_sync"
    assert meeting_folder.is_dir()
    metadata = json.loads((meeting_folder / "meeting_metadata.json").read_text(encoding="utf-8"))
    assert metadata["title"] == "Planning / sync"
    assert metadata["started_at"] == "2026-06-01T09:05:00"
    assert (meeting_folder / "transcript.md").is_file()


def test_placeholder_summary_file_generation(tmp_path) -> None:
    storage = StorageService(tmp_path)
    meeting_folder = tmp_path / "meeting"
    meeting_folder.mkdir()

    summary_path = storage.write_placeholder_summary(meeting_folder)

    assert summary_path == meeting_folder / "summary.md"
    assert "Итоги встречи" in summary_path.read_text(encoding="utf-8")


def test_start_workday_creates_day_metadata(tmp_path) -> None:
    storage = StorageService(tmp_path)

    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))

    metadata = json.loads((day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    assert metadata == {
        "date": "2026-06-01",
        "started_at": "2026-06-01T08:30:00",
        "status": "active",
        "meetings": [],
        "events": [{"type": "started", "at": "2026-06-01T08:30:00"}],
    }
    assert storage.workday_active


def test_reopen_ended_workday_preserves_files_and_allows_new_meeting(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    first_meeting = storage.start_meeting("Первый созвон", datetime(2026, 6, 1, 9, 0))
    storage.end_meeting(datetime(2026, 6, 1, 9, 30))
    storage.save_meeting_summary_draft(first_meeting, "# Черновик встречи\n")
    storage.save_day_summary_draft(day_folder, "# Черновик дня\n")
    storage.save_tasks_draft(day_folder, "# Черновик задач\n")
    storage.save_final_files(
        first_meeting,
        "# Финальные итоги встречи\n",
        "# Финальные итоги дня\n",
        "# Финальные задачи\n",
    )
    storage.end_workday(datetime(2026, 6, 1, 18, 0))

    reopened_folder = storage.start_workday(datetime(2026, 6, 1, 18, 15))
    second_meeting = storage.start_meeting("Второй созвон", datetime(2026, 6, 1, 18, 30))

    metadata_path = day_folder / "day_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert reopened_folder == day_folder
    assert storage.active_day_folder == day_folder
    assert storage.last_workday_action == "reopened"
    assert metadata["status"] == "active"
    assert metadata["ended_at"] is None
    assert metadata["events"] == [
        {"type": "started", "at": "2026-06-01T08:30:00"},
        {"type": "ended", "at": "2026-06-01T18:00:00"},
        {"type": "reopened", "at": "2026-06-01T18:15:00"},
    ]
    assert first_meeting.is_dir()
    assert second_meeting.parent == day_folder
    assert (first_meeting / "summary_draft.md").read_text(encoding="utf-8") == (
        "# Черновик встречи\n"
    )
    assert (first_meeting / "summary_final.md").is_file()
    assert (day_folder / "00_day_summary_draft.md").read_text(encoding="utf-8") == (
        "# Черновик дня\n"
    )
    assert (day_folder / "00_tasks_draft.md").read_text(encoding="utf-8") == (
        "# Черновик задач\n"
    )
    assert (day_folder / "00_day_summary_final.md").is_file()
    assert (day_folder / "00_tasks_final.md").is_file()

    storage.end_meeting(datetime(2026, 6, 1, 19, 0))
    storage.end_workday(datetime(2026, 6, 1, 20, 0))

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["ended_at"] == "2026-06-01T20:00:00"
    assert metadata["events"][-1] == {"type": "ended", "at": "2026-06-01T20:00:00"}


def test_reopen_legacy_ended_workday_adds_events(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    metadata_path = day_folder / "day_metadata.json"
    metadata_path.write_text(
        '{"date": "2026-06-01", "status": "ended", '
        '"ended_at": "2026-06-01T18:00:00", "meetings": []}\n',
        encoding="utf-8",
    )

    reopened_folder = storage.start_workday(datetime(2026, 6, 1, 18, 15))

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert reopened_folder == day_folder
    assert metadata["status"] == "active"
    assert metadata["ended_at"] is None
    assert metadata["events"] == [
        {"type": "ended", "at": "2026-06-01T18:00:00"},
        {"type": "reopened", "at": "2026-06-01T18:15:00"},
    ]


def test_start_meeting_creates_active_metadata(tmp_path) -> None:
    storage = StorageService(tmp_path)
    storage.start_workday(datetime(2026, 6, 1, 8, 30))

    meeting_folder = storage.start_meeting("Product / sync", datetime(2026, 6, 1, 9, 15))

    metadata = json.loads((meeting_folder / "meeting_metadata.json").read_text(encoding="utf-8"))
    assert meeting_folder.name == "09-15_Product_sync"
    assert metadata == {
        "title": "Product / sync",
        "started_at": "2026-06-01T09:15:00",
        "status": "active",
        "recording_status": "disabled",
    }
    assert storage.meeting_active


def test_end_meeting_creates_placeholder_files_and_updates_day(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Planning", datetime(2026, 6, 1, 9, 15))

    storage.end_meeting(datetime(2026, 6, 1, 9, 45, 10))

    metadata = json.loads((meeting_folder / "meeting_metadata.json").read_text(encoding="utf-8"))
    day_metadata = json.loads((day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    assert metadata["ended_at"] == "2026-06-01T09:45:10"
    assert metadata["duration_seconds"] == 1810
    assert metadata["status"] == "ended"
    assert metadata["summary_status"] == "disabled"
    assert (meeting_folder / "transcript.md").is_file()
    assert (meeting_folder / "transcript.json").is_file()
    assert (meeting_folder / "summary.md").is_file()
    assert day_metadata["meetings"] == [{"folder": "09-15_Planning", **metadata}]
    assert not storage.meeting_active


def test_finish_active_meeting_recording_allows_next_meeting_before_processing(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    first_meeting = storage.start_meeting("Первый созвон", datetime(2026, 6, 1, 9, 0))

    finished_meeting = storage.finish_active_meeting_recording(datetime(2026, 6, 1, 9, 30))
    second_meeting = storage.start_meeting("Второй созвон", datetime(2026, 6, 1, 9, 31))

    first_metadata = json.loads(
        (first_meeting / "meeting_metadata.json").read_text(encoding="utf-8")
    )
    day_metadata = json.loads((day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    assert finished_meeting == first_meeting
    assert second_meeting != first_meeting
    assert storage.active_meeting_folder == second_meeting
    assert first_metadata["status"] == "ended"
    assert first_metadata["processing_status"] == "pending"
    assert day_metadata["meetings"][0]["folder"] == first_meeting.name
    assert day_metadata["meetings"][0]["processing_status"] == "pending"


def test_process_meeting_pipeline_updates_existing_day_meeting_entry(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Pipeline", datetime(2026, 6, 1, 9, 0))
    storage.finish_active_meeting_recording(datetime(2026, 6, 1, 9, 30))

    storage.process_meeting_pipeline(meeting_folder)

    metadata = json.loads((meeting_folder / "meeting_metadata.json").read_text(encoding="utf-8"))
    day_metadata = json.loads((day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    assert metadata["processing_status"] == "completed"
    assert day_metadata["meetings"] == [{"folder": meeting_folder.name, **metadata}]


def test_process_meeting_pipeline_marks_failed_on_unhandled_exception(tmp_path) -> None:
    class FailingAudioExtractor:
        def extract_audio(self, recording_path, meeting_folder):
            del recording_path, meeting_folder
            raise RuntimeError("FFmpeg crashed")

    storage = StorageService(tmp_path, audio_extractor=FailingAudioExtractor())
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Pipeline failure", datetime(2026, 6, 1, 9, 0))
    storage.finish_active_meeting_recording(datetime(2026, 6, 1, 9, 30))
    metadata = storage.read_meeting_metadata(meeting_folder)
    metadata.update(
        {
            "recording_status": "stopped",
            "recording_path": str(meeting_folder / "recording.mkv"),
        }
    )
    storage.write_metadata(meeting_folder, metadata)
    storage._sync_day_meeting_metadata(meeting_folder, metadata)

    with pytest.raises(RuntimeError, match="FFmpeg crashed"):
        storage.process_meeting_pipeline(meeting_folder)

    failed_metadata = storage.read_meeting_metadata(meeting_folder)
    day_metadata = json.loads((day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    day_entry = next(meeting for meeting in day_metadata["meetings"] if meeting["folder"] == meeting_folder.name)
    assert failed_metadata["processing_status"] == "failed"
    assert failed_metadata["processing_error"] == "FFmpeg crashed"
    assert "processing_failed_at" in failed_metadata
    assert day_entry["processing_status"] == "failed"
    assert day_entry["processing_error"] == "FFmpeg crashed"
    assert not storage.has_unfinished_meeting_processing(day_folder)


def test_running_meeting_is_marked_for_recovery_after_restart(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Interrupted", datetime(2026, 6, 1, 9, 0))
    storage.finish_active_meeting_recording(datetime(2026, 6, 1, 9, 30))
    metadata = storage.read_meeting_metadata(meeting_folder)
    metadata.update(
        {
            "processing_status": "running",
            "processing_started_at": "2026-06-01T09:31:00",
            "audio_status": "extracted",
            "audio_path": str(meeting_folder / "audio.wav"),
        }
    )
    storage.write_metadata(meeting_folder, metadata)
    storage._sync_day_meeting_metadata(meeting_folder, metadata)

    restored_storage = StorageService(tmp_path)
    recovered = restored_storage.recover_interrupted_meeting_processing(
        day_folder,
        recovered_at=datetime(2026, 6, 1, 10, 0),
    )

    recovered_metadata = restored_storage.read_meeting_metadata(meeting_folder)
    day_metadata = json.loads((day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    assert recovered == [meeting_folder]
    assert recovered_metadata["processing_status"] == "pending"
    assert recovered_metadata["processing_recovery_status"] == "recovered"
    assert recovered_metadata["processing_recovered_at"] == "2026-06-01T10:00:00"
    assert recovered_metadata["processing_recovery_reason"] == (
        "Обработка была прервана при прошлом запуске приложения."
    )
    assert day_metadata["meetings"][0]["processing_status"] == "pending"
    assert day_metadata["meetings"][0]["processing_recovery_status"] == "recovered"


def test_corrupted_meeting_metadata_is_backed_up_and_reported(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    meeting_folder = day_folder / "09-00_Broken"
    meeting_folder.mkdir()
    metadata_path = meeting_folder / "meeting_metadata.json"
    metadata_path.write_text('{"status": "ended",', encoding="utf-8")

    with pytest.raises(storage_module.MetadataReadError) as error:
        storage.read_meeting_metadata(meeting_folder)

    assert error.value.path == metadata_path
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == {
        "status": "corrupted",
        "__auto_healed": True,
    }
    backups = list(meeting_folder.glob("meeting_metadata.corrupt-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"status": "ended",'


def test_metadata_read_retries_transient_permission_error(tmp_path, monkeypatch) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    meeting_folder = day_folder / "09-00_Locked"
    meeting_folder.mkdir()
    metadata_path = meeting_folder / "meeting_metadata.json"
    metadata_path.write_text('{"status": "ended"}', encoding="utf-8")
    original_read_text = storage_module.Path.read_text
    attempts = {"count": 0}
    sleeps = {"count": 0}

    def fake_sleep(delay):
        del delay
        sleeps["count"] += 1

    monkeypatch.setattr(storage_module.time, "sleep", fake_sleep)

    def flaky_read_text(path, *args, **kwargs):
        """
        Simulate a transient PermissionError for a target metadata path on its first invocation, then return the file's text.
        
        Parameters:
            path: Path-like object to read; when equal to the test's `metadata_path` this function raises PermissionError once on the first call.
            
        Returns:
            str: The text content of the file at `path`.
        
        Raises:
            PermissionError: Raised once for the target `metadata_path` to simulate a brief lock; subsequent calls return the file content.
        """
        if path == metadata_path and attempts["count"] == 0:
            attempts["count"] += 1
            raise PermissionError("metadata is briefly locked")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(storage_module.Path, "read_text", flaky_read_text)

    assert storage.read_meeting_metadata(meeting_folder) == {"status": "ended"}
    assert attempts["count"] == 1
    assert sleeps["count"] == 1
    assert not list(meeting_folder.glob("meeting_metadata.corrupt-*.json"))


def test_metadata_write_retries_transient_permission_error(tmp_path, monkeypatch) -> None:
    """
    Verifies that StorageService._write_json retries and succeeds when a transient PermissionError occurs during os.replace.
    
    Asserts that the temporary failure is retried exactly once, the final metadata file contains the expected JSON content, and no temporary `.tmp` files are left behind.
    """
    path = tmp_path / "meeting_metadata.json"
    original_replace = storage_module.os.replace
    attempts = {"count": 0}
    sleeps = {"count": 0}

    def fake_sleep(delay):
        del delay
        sleeps["count"] += 1

    monkeypatch.setattr(storage_module.time, "sleep", fake_sleep)

    def flaky_replace(source, destination):
        """
        Simulate a flaky file replace operation that raises PermissionError once for a specific destination path.
        
        Parameters:
            source (str | pathlib.Path): Source path passed to the replace operation.
            destination (str | pathlib.Path): Destination path passed to the replace operation; when equal to the test `path` the function will raise PermissionError on the first call.
        
        Returns:
            The value returned by the underlying `original_replace` call.
        """
        if destination == path and attempts["count"] == 0:
            attempts["count"] += 1
            raise PermissionError("metadata is briefly locked")
        return original_replace(source, destination)

    monkeypatch.setattr(storage_module.os, "replace", flaky_replace)

    storage_module.StorageService._write_json(path, {"status": "ended"})

    assert attempts["count"] == 1
    assert sleeps["count"] == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "ended"}
    assert not list(tmp_path.glob(".meeting_metadata.json.*.tmp"))


def test_corrupted_day_metadata_does_not_restore_active_day(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    metadata_path = day_folder / "day_metadata.json"
    metadata_path.write_text('{"status": "active",', encoding="utf-8")

    storage.load_today_state(datetime(2026, 6, 1, 10, 0))

    assert not storage.workday_active
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == {
        "status": "corrupted",
        "__auto_healed": True,
    }
    backups = list(day_folder.glob("day_metadata.corrupt-*.json"))
    assert len(backups) == 1


def test_start_workday_recreates_auto_healed_day_metadata(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    metadata_path = day_folder / "day_metadata.json"
    metadata_path.write_text('{"status": "active",', encoding="utf-8")
    storage.load_today_state(datetime(2026, 6, 1, 9, 0))

    reopened_folder = storage.start_workday(datetime(2026, 6, 1, 9, 5))

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert reopened_folder == day_folder
    assert metadata["status"] == "active"
    assert metadata["started_at"] == "2026-06-01T09:05:00"
    assert metadata["events"] == [{"type": "started", "at": "2026-06-01T09:05:00"}]
    assert "__auto_healed" not in metadata


def test_start_workday_recovers_corrupted_day_metadata_directly(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    metadata_path = day_folder / "day_metadata.json"
    metadata_path.write_text('{"status": "active",', encoding="utf-8")

    started_folder = storage.start_workday(datetime(2026, 6, 1, 9, 5))

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert started_folder == day_folder
    assert metadata["status"] == "active"
    assert metadata["started_at"] == "2026-06-01T09:05:00"
    assert "__auto_healed" not in metadata
    assert list(day_folder.glob("day_metadata.corrupt-*.json"))


def test_start_workday_recreates_legacy_empty_day_metadata(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    metadata_path = day_folder / "day_metadata.json"
    metadata_path.write_text("{}\n", encoding="utf-8")

    started_folder = storage.start_workday(datetime(2026, 6, 1, 9, 5))

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert started_folder == day_folder
    assert metadata["status"] == "active"
    assert metadata["started_at"] == "2026-06-01T09:05:00"
    assert "__auto_healed" not in metadata


def test_corrupted_active_meeting_metadata_does_not_break_today_restore(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = day_folder / "09-00_Broken"
    meeting_folder.mkdir()
    metadata_path = meeting_folder / "meeting_metadata.json"
    metadata_path.write_text('{"status": "active",', encoding="utf-8")

    restored_storage = StorageService(tmp_path)
    restored_storage.load_today_state(datetime(2026, 6, 1, 10, 0))

    assert restored_storage.workday_active
    assert not restored_storage.meeting_active
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == {
        "status": "corrupted",
        "__auto_healed": True,
    }
    backups = list(meeting_folder.glob("meeting_metadata.corrupt-*.json"))
    assert len(backups) == 1


def test_recovered_pipeline_skips_completed_steps(tmp_path) -> None:
    class FailingAudioExtractor:
        def extract_audio(self, recording_path, meeting_folder):
            """
            Prevent extraction of audio for a meeting; calling this method always raises an AssertionError.
            
            Raises:
                AssertionError: always raised with message "audio should not be extracted again".
            """
            raise AssertionError("audio should not be extracted again")

    class FailingTranscriber:
        running_message = "transcriber should not run"

        def transcribe(self, audio_path, meeting_folder, progress_callback=None):
            """
            Stub transcribe implementation used in tests that deliberately fails if invoked.
            
            This function always raises an AssertionError to signal that transcription must not be executed
            (attempting to run transcription again is considered a test failure).
            
            Parameters:
                audio_path: Path or str to the audio file (ignored).
                meeting_folder: Path or str to the meeting folder (ignored).
                progress_callback: Optional callable for progress updates (ignored).
            
            Raises:
                AssertionError: Always raised to indicate the transcribe function should not be called.
            """
            raise AssertionError("transcription should not run again")

    class FailingSummarizer:
        def summarize_meeting(self, meeting_folder, metadata):
            """
            Placeholder meeting summarizer used in tests that fails if invoked.
            
            Raises:
                AssertionError: Always raised to indicate the summarization step must not be run.
            """
            raise AssertionError("summary should not run again")

        def summarize_day(self, day_folder, current_summary, meeting_summaries):
            """
            Test stub used in tests that must not invoke day summarization; it fails immediately if called.
            
            Parameters:
                day_folder (pathlib.Path): Path to the day folder that would be summarized.
                current_summary (str): Existing day summary text (may be a placeholder).
                meeting_summaries (list): Collected meeting summary items to include in the day summary.
            
            Raises:
                AssertionError: Always raised to signal that day summarization should not run in this test.
            """
            raise AssertionError("day summary is not part of this test")

    storage = StorageService(
        tmp_path,
        audio_extractor=FailingAudioExtractor(),
        transcriber=FailingTranscriber(),
        summarizer=FailingSummarizer(),
    )
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Recovered", datetime(2026, 6, 1, 9, 0))
    storage.finish_active_meeting_recording(datetime(2026, 6, 1, 9, 30))
    audio_path = meeting_folder / "audio.wav"
    transcript_path = meeting_folder / "transcript.md"
    transcript_json_path = meeting_folder / "transcript.json"
    summary_path = meeting_folder / "summary_draft.md"
    audio_path.write_bytes(b"audio")
    transcript_path.write_text("# Транскрипт\n\nГотов.\n", encoding="utf-8")
    transcript_json_path.write_text(
        json.dumps({"status": "completed", "text": "Готов.", "segments": []}),
        encoding="utf-8",
    )
    summary_path.write_text("# Итоги встречи\n\nГотовы.\n", encoding="utf-8")
    metadata = storage.read_meeting_metadata(meeting_folder)
    metadata.update(
        {
            "processing_status": "running",
            "recording_status": "stopped",
            "audio_status": "extracted",
            "audio_path": str(audio_path),
            "transcription_status": "completed",
            "transcript_path": str(transcript_path),
            "transcript_json_path": str(transcript_json_path),
            "summary_status": "draft_created",
            "summary_path": str(summary_path),
        }
    )
    storage.write_metadata(meeting_folder, metadata)
    storage._sync_day_meeting_metadata(meeting_folder, metadata)

    storage.recover_interrupted_meeting_processing(day_folder, datetime(2026, 6, 1, 10, 0))
    storage.process_meeting_pipeline(meeting_folder)

    recovered_metadata = storage.read_meeting_metadata(meeting_folder)
    assert recovered_metadata["processing_status"] == "completed"
    assert "processing_recovery_status" not in recovered_metadata
    day_metadata = json.loads((day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    day_entry = next(meeting for meeting in day_metadata["meetings"] if meeting["folder"] == meeting_folder.name)
    assert day_entry["processing_status"] == "completed"
    assert "processing_recovery_status" not in day_entry
    assert "processing_force_reprocess" not in day_entry


def test_recovered_pipeline_does_not_skip_placeholder_outputs(tmp_path) -> None:
    calls = {"transcription": 0, "summary": 0}

    class FailingAudioExtractor:
        def extract_audio(self, recording_path, meeting_folder):
            raise AssertionError("audio should not be extracted again")

    class CountingTranscriber:
        running_message = "transcribing"

        def transcribe(self, audio_path, meeting_folder, progress_callback=None):
            del audio_path, progress_callback
            calls["transcription"] += 1
            transcript_path = meeting_folder / "transcript.md"
            transcript_json_path = meeting_folder / "transcript.json"
            transcript_path.write_text("Real transcript.\n", encoding="utf-8")
            transcript_json_path.write_text(
                json.dumps({"status": "completed", "text": "Real transcript.", "segments": []}),
                encoding="utf-8",
            )
            return {
                "transcription_status": "completed",
                "transcript_path": str(transcript_path),
                "transcript_json_path": str(transcript_json_path),
            }

    class CountingSummarizer:
        def summarize_meeting(self, meeting_folder, metadata):
            del metadata
            calls["summary"] += 1
            summary_path = meeting_folder / "summary_draft.md"
            summary_path.write_text("# Real summary\n", encoding="utf-8")
            return {"summary_status": "draft_created", "summary_path": str(summary_path)}

        def summarize_day(self, day_folder, current_summary, meeting_summaries):
            raise AssertionError("day summary is not part of this test")

    storage = StorageService(
        tmp_path,
        audio_extractor=FailingAudioExtractor(),
        transcriber=CountingTranscriber(),
        summarizer=CountingSummarizer(),
    )
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Placeholder recovery", datetime(2026, 6, 1, 9, 0))
    storage.finish_active_meeting_recording(datetime(2026, 6, 1, 9, 30))
    audio_path = meeting_folder / "audio.wav"
    audio_path.write_bytes(b"audio")
    metadata = storage.read_meeting_metadata(meeting_folder)
    metadata.update(
        {
            "processing_status": "running",
            "recording_status": "stopped",
            "audio_status": "extracted",
            "audio_path": str(audio_path),
            "transcription_status": "completed",
            "transcript_path": str(meeting_folder / "transcript.md"),
            "transcript_json_path": str(meeting_folder / "transcript.json"),
            "summary_status": "draft_created",
            "summary_path": str(meeting_folder / "summary_draft.md"),
        }
    )
    storage.write_metadata(meeting_folder, metadata)
    storage._sync_day_meeting_metadata(meeting_folder, metadata)

    storage.recover_interrupted_meeting_processing(day_folder, datetime(2026, 6, 1, 10, 0))
    storage.process_meeting_pipeline(meeting_folder)

    assert calls == {"transcription": 1, "summary": 1}


def test_manual_reprocess_runs_completed_steps_again(tmp_path) -> None:
    """
    Verifies that marking a meeting for reprocessing forces all pipeline steps to run again.
    
    Creates a meeting with completed processing state, marks it for reprocessing, and asserts that the audio extraction, transcription, and summarization steps are each executed exactly once. Also verifies the meeting metadata ends with `processing_status` equal to `"completed"` and that the `processing_force_reprocess` flag is removed.
    """
    calls = {"audio": 0, "transcription": 0, "summary": 0}

    class CountingAudioExtractor:
        def extract_audio(self, recording_path, meeting_folder):
            """
            Simulate extracting audio for a meeting by creating an `audio.wav` file in the meeting folder and returning extraction metadata.
            
            Parameters:
                recording_path (Path): Path to the source recording (unused by this test helper).
                meeting_folder (Path): Destination folder where the extracted `audio.wav` will be written.
            
            Returns:
                dict: Extraction metadata with keys:
                    - "audio_status": `"extracted"` when the simulated extraction succeeded.
                    - "audio_path": string path to the written `audio.wav` file.
            """
            del recording_path
            calls["audio"] += 1
            audio_path = meeting_folder / "audio.wav"
            audio_path.write_bytes(b"new audio")
            return {"audio_status": "extracted", "audio_path": str(audio_path)}

    class CountingTranscriber:
        running_message = "transcribing"

        def transcribe(self, audio_path, meeting_folder, progress_callback=None):
            """
            Write a completed transcript and transcript JSON into the given meeting folder and return transcription metadata.
            
            Parameters:
                audio_path: Path-like object for the source audio (not used by this fake implementation).
                meeting_folder (pathlib.Path): Directory where `transcript.md` and `transcript.json` will be written.
                progress_callback: Optional callable for progress updates (ignored by this implementation).
            
            Returns:
                dict: Metadata about the transcription with keys:
                    - `transcription_status` (str): `"completed"`.
                    - `transcript_path` (str): Filesystem path to the written `transcript.md`.
                    - `transcript_json_path` (str): Filesystem path to the written `transcript.json`.
            """
            del audio_path, progress_callback
            calls["transcription"] += 1
            transcript_path = meeting_folder / "transcript.md"
            transcript_json_path = meeting_folder / "transcript.json"
            transcript_path.write_text("New transcript.\n", encoding="utf-8")
            transcript_json_path.write_text(
                json.dumps({"status": "completed", "text": "New transcript.", "segments": []}),
                encoding="utf-8",
            )
            return {
                "transcription_status": "completed",
                "transcript_path": str(transcript_path),
                "transcript_json_path": str(transcript_json_path),
            }

    class CountingSummarizer:
        def summarize_meeting(self, meeting_folder, metadata):
            """
            Create a meeting summary draft file inside the specified meeting folder.
            
            Parameters:
                meeting_folder (pathlib.Path): Directory of the meeting where the draft will be written.
                metadata (dict): Meeting metadata dictionary.
            
            Returns:
                dict: A mapping with `"summary_status": "draft_created"` and `"summary_path"` set to the created draft file path as a string.
            """
            del metadata
            calls["summary"] += 1
            summary_path = meeting_folder / "summary_draft.md"
            summary_path.write_text("# New summary\n", encoding="utf-8")
            return {"summary_status": "draft_created", "summary_path": str(summary_path)}

        def summarize_day(self, day_folder, current_summary, meeting_summaries):
            """
            Test stub used in tests that must not invoke day summarization; it fails immediately if called.
            
            Parameters:
                day_folder (pathlib.Path): Path to the day folder that would be summarized.
                current_summary (str): Existing day summary text (may be a placeholder).
                meeting_summaries (list): Collected meeting summary items to include in the day summary.
            
            Raises:
                AssertionError: Always raised to signal that day summarization should not run in this test.
            """
            raise AssertionError("day summary is not part of this test")

    storage = StorageService(
        tmp_path,
        audio_extractor=CountingAudioExtractor(),
        transcriber=CountingTranscriber(),
        summarizer=CountingSummarizer(),
    )
    storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Manual reprocess", datetime(2026, 6, 1, 9, 0))
    storage.finish_active_meeting_recording(datetime(2026, 6, 1, 9, 30))
    recording_path = meeting_folder / "recording.mkv"
    recording_path.write_bytes(b"video")
    audio_path = meeting_folder / "audio.wav"
    transcript_path = meeting_folder / "transcript.md"
    transcript_json_path = meeting_folder / "transcript.json"
    summary_path = meeting_folder / "summary_draft.md"
    audio_path.write_bytes(b"old audio")
    transcript_path.write_text("Old transcript.\n", encoding="utf-8")
    transcript_json_path.write_text(
        json.dumps({"status": "completed", "text": "Old transcript.", "segments": []}),
        encoding="utf-8",
    )
    summary_path.write_text("# Old summary\n", encoding="utf-8")
    metadata = storage.read_meeting_metadata(meeting_folder)
    metadata.update(
        {
            "processing_status": "completed",
            "recording_status": "stopped",
            "recording_path": str(recording_path),
            "audio_status": "extracted",
            "audio_path": str(audio_path),
            "transcription_status": "completed",
            "transcript_path": str(transcript_path),
            "transcript_json_path": str(transcript_json_path),
            "summary_status": "draft_created",
            "summary_path": str(summary_path),
        }
    )
    storage.write_metadata(meeting_folder, metadata)

    storage.mark_meeting_for_reprocessing(meeting_folder)
    storage.process_meeting_pipeline(meeting_folder)

    assert calls == {"audio": 1, "transcription": 1, "summary": 1}
    metadata = storage.read_meeting_metadata(meeting_folder)
    assert metadata["processing_status"] == "completed"
    assert "processing_force_reprocess" not in metadata
    day_metadata = json.loads(((tmp_path / "2026-06-01") / "day_metadata.json").read_text(encoding="utf-8"))
    day_entry = next(meeting for meeting in day_metadata["meetings"] if meeting["folder"] == meeting_folder.name)
    assert day_entry["processing_status"] == "completed"
    assert "processing_recovery_status" not in day_entry
    assert "processing_force_reprocess" not in day_entry


def test_end_workday_creates_day_drafts(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))

    storage.end_workday(datetime(2026, 6, 1, 18, 0))

    metadata = json.loads((day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    assert metadata["ended_at"] == "2026-06-01T18:00:00"
    assert metadata["status"] == "ended"
    assert metadata["events"] == [
        {"type": "started", "at": "2026-06-01T08:30:00"},
        {"type": "ended", "at": "2026-06-01T18:00:00"},
    ]
    assert (day_folder / "00_day_summary.md").is_file()
    assert (day_folder / "00_tasks_draft.md").is_file()
    assert not storage.workday_active


def test_active_day_is_restored_after_restart(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))

    restored_storage = StorageService(tmp_path)
    restored_storage.load_today_state(datetime(2026, 6, 1, 10, 0))

    assert restored_storage.active_day_folder == day_folder
    assert restored_storage.workday_active
    assert not restored_storage.meeting_active


def test_ended_day_is_not_restored_as_active(tmp_path) -> None:
    storage = StorageService(tmp_path)
    storage.start_workday(datetime(2026, 6, 1, 8, 30))
    storage.end_workday(datetime(2026, 6, 1, 18, 0))

    restored_storage = StorageService(tmp_path)
    restored_storage.load_today_state(datetime(2026, 6, 1, 19, 0))

    assert not restored_storage.workday_active
    assert not restored_storage.meeting_active


def test_active_meeting_is_restored_after_restart(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Restart sync", datetime(2026, 6, 1, 9, 15))

    restored_storage = StorageService(tmp_path)
    restored_storage.load_today_state(datetime(2026, 6, 1, 9, 30))

    assert restored_storage.active_day_folder == day_folder
    assert restored_storage.active_meeting_folder == meeting_folder
    assert restored_storage.workday_active
    assert restored_storage.meeting_active


def test_past_active_workday_is_found_without_restoring_as_today(tmp_path) -> None:
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    storage = StorageService(tmp_path)
    past_day_folder = storage.start_workday(yesterday.replace(hour=8, minute=30))

    restored_storage = StorageService(tmp_path)
    restored_storage.load_today_state(now)

    assert not restored_storage.workday_active
    assert restored_storage.find_past_active_workday(now) == past_day_folder


def test_end_workday_folder_finishes_past_day_without_touching_today(tmp_path) -> None:
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    past_storage = StorageService(tmp_path)
    past_day_folder = past_storage.start_workday(yesterday.replace(hour=8, minute=30))

    storage = StorageService(tmp_path)
    today_day_folder = storage.start_workday(now.replace(hour=9, minute=0))

    ended_at = now.replace(hour=18, minute=0)
    finished_folder = storage.end_workday_folder(past_day_folder, ended_at)

    past_metadata = json.loads((past_day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    today_metadata = json.loads((today_day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    assert finished_folder == past_day_folder
    assert past_metadata["status"] == "ended"
    assert past_metadata["ended_at"] == ended_at.isoformat()
    assert past_metadata["events"][-1] == {"type": "ended", "at": ended_at.isoformat()}
    assert today_metadata["status"] == "active"
    assert storage.active_day_folder == today_day_folder
    assert storage.workday_active


def test_end_workday_folder_rejects_active_meetings(tmp_path) -> None:
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    storage = StorageService(tmp_path)
    past_day_folder = storage.start_workday(yesterday.replace(hour=8, minute=30))
    storage.start_meeting("Active", yesterday.replace(hour=9, minute=0))

    with pytest.raises(ValueError, match="активную встречу"):
        storage.end_workday_folder(past_day_folder, now.replace(hour=18, minute=0))

    metadata = json.loads((past_day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "active"
    assert "ended_at" not in metadata
    assert not any(event.get("type") == "ended" for event in metadata.get("events", []))


def test_pending_processing_folders_include_recovered_running_meetings(tmp_path) -> None:
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(yesterday.replace(hour=8, minute=30))
    pending_meeting = storage.start_meeting("Pending", yesterday.replace(hour=9, minute=0))
    storage.finish_active_meeting_recording(yesterday.replace(hour=9, minute=30))
    running_meeting = storage.start_meeting("Running", yesterday.replace(hour=10, minute=0))
    storage.finish_active_meeting_recording(yesterday.replace(hour=10, minute=30))
    running_metadata = storage.read_meeting_metadata(running_meeting)
    running_metadata["processing_status"] = "running"
    storage.write_metadata(running_meeting, running_metadata)
    storage._sync_day_meeting_metadata(running_meeting, running_metadata)

    recovered = storage.recover_interrupted_meeting_processing(day_folder)
    pending_folders = storage.list_pending_meeting_processing_folders(day_folder)

    assert recovered == [running_meeting]
    assert pending_folders == [pending_meeting, running_meeting]
    assert storage.read_meeting_metadata(running_meeting)["processing_status"] == "pending"


def test_full_happy_path_still_works(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Happy path", datetime(2026, 6, 1, 9, 15))

    storage.end_meeting(datetime(2026, 6, 1, 9, 45))
    storage.end_workday(datetime(2026, 6, 1, 18, 0))

    assert (meeting_folder / "meeting_metadata.json").is_file()
    assert (meeting_folder / "transcript.md").is_file()
    assert (meeting_folder / "transcript.json").is_file()
    assert (meeting_folder / "summary.md").is_file()
    assert (day_folder / "00_day_summary.md").is_file()
    assert (day_folder / "00_tasks_draft.md").is_file()
    assert not storage.workday_active
    assert not storage.meeting_active


def test_end_meeting_runs_summarizer_after_successful_transcription(tmp_path) -> None:
    class FakeRecorder:
        enabled = True
        status_text = "OBS: подключен"

        def check_connection(self) -> str:
            return self.status_text

        def start_recording(self, meeting_folder):
            del meeting_folder
            return RecorderResult(
                metadata={"recording_status": "recording"},
                message="Запись начата.",
            )

        def stop_recording(self):
            recording_path = tmp_path / "recording.mkv"
            recording_path.touch()
            return RecorderResult(
                metadata={"recording_status": "stopped", "recording_path": str(recording_path)},
                message="Запись остановлена.",
            )

    class FakeAudioExtractor:
        def extract_audio(self, recording_path, meeting_folder):
            del recording_path
            audio_path = meeting_folder / "audio.wav"
            audio_path.touch()
            return {"audio_status": "extracted", "audio_path": str(audio_path)}

    class FakeTranscriber:
        def transcribe(self, audio_path, meeting_folder, progress_callback=None):
            del audio_path, progress_callback
            (meeting_folder / "transcript.json").write_text(
                json.dumps(
                    {"status": "completed", "text": "Обсудили план.", "segments": []},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (meeting_folder / "transcript.md").write_text("Обсудили план.\n", encoding="utf-8")
            return {
                "transcription_status": "completed",
                "transcription_provider": "fake",
                "transcript_path": str(meeting_folder / "transcript.md"),
                "transcript_json_path": str(meeting_folder / "transcript.json"),
                "transcribed_at": "2026-06-01T09:40:00",
            }

    class FakeSummarizer:
        def __init__(self) -> None:
            self.called = False

        def summarize_meeting(self, meeting_folder, metadata):
            self.called = True
            assert metadata["transcription_status"] == "completed"
            (meeting_folder / "summary.md").write_text(
                "# Итоги встречи\n\n## Кратко\n\nОбсудили план.\n",
                encoding="utf-8",
            )
            return {
                "summary_status": "draft_created",
                "summary_provider": "openai",
                "summary_model": "gpt-5.4-mini",
                "summary_path": str(meeting_folder / "summary.md"),
                "summary_generated_at": "2026-06-01T09:41:00",
            }

    summarizer = FakeSummarizer()
    storage = StorageService(
        tmp_path,
        recorder=FakeRecorder(),
        audio_extractor=FakeAudioExtractor(),
        transcriber=FakeTranscriber(),
        summarizer=summarizer,
    )
    storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Summary", datetime(2026, 6, 1, 9, 0))

    storage.end_meeting(datetime(2026, 6, 1, 9, 30))

    metadata = json.loads((meeting_folder / "meeting_metadata.json").read_text(encoding="utf-8"))
    assert summarizer.called
    assert metadata["summary_status"] == "draft_created"
    assert metadata["summary_provider"] == "openai"
    assert storage.last_summary_message == "Итоги подготовлены."
    assert "Обсудили план" in (meeting_folder / "summary.md").read_text(encoding="utf-8")


def test_end_meeting_pipeline_emits_progress_events(tmp_path) -> None:
    class FakeRecorder:
        enabled = False
        status_text = "OBS: выключен в настройках"

        def check_connection(self):
            return self.status_text

        def start_recording(self, meeting_folder):
            del meeting_folder
            return RecorderResult({"recording_status": "disabled"}, "OBS выключен.")

        def stop_recording(self):
            return RecorderResult({"recording_status": "disabled"}, "OBS выключен.")

    storage = StorageService(tmp_path, recorder=FakeRecorder())
    storage.start_workday(datetime(2026, 6, 1, 8, 30))
    storage.start_meeting("Pipeline", datetime(2026, 6, 1, 9, 0))
    events = []

    storage.end_meeting_pipeline(
        datetime(2026, 6, 1, 9, 30),
        progress_callback=lambda event, message: events.append(event),
    )

    assert "meeting_ending" in events
    assert "audio_running" in events
    assert "transcription_running" in events
    assert "summary_running" in events
    assert events[-1] == "meeting_done"


def test_list_today_meeting_folders(tmp_path) -> None:
    storage = StorageService(tmp_path)
    storage.start_workday(datetime(2026, 6, 1, 8, 30))
    first_meeting = storage.start_meeting("Первая встреча", datetime(2026, 6, 1, 9, 0))
    storage.end_meeting(datetime(2026, 6, 1, 9, 30))
    second_meeting = storage.start_meeting("Вторая встреча", datetime(2026, 6, 1, 10, 0))

    assert storage.list_today_meeting_folders(datetime(2026, 6, 1, 12, 0)) == [
        first_meeting,
        second_meeting,
    ]


def test_list_today_meeting_folders_without_day_returns_empty_list(tmp_path) -> None:
    storage = StorageService(tmp_path)

    assert storage.list_today_meeting_folders(datetime(2026, 6, 1, 12, 0)) == []


def test_list_past_workday_folders_excludes_today_and_sorts_newest_first(tmp_path) -> None:
    storage = StorageService(tmp_path)
    today = datetime(2026, 6, 14, 12, 0)
    old_day = storage.create_day_folder(date(2026, 6, 10))
    recent_day = storage.create_day_folder(date(2026, 6, 13))
    today_day = storage.create_day_folder(date(2026, 6, 14))
    storage._write_json(old_day / "day_metadata.json", {"date": "2026-06-10", "status": "ended"})
    storage._write_json(recent_day / "day_metadata.json", {"date": "2026-06-13", "status": "ended"})
    storage._write_json(today_day / "day_metadata.json", {"date": "2026-06-14", "status": "active"})

    assert storage.list_past_workday_folders(today) == [recent_day, old_day]


def test_list_past_workday_folders_skips_non_workday_folders(tmp_path) -> None:
    storage = StorageService(tmp_path)
    (tmp_path / "notes").mkdir()
    day_folder = storage.create_day_folder(date(2026, 6, 12))
    storage._write_json(day_folder / "day_metadata.json", {"date": "2026-06-12", "status": "ended"})

    assert storage.list_past_workday_folders(datetime(2026, 6, 14, 12, 0)) == [day_folder]


def test_read_and_save_meeting_summary_draft(tmp_path) -> None:
    storage = StorageService(tmp_path)
    meeting_folder = tmp_path / "meeting"
    meeting_folder.mkdir()

    placeholder = storage.read_meeting_summary_draft(meeting_folder)
    saved_path = storage.save_meeting_summary_draft(meeting_folder, "# Обновленные итоги\n")

    assert "Итоги встречи" in placeholder
    assert saved_path == meeting_folder / "summary_draft.md"
    assert storage.read_meeting_summary_draft(meeting_folder) == "# Обновленные итоги\n"


def test_read_and_save_meeting_summary_single_file_with_legacy_fallback(tmp_path) -> None:
    storage = StorageService(tmp_path)
    meeting_folder = storage.create_meeting_folder("Новая модель", datetime(2026, 6, 14, 10, 0))

    (meeting_folder / "summary.md").unlink()
    (meeting_folder / "summary_draft.md").write_text("# Старый итог\n", encoding="utf-8")
    assert storage.read_meeting_summary(meeting_folder) == "# Старый итог\n"

    saved_path = storage.save_meeting_summary(meeting_folder, "# Новый итог\n")

    assert saved_path == meeting_folder / "summary.md"
    assert storage.read_meeting_summary(meeting_folder) == "# Новый итог\n"
    assert (meeting_folder / "summary.md").read_text(encoding="utf-8") == "# Новый итог\n"


def test_read_meeting_summary_skips_legacy_placeholder_draft(tmp_path) -> None:
    storage = StorageService(tmp_path)
    meeting_folder = storage.create_meeting_folder("Fallback", datetime(2026, 6, 14, 10, 0))

    (meeting_folder / "summary.md").unlink()
    (meeting_folder / "summary_draft.md").write_text(
        "# Черновик итогов встречи\n\n_Итоги встречи пока не заполнены._\n",
        encoding="utf-8",
    )
    (meeting_folder / "summary_final.md").write_text("# Старый финальный итог\n", encoding="utf-8")

    assert storage.read_meeting_summary(meeting_folder) == "# Старый финальный итог\n"


def test_save_meeting_summary_final_writes_final_without_touching_draft(tmp_path) -> None:
    storage = StorageService(tmp_path)
    meeting_folder = storage.create_meeting_folder("Архив", datetime(2026, 6, 12, 9, 0))
    storage.save_meeting_summary_draft(meeting_folder, "# Черновик\n")

    final_path = storage.save_meeting_summary_final(meeting_folder, "# Финал\n")

    assert final_path == meeting_folder / "summary_final.md"
    assert (meeting_folder / "summary_draft.md").read_text(encoding="utf-8") == "# Черновик\n"
    assert final_path.read_text(encoding="utf-8") == "# Финал\n"


def test_read_and_save_day_summary_and_tasks_drafts(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))

    day_placeholder = storage.read_day_summary_draft(day_folder)
    tasks_placeholder = storage.read_tasks_draft(day_folder)
    storage.save_day_summary_draft(day_folder, "# Итоги дня\n")
    storage.save_tasks_draft(day_folder, "# Задачи\n")

    assert "Итоги дня" in day_placeholder
    assert "Черновик задач" in tasks_placeholder
    assert storage.read_day_summary_draft(day_folder) == "# Итоги дня\n"
    assert storage.read_tasks_draft(day_folder) == "# Задачи\n"


def test_read_and_save_day_summary_single_file_with_legacy_fallback(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 14))

    (day_folder / "00_day_summary_draft.md").write_text("# Старый итог дня\n", encoding="utf-8")
    assert storage.read_day_summary(day_folder) == "# Старый итог дня\n"

    saved_path = storage.save_day_summary(day_folder, "# Новый итог дня\n")

    assert saved_path == day_folder / "00_day_summary.md"
    assert storage.read_day_summary(day_folder) == "# Новый итог дня\n"
    assert (day_folder / "00_day_summary.md").read_text(encoding="utf-8") == "# Новый итог дня\n"


def test_read_day_summary_skips_legacy_placeholder_draft(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 14))

    (day_folder / "00_day_summary_draft.md").write_text(
        "# Черновик итогов дня\n\n_Итоги дня пока не заполнены._\n",
        encoding="utf-8",
    )
    (day_folder / "00_day_summary_final.md").write_text("# Старый финальный итог дня\n", encoding="utf-8")

    assert storage.read_day_summary(day_folder) == "# Старый финальный итог дня\n"


def test_day_summary_exists_detects_legacy_final_file(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 14))

    (day_folder / "00_day_summary_final.md").write_text("# Старый финальный итог дня\n", encoding="utf-8")

    assert storage.day_summary_exists(day_folder)


def test_day_summary_pipeline_includes_missing_summaries_and_skips_without_new_meetings(
    tmp_path,
) -> None:
    class FakeDaySummarizer:
        def __init__(self) -> None:
            self.calls = []

        def summarize_meeting(self, meeting_folder, metadata):
            del meeting_folder, metadata
            return {"summary_status": "disabled"}

        def summarize_day(self, day_folder, current_summary, meeting_summaries):
            self.calls.append(meeting_summaries)
            assert "# Итоги дня" in current_summary
            assert meeting_summaries[0]["summary_source"] == "draft"
            assert meeting_summaries[1]["summary_source"] == "missing"
            (day_folder / "00_day_summary.md").write_text(
                "# Итоги встреч\n\nСводка дня.\n",
                encoding="utf-8",
            )
            return {
                "day_summary_status": "draft_created",
                "day_summary_provider": "openai",
                "day_summary_model": "gpt-5.4-mini",
                "day_summary_path": str(day_folder / "00_day_summary.md"),
                "day_summary_generated_at": "2026-06-01T18:10:00",
            }

    summarizer = FakeDaySummarizer()
    storage = StorageService(tmp_path, summarizer=summarizer)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    first = storage.create_meeting_folder(
        "С summary",
        datetime(2026, 6, 1, 10, 0),
        {"status": "ended", "processing_status": "completed"},
    )
    storage.save_meeting_summary_draft(first, "# Итоги встречи\n\nГотовый summary.\n")
    storage.create_meeting_folder(
        "Без summary",
        datetime(2026, 6, 1, 11, 0),
        {"status": "ended", "processing_status": "completed"},
    )

    storage.process_day_summary_pipeline(day_folder)

    metadata = storage.read_day_summary_metadata(day_folder)
    assert metadata["day_summary_status"] == "draft_created"
    assert len(metadata["included_meetings"]) == 2
    assert metadata["included_meetings"][1]["summary_missing"] is True
    assert len(summarizer.calls) == 1

    storage.process_day_summary_pipeline(day_folder)

    metadata = storage.read_day_summary_metadata(day_folder)
    assert metadata["day_summary_status"] == "up_to_date"
    assert len(summarizer.calls) == 1


def test_day_summary_does_not_use_suspect_transcript_summary(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    meeting_folder = storage.create_meeting_folder(
        "Плохая транскрипция",
        datetime(2026, 6, 1, 9, 0),
        {
            "status": "ended",
            "processing_status": "completed",
            "transcription_quality": "suspect",
            "summary_status": "skipped",
        },
    )
    storage.save_meeting_summary_draft(
        meeting_folder,
        "# Итоги встречи\n\nТранскрипция требует проверки.\n",
    )

    items = storage.collect_day_meeting_summaries(day_folder)

    assert items == [
        {
            "folder": meeting_folder.name,
            "title": "Плохая транскрипция",
            "started_at": "2026-06-01T09:00:00",
            "summary_source": "missing",
            "summary_text": "",
            "summary_missing": True,
        }
    ]


def test_day_summary_metadata_is_recreated_after_corruption(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    metadata_path = storage.day_summary_metadata_path(day_folder)
    metadata_path.write_text('{"day_summary_status": "running",', encoding="utf-8")

    metadata = storage.read_day_summary_metadata(day_folder)

    assert metadata["kind"] == "day_summary"
    assert metadata["title"] == "Итоги дня"
    assert metadata["day_folder"] == "2026-06-01"
    assert metadata["day_summary_status"] == "pending"
    assert metadata["included_meetings"] == []
    assert metadata["pipeline"] == storage._default_day_summary_pipeline()
    assert "__auto_healed" not in metadata
    assert list(day_folder.glob("00_day_summary_metadata.corrupt-*.json"))


def test_day_summary_skips_auto_healed_meeting_metadata(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    good_meeting = storage.create_meeting_folder(
        "Обычная встреча",
        datetime(2026, 6, 1, 10, 0),
        {"status": "ended", "processing_status": "completed"},
    )
    storage.save_meeting_summary_draft(good_meeting, "# Итоги встречи\n\nГотовый summary.\n")
    broken_meeting = storage.create_meeting_folder(
        "Поврежденная встреча",
        datetime(2026, 6, 1, 11, 0),
        {"status": "ended", "processing_status": "completed"},
    )
    storage.write_metadata(broken_meeting, storage._auto_healed_metadata())

    items = storage.collect_day_meeting_summaries(day_folder)

    assert [item["folder"] for item in items] == [good_meeting.name]


def test_day_summary_skips_corrupted_meeting_metadata(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    good_meeting = storage.create_meeting_folder(
        "Обычная встреча",
        datetime(2026, 6, 1, 10, 0),
        {"status": "ended", "processing_status": "completed"},
    )
    storage.save_meeting_summary_draft(
        good_meeting,
        "# Итоги встречи\n\nГотовый summary.\n",
    )
    broken_meeting = storage.create_meeting_folder(
        "Поврежденная встреча",
        datetime(2026, 6, 1, 11, 0),
        {"status": "ended", "processing_status": "completed"},
    )
    (broken_meeting / "meeting_metadata.json").write_text("{", encoding="utf-8")

    items = storage.collect_day_meeting_summaries(day_folder)

    assert [item["folder"] for item in items] == [good_meeting.name]
    assert list(broken_meeting.glob("meeting_metadata.corrupt-*.json"))


def test_day_summary_waits_for_unfinished_meeting_processing(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    storage.create_meeting_folder(
        "В очереди",
        datetime(2026, 6, 1, 10, 0),
        {"status": "ended", "processing_status": "pending"},
    )
    events = []

    storage.process_day_summary_pipeline(
        day_folder,
        progress_callback=lambda event, message: events.append((event, message)),
    )

    metadata = storage.read_day_summary_metadata(day_folder)
    assert metadata["day_summary_status"] == "waiting_for_meetings"
    assert metadata["pipeline"]["collect"] == "active"
    assert events[0][0] == "day_summary_waiting"


def test_save_final_files_preserves_drafts(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    meeting_folder = day_folder / "09-00_review"
    meeting_folder.mkdir()
    storage.save_meeting_summary_draft(meeting_folder, "# Черновик встречи\n")
    storage.save_day_summary_draft(day_folder, "# Черновик дня\n")
    storage.save_tasks_draft(day_folder, "# Черновик задач\n")

    final_paths = storage.save_final_files(
        meeting_folder,
        "# Финальные итоги встречи\n",
        "# Финальные итоги дня\n",
        "# Финальные задачи\n",
    )

    assert final_paths == (
        meeting_folder / "summary_final.md",
        day_folder / "00_day_summary_final.md",
        day_folder / "00_tasks_final.md",
    )
    assert (meeting_folder / "summary_final.md").read_text(encoding="utf-8") == (
        "# Финальные итоги встречи\n"
    )
    assert (day_folder / "00_day_summary_final.md").read_text(encoding="utf-8") == (
        "# Финальные итоги дня\n"
    )
    assert (day_folder / "00_tasks_final.md").read_text(encoding="utf-8") == (
        "# Финальные задачи\n"
    )
    assert (meeting_folder / "summary_draft.md").read_text(encoding="utf-8") == (
        "# Черновик встречи\n"
    )
    assert (day_folder / "00_day_summary_draft.md").read_text(encoding="utf-8") == (
        "# Черновик дня\n"
    )
    assert (day_folder / "00_tasks_draft.md").read_text(encoding="utf-8") == (
        "# Черновик задач\n"
    )

