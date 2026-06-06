import json
import shutil
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from app.services.summarization import load_api_key


TRANSCRIPTION_PROVIDER = "local_whisper_cli"
FASTER_WHISPER_PROVIDER = "local_faster_whisper"
AITUNNEL_PROVIDER = "aitunnel"
AITUNNEL_TRANSCRIPTION_MODEL_DEFAULT = "whisper-large-v3-turbo"
AITUNNEL_BASE_URL_DEFAULT = "https://api.aitunnel.ru/v1/"
AITUNNEL_API_KEY_ENV_DEFAULT = "AITUNNEL_KEY"
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
AITUNNEL_KEY_MISSING_ERROR = "API key для внешней транскрипции не найден."
AITUNNEL_FAILED_ERROR = "Не удалось выполнить внешнюю транскрипцию через AI Tunnel."
AITUNNEL_FILE_TOO_LARGE_ERROR = (
    "Аудиофайл больше лимита внешней транскрипции. Нужна нарезка аудио на части."
)


class Transcriber(Protocol):
    def transcribe(self, audio_path: str | Path, meeting_folder: Path) -> dict[str, Any]:
        ...


class NoopTranscriber:
    def transcribe(self, audio_path: str | Path, meeting_folder: Path) -> dict[str, Any]:
        del audio_path, meeting_folder
        return skipped_transcription_metadata()


class LocalWhisperTranscriber:
    running_message = "Готовим локальный transcript."

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
    def _render_markdown(
        result: dict[str, Any],
        source_note: str = "локальная транскрипция Whisper",
    ) -> str:
        text = result.get("text", "")
        segments = result.get("segments") or []
        lines = [
            "# Транскрипт",
            "",
            f"_Источник: {source_note}._",
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
    running_message = "Готовим локальный transcript через faster-whisper."

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


class AITunnelTranscriber:
    running_message = "Отправляем audio.wav во внешний сервис транскрипции."

    def __init__(
        self,
        model_name: str = AITUNNEL_TRANSCRIPTION_MODEL_DEFAULT,
        language: str = "ru",
        api_key_env: str = AITUNNEL_API_KEY_ENV_DEFAULT,
        base_url: str = AITUNNEL_BASE_URL_DEFAULT,
        env_file: str = "",
        timeout_seconds: int = 300,
        max_upload_mb: float = 25,
        client_factory: Callable[..., Any] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.model_name = (
            model_name
            if model_name and model_name != "base"
            else AITUNNEL_TRANSCRIPTION_MODEL_DEFAULT
        )
        self.language = language
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.env_file = env_file
        self.timeout_seconds = timeout_seconds
        self.max_upload_mb = max_upload_mb
        self.client_factory = client_factory or self._default_client_factory
        self.now = now or datetime.now

    def transcribe(self, audio_path: str | Path, meeting_folder: Path) -> dict[str, Any]:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            return {
                "transcription_status": "missing_audio",
                "transcription_error": MISSING_AUDIO_ERROR,
            }

        audio_size = audio_path.stat().st_size
        max_bytes = int(float(self.max_upload_mb) * 1024 * 1024)
        if audio_size > max_bytes:
            return {
                "transcription_status": "file_too_large",
                "transcription_error": AITUNNEL_FILE_TOO_LARGE_ERROR,
            }

        api_key = load_api_key(self.api_key_env, self.env_file)
        if not api_key:
            return {
                "transcription_status": "aitunnel_unavailable",
                "transcription_error": AITUNNEL_KEY_MISSING_ERROR,
            }

        try:
            client_kwargs: dict[str, Any] = {
                "api_key": api_key,
                "timeout": int(self.timeout_seconds),
            }
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            client = self.client_factory(**client_kwargs)
            with audio_path.open("rb") as audio_file:
                response = client.audio.transcriptions.create(
                    model=self.model_name,
                    file=audio_file,
                    language=self.language,
                    response_format="json",
                )
            text = _extract_transcription_text(response)
            usage = _extract_transcription_usage(response)
        except Exception:
            return {
                "transcription_status": "failed",
                "transcription_error": AITUNNEL_FAILED_ERROR,
            }

        segment_items: list[dict[str, Any]] = []
        quality = transcript_quality(text, segment_items)
        transcript_json_path = meeting_folder / "transcript.json"
        transcript_md_path = meeting_folder / "transcript.md"
        canonical_result: dict[str, Any] = {
            "status": "completed",
            "provider": AITUNNEL_PROVIDER,
            "model": self.model_name,
            "language": self.language,
            "text": text,
            "segments": segment_items,
            **quality,
        }
        if usage:
            canonical_result["usage"] = usage
        transcript_json_path.write_text(
            json.dumps(canonical_result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        transcript_md_path.write_text(
            LocalWhisperTranscriber._render_markdown(
                canonical_result,
                source_note="внешняя транскрипция AI Tunnel",
            ),
            encoding="utf-8",
        )

        result: dict[str, Any] = {
            "transcription_status": "completed",
            "transcription_provider": AITUNNEL_PROVIDER,
            "transcription_model": self.model_name,
            "transcription_language": self.language,
            "transcription_base_url": self.base_url,
            "transcription_audio_bytes": audio_size,
            "transcription_quality": quality["quality"],
            "transcription_quality_warnings": quality["quality_warnings"],
            "transcript_path": str(transcript_md_path),
            "transcript_json_path": str(transcript_json_path),
            "transcribed_at": self.now().isoformat(),
        }
        if usage:
            result["transcription_usage"] = usage
        return result

    @staticmethod
    def _default_client_factory(**kwargs: Any) -> Any:
        from openai import OpenAI

        return OpenAI(**kwargs)


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
    if backend == "aitunnel":
        return AITunnelTranscriber(
            model_name=model_name,
            language=str(config.get("language") or "ru"),
            api_key_env=str(config.get("api_key_env") or AITUNNEL_API_KEY_ENV_DEFAULT),
            base_url=str(config.get("base_url") or AITUNNEL_BASE_URL_DEFAULT),
            env_file=str(config.get("env_file") or ""),
            timeout_seconds=int(config.get("timeout_seconds") or 300),
            max_upload_mb=float(config.get("max_upload_mb") or 25),
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
        if metadata.get("transcription_provider") == AITUNNEL_PROVIDER:
            return "Транскрипция завершена через AI Tunnel."
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


def _extract_transcription_text(response: Any) -> str:
    if isinstance(response, dict):
        text = response.get("text")
    else:
        text = getattr(response, "text", None)
    return str(text or "").strip()


def _extract_transcription_usage(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        usage = response.get("usage")
    else:
        usage = getattr(response, "usage", None)
    if not usage:
        return {}

    result: dict[str, Any] = {}
    for key in ("seconds", "input_tokens", "output_tokens", "total_tokens", "cost_rub"):
        if isinstance(usage, dict):
            value = usage.get(key)
        else:
            value = getattr(usage, key, None)
        if value is not None:
            result[key] = value
    return result
