import json
import shutil
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


TRANSCRIPTION_PROVIDER = "local_whisper_cli"
FASTER_WHISPER_PROVIDER = "local_faster_whisper"
MISSING_AUDIO_ERROR = "Аудиофайл для транскрипции не найден."
SKIPPED_AUDIO_ERROR = "Аудио еще не извлечено."
WHISPER_UNAVAILABLE_ERROR = (
    "Локальный Whisper недоступен. Установите Whisper CLI или пропустите транскрипцию."
)
WHISPER_FAILED_ERROR = "Не удалось выполнить локальную транскрипцию."
FASTER_WHISPER_UNAVAILABLE_ERROR = (
    "Локальный faster-whisper недоступен. Установите optional-зависимость или выберите whisper_cli."
)
FASTER_WHISPER_FAILED_ERROR = "Не удалось выполнить локальную транскрипцию через faster-whisper."
TRANSCRIPTION_SUSPECT_ERROR = "Транскрипция требует проверки."


class Transcriber(Protocol):
    def transcribe(self, audio_path: str | Path, meeting_folder: Path) -> dict[str, Any]:
        ...


class NoopTranscriber:
    def transcribe(self, audio_path: str | Path, meeting_folder: Path) -> dict[str, Any]:
        del audio_path, meeting_folder
        return skipped_transcription_metadata()


