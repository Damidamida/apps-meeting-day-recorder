import json
from datetime import date, datetime

from app.services.recorder import RecorderResult
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

    assert summary_path == meeting_folder / "summary_draft.md"
    assert "Черновик итогов встречи" in summary_path.read_text(encoding="utf-8")


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
    assert (meeting_folder / "summary_draft.md").is_file()
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
    assert (day_folder / "00_day_summary_draft.md").is_file()
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


def test_full_happy_path_still_works(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))
    meeting_folder = storage.start_meeting("Happy path", datetime(2026, 6, 1, 9, 15))

    storage.end_meeting(datetime(2026, 6, 1, 9, 45))
    storage.end_workday(datetime(2026, 6, 1, 18, 0))

    assert (meeting_folder / "meeting_metadata.json").is_file()
    assert (meeting_folder / "transcript.md").is_file()
    assert (meeting_folder / "transcript.json").is_file()
    assert (meeting_folder / "summary_draft.md").is_file()
    assert (day_folder / "00_day_summary_draft.md").is_file()
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
        def transcribe(self, audio_path, meeting_folder):
            del audio_path
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
            (meeting_folder / "summary_draft.md").write_text(
                "# Итоги встречи\n\n## Кратко\n\nОбсудили план.\n",
                encoding="utf-8",
            )
            return {
                "summary_status": "draft_created",
                "summary_provider": "openai",
                "summary_model": "gpt-5.4-mini",
                "summary_path": str(meeting_folder / "summary_draft.md"),
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
    assert storage.last_summary_message == "Черновик итогов подготовлен."
    assert "Обсудили план" in (meeting_folder / "summary_draft.md").read_text(encoding="utf-8")


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


def test_read_and_save_meeting_summary_draft(tmp_path) -> None:
    storage = StorageService(tmp_path)
    meeting_folder = tmp_path / "meeting"
    meeting_folder.mkdir()

    placeholder = storage.read_meeting_summary_draft(meeting_folder)
    saved_path = storage.save_meeting_summary_draft(meeting_folder, "# Обновленные итоги\n")

    assert "Черновик итогов встречи" in placeholder
    assert saved_path == meeting_folder / "summary_draft.md"
    assert storage.read_meeting_summary_draft(meeting_folder) == "# Обновленные итоги\n"


def test_read_and_save_day_summary_and_tasks_drafts(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))

    day_placeholder = storage.read_day_summary_draft(day_folder)
    tasks_placeholder = storage.read_tasks_draft(day_folder)
    storage.save_day_summary_draft(day_folder, "# Итоги дня\n")
    storage.save_tasks_draft(day_folder, "# Задачи\n")

    assert "Черновик итогов дня" in day_placeholder
    assert "Черновик задач" in tasks_placeholder
    assert storage.read_day_summary_draft(day_folder) == "# Итоги дня\n"
    assert storage.read_tasks_draft(day_folder) == "# Задачи\n"


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
            assert "# Черновик итогов дня" in current_summary
            assert meeting_summaries[0]["summary_source"] == "draft"
            assert meeting_summaries[1]["summary_source"] == "missing"
            (day_folder / "00_day_summary_draft.md").write_text(
                "# Итоги встреч\n\nСводка дня.\n",
                encoding="utf-8",
            )
            return {
                "day_summary_status": "draft_created",
                "day_summary_provider": "openai",
                "day_summary_model": "gpt-5.4-mini",
                "day_summary_path": str(day_folder / "00_day_summary_draft.md"),
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

