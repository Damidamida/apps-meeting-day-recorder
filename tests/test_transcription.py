import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from app.services.transcription import LocalWhisperTranscriber


def test_local_whisper_transcriber_creates_transcript_files(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.touch()

    def fake_run(*args, **kwargs):
        del args, kwargs
        (tmp_path / "audio.json").write_text(
            json.dumps(
                {
                    "text": "Текст встречи",
                    "segments": [
                        {"start": 1.2, "end": 5.8, "text": "Первый сегмент"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    with (
        patch("app.services.transcription.shutil.which", return_value="C:/tools/whisper.exe"),
        patch("app.services.transcription.subprocess.run", side_effect=fake_run) as run,
    ):
        metadata = LocalWhisperTranscriber().transcribe(audio_path, tmp_path)

    transcript_json = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    transcript_md = (tmp_path / "transcript.md").read_text(encoding="utf-8")
    assert metadata["transcription_status"] == "completed"
    assert metadata["transcription_provider"] == "local_whisper_cli"
    assert metadata["transcript_path"] == str(tmp_path / "transcript.md")
    assert metadata["transcript_json_path"] == str(tmp_path / "transcript.json")
    assert "transcribed_at" in metadata
    assert transcript_json == {
        "status": "completed",
        "provider": "local_whisper_cli",
        "text": "Текст встречи",
        "segments": [{"start": 1.2, "end": 5.8, "text": "Первый сегмент"}],
    }
    assert "# Транскрипт" in transcript_md
    assert "Первый сегмент" in transcript_md
    run.assert_called_once_with(
        [
            "whisper",
            str(audio_path),
            "--model",
            "base",
            "--language",
            "Russian",
            "--output_format",
            "json",
            "--output_dir",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_local_whisper_transcriber_reports_missing_audio(tmp_path: Path) -> None:
    metadata = LocalWhisperTranscriber().transcribe(tmp_path / "missing.wav", tmp_path)

    assert metadata == {
        "transcription_status": "missing_audio",
        "transcription_error": "Аудиофайл для транскрипции не найден.",
    }


def test_local_whisper_transcriber_reports_unavailable_whisper(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.touch()

    with patch("app.services.transcription.shutil.which", return_value=None):
        metadata = LocalWhisperTranscriber().transcribe(audio_path, tmp_path)

    assert metadata == {
        "transcription_status": "whisper_unavailable",
        "transcription_error": (
            "Локальный Whisper недоступен. Установите Whisper CLI или пропустите транскрипцию."
        ),
    }


def test_local_whisper_transcriber_reports_cli_failure(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.touch()

    with (
        patch("app.services.transcription.shutil.which", return_value="C:/tools/whisper.exe"),
        patch(
            "app.services.transcription.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["whisper"]),
        ),
    ):
        metadata = LocalWhisperTranscriber().transcribe(audio_path, tmp_path)

    assert metadata == {
        "transcription_status": "failed",
        "transcription_error": "Не удалось выполнить локальную транскрипцию.",
    }
