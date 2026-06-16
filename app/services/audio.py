import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.runtime import bundled_tool_path
from app.services.subprocess_utils import hidden_process_kwargs


class AudioExtractor:
    def __init__(self, ffmpeg_command: str = "ffmpeg") -> None:
        self.ffmpeg_command = ffmpeg_command

    def extract_audio(self, recording_path: str | Path, meeting_folder: Path) -> dict[str, Any]:
        recording_path = Path(recording_path)
        if not recording_path.is_file():
            return {
                "audio_status": "missing_recording",
                "audio_error": "Файл записи не найден.",
            }
        ffmpeg_command = self._resolved_ffmpeg_command()
        if ffmpeg_command is None:
            return {
                "audio_status": "ffmpeg_unavailable",
                "audio_error": "FFmpeg недоступен. Установите FFmpeg и добавьте его в PATH.",
            }

        audio_path = meeting_folder / "audio.wav"
        try:
            subprocess.run(
                [
                    ffmpeg_command,
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
                    str(audio_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
                **hidden_process_kwargs(),
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return {
                "audio_status": "failed",
                "audio_error": "Не удалось извлечь аудио через FFmpeg.",
            }
        return {
            "audio_status": "extracted",
            "audio_path": str(audio_path),
            "audio_extracted_at": datetime.now().isoformat(),
        }

    def _resolved_ffmpeg_command(self) -> str | None:
        bundled_ffmpeg = bundled_tool_path("ffmpeg.exe")
        if bundled_ffmpeg.is_file():
            return str(bundled_ffmpeg)
        if shutil.which(self.ffmpeg_command) is None:
            return None
        return self.ffmpeg_command


def skipped_audio_metadata() -> dict[str, str]:
    return {
        "audio_status": "skipped",
        "audio_error": "Путь к записи отсутствует.",
    }