class LocalWhisperTranscriber:
    def __init__(
        self,
        whisper_command: str = "whisper",
        model_name: str = "base",
        language: str = "ru",
    ) -> None:
        self.whisper_command = whisper_command
        self.model_name = model_name
        self.language = language

    def transcribe(self, audio_path: str | Path, meeting_folder: Path) -> dict[str, Any]:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            return {
                "transcription_status": "missing_audio",
                "transcription_error": MISSING_AUDIO_ERROR,
            }
        if shutil.which(self.whisper_command) is None:
            return {
                "transcription_status": "whisper_unavailable",
                "transcription_error": WHISPER_UNAVAILABLE_ERROR,
            }

        try:
            subprocess.run(
                [
                    self.whisper_command,
                    str(audio_path),
                    "--model",
                    self.model_name,
                    "--language",
                    self.language,
                    "--output_format",
                    "json",
                    "--output_dir",
                    str(meeting_folder),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            whisper_result = self._read_whisper_result(audio_path, meeting_folder)
        except (OSError, json.JSONDecodeError, subprocess.CalledProcessError):
            return {
                "transcription_status": "failed",
                "transcription_error": WHISPER_FAILED_ERROR,
            }

        transcript_json_path = meeting_folder / "transcript.json"
        transcript_md_path = meeting_folder / "transcript.md"
        canonical_result = {
            "status": "completed",
            "provider": TRANSCRIPTION_PROVIDER,
            "text": str(whisper_result.get("text", "")).strip(),
            "segments": whisper_result.get("segments") or [],
        }
        quality = transcript_quality(canonical_result["text"], canonical_result["segments"])
        canonical_result.update(quality)
        transcript_json_path.write_text(
            json.dumps(canonical_result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        transcript_md_path.write_text(
            self._render_markdown(canonical_result),
            encoding="utf-8",
        )
        return {
            "transcription_status": "completed",
            "transcription_provider": TRANSCRIPTION_PROVIDER,
            "transcription_quality": quality["quality"],
            "transcription_quality_warnings": quality["quality_warnings"],
            "transcript_path": str(transcript_md_path),
            "transcript_json_path": str(transcript_json_path),
            "transcribed_at": datetime.now().isoformat(),
        }

    def _read_whisper_result(self, audio_path: Path, meeting_folder: Path) -> dict[str, Any]:
        result_path = meeting_folder / f"{audio_path.stem}.json"
        return json.loads(result_path.read_text(encoding="utf-8"))

    @staticmethod
    def _render_markdown(result: dict[str, Any]) -> str:
        text = result.get("text", "")
        segments = result.get("segments") or []
        lines = [
            "# Транскрипт",
            "",
            "_Источник: локальная транскрипция Whisper._",
            "",
        ]
        if result.get("quality") == "suspect":
            lines.extend(
                [
                    "## Требует проверки",
                    "",
                    "Транскрипция выглядит подозрительно и требует ручной проверки.",
                ]
            )
            for warning in result.get("quality_warnings") or []:
                lines.append(f"- {warning}")
            lines.append("")
        if segments:
            lines.extend(["## Сегменты", ""])
            for segment in segments:
                start = _format_seconds(segment.get("start"))
                end = _format_seconds(segment.get("end"))
                segment_text = str(segment.get("text", "")).strip()
                lines.append(f"- [{start} -> {end}] {segment_text}")
        else:
            lines.append(str(text).strip())
        lines.append("")
        return "\n".join(lines)


class FasterWhisperTranscriber:
    def __init__(
        self,
        model_name: str = "base",
        language: str = "ru",
        device: str = "cpu",
        compute_type: str = "int8",
        vad_filter: bool = True,
    ) -> None:
        self.model_name = model_name
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self.vad_filter = vad_filter

    def transcribe(self, audio_path: str | Path, meeting_folder: Path) -> dict[str, Any]:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            return {
                "transcription_status": "missing_audio",
                "transcription_error": MISSING_AUDIO_ERROR,
            }
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return {
                "transcription_status": "faster_whisper_unavailable",
                "transcription_error": FASTER_WHISPER_UNAVAILABLE_ERROR,
            }

        try:
            model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
            segments, info = model.transcribe(
                str(audio_path),
                language=self.language,
                vad_filter=self.vad_filter,
            )
            segment_items = [
                {
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": str(segment.text).strip(),
                }
                for segment in segments
            ]
        except Exception:
            return {
                "transcription_status": "failed",
                "transcription_error": FASTER_WHISPER_FAILED_ERROR,
            }

        text = " ".join(item["text"] for item in segment_items).strip()
        quality = transcript_quality(text, segment_items)
        transcript_json_path = meeting_folder / "transcript.json"
        transcript_md_path = meeting_folder / "transcript.md"
        canonical_result = {
            "status": "completed",
            "provider": FASTER_WHISPER_PROVIDER,
            "model": self.model_name,
            "language": str(getattr(info, "language", self.language) or self.language),
            "text": text,
            "segments": segment_items,
            **quality,
        }
        transcript_json_path.write_text(
            json.dumps(canonical_result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        transcript_md_path.write_text(
            LocalWhisperTranscriber._render_markdown(canonical_result),
            encoding="utf-8",
        )
        return {
            "transcription_status": "completed",
            "transcription_provider": FASTER_WHISPER_PROVIDER,
            "transcription_model": self.model_name,
            "transcription_device": self.device,
            "transcription_compute_type": self.compute_type,
            "transcription_vad_filter": self.vad_filter,
            "transcription_quality": quality["quality"],
            "transcription_quality_warnings": quality["quality_warnings"],
            "transcript_path": str(transcript_md_path),
            "transcript_json_path": str(transcript_json_path),
            "transcribed_at": datetime.now().isoformat(),
        }


def create_transcriber(config: dict[str, Any]) -> Transcriber:
    backend = str(config.get("backend") or "whisper_cli")
    model_name = str(config.get("model") or "base")
    if backend == "faster_whisper":
        return FasterWhisperTranscriber(
            model_name=model_name,
            language=str(config.get("language") or "ru"),
            device=str(config.get("device") or "cpu"),
            compute_type=str(config.get("compute_type") or "int8"),
            vad_filter=bool(config.get("vad_filter", True)),
        )
    return LocalWhisperTranscriber(
        whisper_command=str(config.get("whisper_command") or "whisper"),
        model_name=model_name,
        language=str(config.get("language") or "ru"),
    )


def skipped_transcription_metadata() -> dict[str, str]:
    return {
        "transcription_status": "skipped",
        "transcription_error": SKIPPED_AUDIO_ERROR,
    }


def transcription_message(metadata: dict[str, Any]) -> str:
    status = metadata["transcription_status"]
    if status == "completed":
        if metadata.get("transcription_quality") == "suspect":
            warnings = metadata.get("transcription_quality_warnings") or []
            warning_text = f" {' '.join(warnings)}" if warnings else ""
            return f"{TRANSCRIPTION_SUSPECT_ERROR}{warning_text}"
        return "Транскрипция завершена."
    if status == "skipped":
        return f"Транскрипция пропущена: {metadata['transcription_error']}"
    return f"Транскрипция не выполнена: {metadata['transcription_error']}"


def transcript_quality(text: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    normalized_segments = [
        _normalize_segment_text(str(segment.get("text") or ""))
        for segment in segments
        if _normalize_segment_text(str(segment.get("text") or ""))
    ]
    duration_seconds = _segments_duration_seconds(segments)
    text = text.strip()

    if len(normalized_segments) >= 8:
        most_common_text, most_common_count = Counter(normalized_segments).most_common(1)[0]
        del most_common_text
        if most_common_count >= max(5, int(len(normalized_segments) * 0.35)):
            warnings.append(
                "В transcript слишком много одинаковых сегментов."
            )

    if duration_seconds >= 600 and len(text) < int(duration_seconds * 1.2):
        warnings.append(
            "Длинная запись дала слишком короткий transcript."
        )

    return {
        "quality": "suspect" if warnings else "ok",
        "quality_warnings": warnings,
    }


def _normalize_segment_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _segments_duration_seconds(segments: list[dict[str, Any]]) -> float:
    ends = []
    for segment in segments:
        try:
            ends.append(float(segment.get("end") or 0))
        except (TypeError, ValueError):
            continue
    return max(ends) if ends else 0.0


def _format_seconds(value: Any) -> str:
    seconds = max(0, int(float(value or 0)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
