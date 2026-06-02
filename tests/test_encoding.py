import json
from datetime import datetime
from pathlib import Path

from app.services.audio import AudioExtractor
from app.services.storage import StorageService
from app.services.transcription import LocalWhisperTranscriber


MOJIBAKE_MARKERS = ("Рџ", "Рў", "Рђ", "Рќ", "РЎ", "Р“", "вЂ", "Гђ", "Г‘")
TEXT_FILES_TO_CHECK = (
    Path("AGENTS.md"),
    Path("PROJECT_STATE.md"),
    Path("README.md"),
    Path("config.yaml.example"),
    Path("app/services/audio.py"),
    Path("app/services/recorder.py"),
    Path("app/services/storage.py"),
    Path("app/services/transcription.py"),
    Path("app/ui/main_window.py"),
)


def test_russian_meeting_title_and_folder_are_readable(tmp_path: Path) -> None:
    storage = StorageService(tmp_path)
    storage.start_workday(datetime(2026, 6, 3, 9, 0))

    meeting_folder = storage.start_meeting(
        "Тест русского названия",
        datetime(2026, 6, 3, 9, 15),
    )
    metadata = json.loads((meeting_folder / "meeting_metadata.json").read_text(encoding="utf-8"))

    assert metadata["title"] == "Тест русского названия"
    assert "Тест_русского_названия" in meeting_folder.name
    assert not _has_mojibake(meeting_folder.name)


def test_placeholder_transcript_has_readable_russian_title(tmp_path: Path) -> None:
    meeting_folder = tmp_path / "meeting"
    meeting_folder.mkdir()

    path = StorageService(tmp_path).write_placeholder_transcript(meeting_folder)

    assert path.read_text(encoding="utf-8").startswith("# Транскрипт")


def test_audio_and_transcription_errors_are_readable_russian(tmp_path: Path) -> None:
    audio_metadata = AudioExtractor().extract_audio(tmp_path / "missing.mkv", tmp_path)
    transcription_metadata = LocalWhisperTranscriber().transcribe(
        tmp_path / "missing.wav",
        tmp_path,
    )

    assert audio_metadata["audio_error"] == "Файл записи не найден."
    assert transcription_metadata["transcription_error"] == (
        "Аудиофайл для транскрипции не найден."
    )
    assert not _has_mojibake(audio_metadata["audio_error"])
    assert not _has_mojibake(transcription_metadata["transcription_error"])


def test_project_russian_text_files_do_not_contain_common_mojibake_markers() -> None:
    for path in TEXT_FILES_TO_CHECK:
        text = path.read_text(encoding="utf-8")
        assert not _has_mojibake(text), f"mojibake marker found in {path}"


def _has_mojibake(text: str) -> bool:
    return any(marker in text for marker in MOJIBAKE_MARKERS)
