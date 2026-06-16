import subprocess
from pathlib import Path
from unittest.mock import patch

from app.services.audio import AudioExtractor


def test_extract_audio_runs_ffmpeg_and_returns_metadata(tmp_path: Path) -> None:
    recording_path = tmp_path / "recording.mkv"
    recording_path.touch()
    meeting_folder = tmp_path / "meeting"
    meeting_folder.mkdir()

    with (
        patch("app.services.audio.shutil.which", return_value="C:/ffmpeg/bin/ffmpeg.exe"),
        patch("app.services.audio.hidden_process_kwargs", return_value={"creationflags": 123}),
        patch("app.services.audio.subprocess.run") as run,
    ):
        metadata = AudioExtractor().extract_audio(recording_path, meeting_folder)

    assert metadata["audio_status"] == "extracted"
    assert metadata["audio_path"] == str(meeting_folder / "audio.wav")
    assert "audio_extracted_at" in metadata
    run.assert_called_once_with(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(recording_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(meeting_folder / "audio.wav"),
        ],
        check=True,
        capture_output=True,
        text=True,
        creationflags=123,
    )


def test_audio_extractor_prefers_bundled_ffmpeg(tmp_path: Path) -> None:
    recording_path = tmp_path / "recording.mkv"
    recording_path.touch()
    meeting_folder = tmp_path / "meeting"
    meeting_folder.mkdir()
    bundled_ffmpeg = tmp_path / "resources" / "ffmpeg" / "ffmpeg.exe"
    bundled_ffmpeg.parent.mkdir(parents=True)
    bundled_ffmpeg.write_text("fake exe", encoding="utf-8")

    with (
        patch("app.services.audio.bundled_tool_path", return_value=bundled_ffmpeg),
        patch("app.services.audio.shutil.which", return_value=None),
        patch("app.services.audio.subprocess.run") as run,
    ):
        metadata = AudioExtractor().extract_audio(recording_path, meeting_folder)

    assert metadata["audio_status"] == "extracted"
    assert run.call_args.args[0][0] == str(bundled_ffmpeg)


def test_extract_audio_reports_missing_recording(tmp_path: Path) -> None:
    metadata = AudioExtractor().extract_audio(tmp_path / "missing.mkv", tmp_path)

    assert metadata == {
        "audio_status": "missing_recording",
        "audio_error": "Файл записи не найден.",
    }


def test_extract_audio_reports_unavailable_ffmpeg(tmp_path: Path) -> None:
    recording_path = tmp_path / "recording.mkv"
    recording_path.touch()

    with patch("app.services.audio.shutil.which", return_value=None):
        metadata = AudioExtractor().extract_audio(recording_path, tmp_path)

    assert metadata == {
        "audio_status": "ffmpeg_unavailable",
        "audio_error": "FFmpeg недоступен. Установите FFmpeg и добавьте его в PATH.",
    }


def test_extract_audio_reports_ffmpeg_failure(tmp_path: Path) -> None:
    recording_path = tmp_path / "recording.mkv"
    recording_path.touch()

    with (
        patch("app.services.audio.shutil.which", return_value="C:/ffmpeg/bin/ffmpeg.exe"),
        patch(
            "app.services.audio.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["ffmpeg"]),
        ),
    ):
        metadata = AudioExtractor().extract_audio(recording_path, tmp_path)

    assert metadata == {
        "audio_status": "failed",
        "audio_error": "Не удалось извлечь аудио через FFmpeg.",
    }
