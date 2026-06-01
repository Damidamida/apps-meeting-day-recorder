import json
from datetime import date, datetime

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

