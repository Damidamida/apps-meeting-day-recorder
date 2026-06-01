import json
from datetime import date, datetime

import pytest

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
    assert "not implemented yet" in summary_path.read_text(encoding="utf-8")


def test_start_workday_creates_day_metadata(tmp_path) -> None:
    storage = StorageService(tmp_path)

    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))

    metadata = json.loads((day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    assert metadata == {
        "date": "2026-06-01",
        "started_at": "2026-06-01T08:30:00",
        "status": "active",
        "meetings": [],
    }
    assert storage.workday_active


def test_start_workday_does_not_overwrite_existing_metadata(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 1))
    metadata_path = day_folder / "day_metadata.json"
    metadata_path.write_text('{"status": "ended"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="already ended"):
        storage.start_workday(datetime(2026, 6, 1, 8, 30))

    assert json.loads(metadata_path.read_text(encoding="utf-8")) == {"status": "ended"}


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
    assert (meeting_folder / "transcript.md").is_file()
    assert (meeting_folder / "transcript.json").is_file()
    assert (meeting_folder / "summary_draft.md").is_file()
    assert day_metadata["meetings"] == [{"folder": "09-15_Planning", **metadata}]
    assert not storage.meeting_active


def test_end_workday_creates_day_drafts(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.start_workday(datetime(2026, 6, 1, 8, 30))

    storage.end_workday(datetime(2026, 6, 1, 18, 0))

    metadata = json.loads((day_folder / "day_metadata.json").read_text(encoding="utf-8"))
    assert metadata["ended_at"] == "2026-06-01T18:00:00"
    assert metadata["status"] == "ended"
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

