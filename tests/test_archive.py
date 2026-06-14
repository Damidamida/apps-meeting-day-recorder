from datetime import date, datetime

from app.services.archive import ArchiveDateFilter, build_archive_days, search_archive
from app.services.storage import StorageService


def test_build_archive_days_returns_statuses_and_meetings(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 12))
    storage._write_json(day_folder / "day_metadata.json", {"date": "2026-06-12", "status": "ended"})
    meeting = storage.create_meeting_folder("Планирование релиза", datetime(2026, 6, 12, 9, 30))
    storage.save_day_summary_draft(day_folder, "# Итоги дня\n")
    metadata = storage.read_meeting_metadata(meeting)
    metadata.update({"status": "ended", "processing_status": "completed"})
    storage.write_metadata(meeting, metadata)

    days = build_archive_days(storage, now=datetime(2026, 6, 14, 12, 0))

    assert len(days) == 1
    assert days[0].folder == day_folder
    assert days[0].workday == date(2026, 6, 12)
    assert days[0].meeting_count == 1
    assert days[0].status_label == "Итоги готовы"
    assert days[0].meetings[0].title == "Планирование релиза"


def test_archive_date_filter_limits_days(tmp_path) -> None:
    storage = StorageService(tmp_path)
    old_day = storage.create_day_folder(date(2026, 5, 20))
    recent_day = storage.create_day_folder(date(2026, 6, 12))
    storage._write_json(old_day / "day_metadata.json", {"date": "2026-05-20", "status": "ended"})
    storage._write_json(recent_day / "day_metadata.json", {"date": "2026-06-12", "status": "ended"})

    days = build_archive_days(
        storage,
        now=datetime(2026, 6, 14, 12, 0),
        date_filter=ArchiveDateFilter(start=date(2026, 6, 1), end=date(2026, 6, 14)),
    )

    assert [day.folder for day in days] == [recent_day]


def test_search_archive_finds_title_summary_day_summary_and_transcript(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 12))
    storage._write_json(day_folder / "day_metadata.json", {"date": "2026-06-12", "status": "ended"})
    storage.save_day_summary_draft(day_folder, "Общие итоги про релиз")
    meeting = storage.create_meeting_folder("Планирование", datetime(2026, 6, 12, 9, 30))
    storage.save_meeting_summary_draft(meeting, "Риски релиза")
    (meeting / "transcript.md").write_text("Обсудили релиз и метрики", encoding="utf-8")

    days = build_archive_days(storage, now=datetime(2026, 6, 14, 12, 0))
    matches = search_archive(days, "релиз")

    assert {match.kind for match in matches} >= {
        "Итоги дня",
        "Итоги встречи",
        "Транскрипт",
    }
    assert all(match.day_folder == day_folder for match in matches)


def test_search_archive_finds_single_summary_files(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 12))
    storage._write_json(day_folder / "day_metadata.json", {"date": day_folder.name, "status": "ended"})
    storage.save_day_summary(day_folder, "Дневной релиз найден")
    meeting = storage.create_meeting_folder("План", datetime(2026, 6, 12, 10, 0))
    storage.save_meeting_summary(meeting, "Встреча про релиз найдена")

    days = build_archive_days(storage, now=datetime(2026, 6, 14, 12, 0))
    matches = search_archive(days, "релиз")

    assert {match.kind for match in matches} >= {"Итоги дня", "Итоги встречи"}


def test_build_archive_days_survives_corrupted_meeting_metadata(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 12))
    storage._write_json(day_folder / "day_metadata.json", {"date": "2026-06-12", "status": "ended"})
    meeting = storage.create_meeting_folder("Поврежденная встреча", datetime(2026, 6, 12, 9, 30))
    (meeting / "meeting_metadata.json").write_text("{", encoding="utf-8")

    days = build_archive_days(storage, now=datetime(2026, 6, 14, 12, 0))

    assert len(days) == 1
    assert days[0].status_label == "Требует внимания"


def test_search_archive_skips_non_utf8_files(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 12))
    storage._write_json(day_folder / "day_metadata.json", {"date": "2026-06-12", "status": "ended"})
    storage.save_day_summary_draft(day_folder, "Обсуждали релиз продукта")
    meeting = storage.create_meeting_folder("Архив", datetime(2026, 6, 12, 9, 30))
    (meeting / "transcript.md").write_bytes(b"\xff\xfe\xfa")

    days = build_archive_days(storage, now=datetime(2026, 6, 14, 12, 0))
    matches = search_archive(days, "релиз")

    assert len(matches) == 1
    assert matches[0].kind == "Итоги дня"


def test_search_archive_snippet_is_compact_for_long_markdown(tmp_path) -> None:
    storage = StorageService(tmp_path)
    day_folder = storage.create_day_folder(date(2026, 6, 12))
    storage._write_json(day_folder / "day_metadata.json", {"date": "2026-06-12", "status": "ended"})
    meeting = storage.create_meeting_folder("Архив", datetime(2026, 6, 12, 9, 30))
    long_text = (
        "# Длинные итоги\n\n"
        + "Очень подробный контекст без совпадения. " * 8
        + "Обсудили релиз продукта и план выката. "
        + "Еще один длинный блок markdown с деталями, ссылками и списками. " * 8
    )
    storage.save_meeting_summary_draft(meeting, long_text)

    days = build_archive_days(storage, now=datetime(2026, 6, 14, 12, 0))
    matches = search_archive(days, "релиз")

    assert len(matches) == 1
    assert len(matches[0].snippet) <= 96
    assert matches[0].snippet.startswith("...")
    assert matches[0].snippet.endswith("...")
