import json
import subprocess
import sys
import types
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.transcription import (
    AITunnelTranscriber,
    FasterWhisperTranscriber,
    LocalWhisperTranscriber,
    create_transcriber,
    transcript_quality,
)


def _write_pcm_wav(path: Path, duration_seconds: int, sample_rate: int = 16000) -> None:
    frame_count = duration_seconds * sample_rate
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\0\0" * frame_count)


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
        patch(
            "app.services.transcription.hidden_process_kwargs",
            return_value={"creationflags": 123},
        ),
        patch("app.services.transcription.subprocess.run", side_effect=fake_run) as run,
    ):
        transcriber = create_transcriber(
            {
                "backend": "whisper_cli",
                "model": "base",
                "language": "ru",
                "whisper_command": "whisper",
            }
        )
        metadata = transcriber.transcribe(audio_path, tmp_path)

    transcript_json = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    transcript_md = (tmp_path / "transcript.md").read_text(encoding="utf-8")
    assert metadata["transcription_status"] == "completed"
    assert metadata["transcription_provider"] == "local_whisper_cli"
    assert metadata["transcription_quality"] == "ok"
    assert metadata["transcription_quality_warnings"] == []
    assert metadata["transcript_path"] == str(tmp_path / "transcript.md")
    assert metadata["transcript_json_path"] == str(tmp_path / "transcript.json")
    assert "transcribed_at" in metadata
    assert transcript_json == {
        "status": "completed",
        "provider": "local_whisper_cli",
        "text": "Текст встречи",
        "segments": [{"start": 1.2, "end": 5.8, "text": "Первый сегмент"}],
        "quality": "ok",
        "quality_warnings": [],
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
            "ru",
            "--output_format",
            "json",
            "--output_dir",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        creationflags=123,
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


def test_faster_whisper_transcriber_creates_transcript_files(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.touch()

    class FakeSegment:
        start = 0.5
        end = 2.0
        text = " Быстрый сегмент "

    class FakeInfo:
        language = "ru"

    class FakeWhisperModel:
        def __init__(self, model_name, device, compute_type) -> None:
            assert model_name == "base"
            assert device == "cpu"
            assert compute_type == "int8"

        def transcribe(self, audio, language, vad_filter):
            assert audio == str(audio_path)
            assert language == "ru"
            assert vad_filter is True
            return [FakeSegment()], FakeInfo()

    fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
    with patch.dict(sys.modules, {"faster_whisper": fake_module}):
        metadata = FasterWhisperTranscriber().transcribe(audio_path, tmp_path)

    transcript_json = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    transcript_md = (tmp_path / "transcript.md").read_text(encoding="utf-8")
    assert metadata["transcription_status"] == "completed"
    assert metadata["transcription_provider"] == "local_faster_whisper"
    assert metadata["transcription_model"] == "base"
    assert metadata["transcription_vad_filter"] is True
    assert metadata["transcription_quality"] == "ok"
    assert metadata["transcription_quality_warnings"] == []
    assert transcript_json["provider"] == "local_faster_whisper"
    assert transcript_json["text"] == "Быстрый сегмент"
    assert transcript_json["quality"] == "ok"
    assert transcript_json["quality_warnings"] == []
    assert transcript_json["segments"] == [
        {"start": 0.5, "end": 2.0, "text": "Быстрый сегмент"}
    ]
    assert "Быстрый сегмент" in transcript_md


def test_faster_whisper_transcriber_reports_missing_dependency(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.touch()

    with patch.dict(sys.modules, {"faster_whisper": None}):
        metadata = FasterWhisperTranscriber().transcribe(audio_path, tmp_path)

    assert metadata == {
        "transcription_status": "faster_whisper_unavailable",
        "transcription_error": (
            "Локальный faster-whisper недоступен. "
            "Установите optional-зависимость или выберите whisper_cli."
        ),
    }


def test_aitunnel_transcriber_creates_transcript_files(tmp_path: Path, monkeypatch) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio")
    monkeypatch.setenv("AITUNNEL_KEY", "test-aitunnel-key")

    class FakeUsage:
        seconds = 9.2
        cost_rub = 0.18

    class FakeTranscript:
        text = "Внешний transcript"
        usage = FakeUsage()

    class FakeTranscriptions:
        def create(self, **kwargs):
            assert kwargs["model"] == "whisper-large-v3-turbo"
            assert kwargs["language"] == "ru"
            assert kwargs["response_format"] == "json"
            assert kwargs["file"].read() == b"fake-audio"
            return FakeTranscript()

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeClient:
        audio = FakeAudio()

    captured_client_kwargs = {}

    def fake_client_factory(**kwargs):
        captured_client_kwargs.update(kwargs)
        return FakeClient()

    transcriber = AITunnelTranscriber(
        model_name="whisper-large-v3-turbo",
        language="ru",
        api_key_env="AITUNNEL_KEY",
        base_url="https://api.aitunnel.ru/v1/",
        timeout_seconds=180,
        client_factory=fake_client_factory,
    )

    metadata = transcriber.transcribe(audio_path, tmp_path)

    transcript_json = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    transcript_md = (tmp_path / "transcript.md").read_text(encoding="utf-8")
    assert captured_client_kwargs == {
        "api_key": "test-aitunnel-key",
        "base_url": "https://api.aitunnel.ru/v1/",
        "timeout": 180,
    }
    assert metadata["transcription_status"] == "completed"
    assert metadata["transcription_provider"] == "aitunnel"
    assert metadata["transcription_model"] == "whisper-large-v3-turbo"
    assert metadata["transcription_base_url"] == "https://api.aitunnel.ru/v1/"
    assert metadata["transcription_audio_bytes"] == len(b"fake-audio")
    assert metadata["transcription_usage"] == {"seconds": 9.2, "cost_rub": 0.18}
    assert metadata["transcription_quality"] == "ok"
    assert transcript_json["provider"] == "aitunnel"
    assert transcript_json["text"] == "Внешний transcript"
    assert transcript_json["usage"] == {"seconds": 9.2, "cost_rub": 0.18}
    assert "Внешний transcript" in transcript_md
    assert "внешняя транскрипция AI Tunnel" in transcript_md


def test_aitunnel_transcriber_requires_api_key(tmp_path: Path, monkeypatch) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio")
    monkeypatch.delenv("AITUNNEL_KEY", raising=False)

    metadata = AITunnelTranscriber(client_factory=lambda **kwargs: None).transcribe(
        audio_path,
        tmp_path,
    )

    assert metadata == {
        "transcription_status": "aitunnel_unavailable",
        "transcription_error": "API key для внешней транскрипции не найден.",
    }


def test_aitunnel_transcriber_rejects_audio_over_upload_limit(tmp_path: Path, monkeypatch) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"0123456789")
    monkeypatch.setenv("AITUNNEL_KEY", "test-aitunnel-key")

    metadata = AITunnelTranscriber(max_upload_mb=0.000001).transcribe(audio_path, tmp_path)

    assert metadata == {
        "transcription_status": "file_too_large",
        "transcription_error": (
            "Аудиофайл больше лимита внешней транскрипции. "
            "Нужна нарезка аудио на части."
        ),
    }


def test_aitunnel_transcriber_chunks_long_audio_and_reports_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = tmp_path / "audio.wav"
    _write_pcm_wav(audio_path, duration_seconds=5)
    monkeypatch.setenv("AITUNNEL_KEY", "test-aitunnel-key")
    uploaded_names: list[str] = []
    progress: list[tuple[str, str]] = []

    class FakeTranscriptions:
        def create(self, **kwargs):
            chunk_name = Path(kwargs["file"].name).name
            uploaded_names.append(chunk_name)
            return SimpleNamespace(
                text=f"Текст {chunk_name}",
                usage=SimpleNamespace(seconds=2.0, cost_rub=0.26),
            )

    class FakeClient:
        audio = SimpleNamespace(transcriptions=FakeTranscriptions())

    transcriber = AITunnelTranscriber(
        chunking_enabled=True,
        chunk_duration_seconds=2,
        client_factory=lambda **kwargs: FakeClient(),
        now=lambda: SimpleNamespace(isoformat=lambda: "2026-06-06T12:00:00"),
    )

    metadata = transcriber.transcribe(
        audio_path,
        tmp_path,
        progress_callback=lambda event, message: progress.append((event, message)),
    )

    transcript_json = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    transcript_md = (tmp_path / "transcript.md").read_text(encoding="utf-8")
    assert uploaded_names == ["chunk_001.wav", "chunk_002.wav", "chunk_003.wav"]
    assert metadata["transcription_status"] == "completed"
    assert metadata["transcription_mode"] == "chunked"
    assert metadata["transcription_chunk_count"] == 3
    assert metadata["transcription_completed_chunks"] == 3
    assert metadata["transcription_usage"] == {"seconds": 6.0, "cost_rub": 0.78}
    assert transcript_json["mode"] == "chunked"
    assert [chunk["index"] for chunk in transcript_json["chunks"]] == [1, 2, 3]
    assert "Текст chunk_001.wav" in transcript_json["text"]
    assert "Текст chunk_003.wav" in transcript_md
    assert ("transcription_chunk_done", "Выполнено: 1/3 (33%).") in progress
    assert ("transcription_chunk_done", "Выполнено: 3/3 (100%).") in progress


def test_aitunnel_transcriber_retries_temporary_chunk_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = tmp_path / "audio.wav"
    _write_pcm_wav(audio_path, duration_seconds=3)
    monkeypatch.setenv("AITUNNEL_KEY", "test-aitunnel-key")
    attempts = 0
    progress: list[tuple[str, str]] = []

    class FakeAITunnelError(Exception):
        status_code = 502

        def __init__(self) -> None:
            super().__init__("provider unavailable")
            self.body = {
                "error": {
                    "code": 502,
                    "message": "Provider unavailable",
                    "metadata": {"provider_name": "openai"},
                }
            }

    class FakeTranscriptions:
        def create(self, **kwargs):
            del kwargs
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise FakeAITunnelError()
            return SimpleNamespace(text="Текст после retry", usage={})

    class FakeClient:
        audio = SimpleNamespace(transcriptions=FakeTranscriptions())

    transcriber = AITunnelTranscriber(
        chunking_enabled=True,
        chunk_duration_seconds=2,
        retry_attempts=1,
        retry_sleep_seconds=0,
        client_factory=lambda **kwargs: FakeClient(),
    )

    metadata = transcriber.transcribe(
        audio_path,
        tmp_path,
        progress_callback=lambda event, message: progress.append((event, message)),
    )

    assert metadata["transcription_status"] == "completed"
    assert attempts == 3
    assert any(
        event == "transcription_chunk_retry"
        and "Повторяем часть 1/2: попытка 2 из 2." in message
        for event, message in progress
    )


def test_aitunnel_transcriber_retries_transport_error_without_http_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = tmp_path / "audio.wav"
    _write_pcm_wav(audio_path, duration_seconds=3)
    monkeypatch.setenv("AITUNNEL_KEY", "test-aitunnel-key")
    attempts = 0
    progress: list[tuple[str, str]] = []

    class FakeTranscriptions:
        def create(self, **kwargs):
            del kwargs
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("request timed out while uploading audio chunk")
            return SimpleNamespace(text="Текст после transport retry", usage={})

    class FakeClient:
        audio = SimpleNamespace(transcriptions=FakeTranscriptions())

    transcriber = AITunnelTranscriber(
        chunking_enabled=True,
        chunk_duration_seconds=2,
        retry_attempts=1,
        retry_sleep_seconds=0,
        client_factory=lambda **kwargs: FakeClient(),
    )

    metadata = transcriber.transcribe(
        audio_path,
        tmp_path,
        progress_callback=lambda event, message: progress.append((event, message)),
    )

    assert metadata["transcription_status"] == "completed"
    assert attempts == 3
    assert (
        "transcription_chunk_retry",
        "Повторяем часть 1/2: попытка 2 из 2.",
    ) in progress


def test_aitunnel_transcriber_records_unknown_chunk_error_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = tmp_path / "audio.wav"
    _write_pcm_wav(audio_path, duration_seconds=3)
    monkeypatch.setenv("AITUNNEL_KEY", "test-aitunnel-key")
    progress: list[tuple[str, str]] = []

    class FakeTranscriptions:
        def create(self, **kwargs):
            del kwargs
            raise RuntimeError("socket closed while uploading chunk")

    class FakeClient:
        audio = SimpleNamespace(transcriptions=FakeTranscriptions())

    metadata = AITunnelTranscriber(
        chunking_enabled=True,
        chunk_duration_seconds=2,
        retry_attempts=0,
        client_factory=lambda **kwargs: FakeClient(),
    ).transcribe(
        audio_path,
        tmp_path,
        progress_callback=lambda event, message: progress.append((event, message)),
    )

    assert metadata["transcription_status"] == "failed"
    assert metadata["transcription_mode"] == "chunked"
    assert metadata["transcription_failed_chunk"] == 1
    assert metadata["transcription_failed_attempts"] == 1
    assert metadata["transcription_exception_type"] == "RuntimeError"
    assert (
        metadata["transcription_exception_message"]
        == "socket closed while uploading chunk"
    )
    assert metadata["transcription_error"] == (
        "Ошибка на части 1/2: Не удалось выполнить внешнюю транскрипцию через AI Tunnel."
    )
    assert (
        "transcription_chunk_failed",
        "Ошибка на части 1/2: Не удалось выполнить внешнюю транскрипцию через AI Tunnel.",
    ) in progress


def test_aitunnel_transcriber_returns_specific_error_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-audio")
    monkeypatch.setenv("AITUNNEL_KEY", "test-aitunnel-key")

    class FakeAITunnelError(Exception):
        status_code = 402

        def __init__(self) -> None:
            super().__init__("no balance")
            self.body = {
                "error": {
                    "code": 402,
                    "message": "Insufficient balance",
                    "metadata": {"provider_name": "aitunnel"},
                }
            }

    class FakeTranscriptions:
        def create(self, **kwargs):
            del kwargs
            raise FakeAITunnelError()

    class FakeClient:
        audio = SimpleNamespace(transcriptions=FakeTranscriptions())

    metadata = AITunnelTranscriber(
        client_factory=lambda **kwargs: FakeClient(),
    ).transcribe(audio_path, tmp_path)

    assert metadata["transcription_status"] == "failed"
    assert metadata["transcription_error_kind"] == "insufficient_balance"
    assert metadata["transcription_error_code"] == 402
    assert metadata["transcription_error_provider"] == "aitunnel"
    assert metadata["transcription_error"] == (
        "AI Tunnel: недостаточно баланса. Пополните баланс и повторите обработку."
    )


def test_transcript_quality_marks_repeated_long_transcript_as_suspect() -> None:
    segments = [
        {"start": index * 30.0, "end": (index + 1) * 30.0, "text": "ТЕЛЕФОННЫЙ ЗВОНОК"}
        for index in range(40)
    ]

    quality = transcript_quality(" ".join(segment["text"] for segment in segments), segments)

    assert quality["quality"] == "suspect"
    assert "В transcript слишком много одинаковых сегментов." in quality["quality_warnings"]
    assert "Длинная запись дала слишком короткий transcript." in quality["quality_warnings"]


def test_create_transcriber_uses_configured_backend() -> None:
    transcriber = create_transcriber(
        {
            "backend": "faster_whisper",
            "model": "small",
            "language": "ru",
            "device": "cpu",
            "compute_type": "int8",
            "vad_filter": False,
        }
    )

    assert isinstance(transcriber, FasterWhisperTranscriber)
    assert transcriber.model_name == "small"
    assert transcriber.vad_filter is False


def test_create_transcriber_uses_whisper_cli_backend() -> None:
    transcriber = create_transcriber(
        {
            "backend": "whisper_cli",
            "model": "small",
            "language": "ru",
            "whisper_command": "whisper-local",
        }
    )

    assert isinstance(transcriber, LocalWhisperTranscriber)
    assert transcriber.model_name == "small"
    assert transcriber.whisper_command == "whisper-local"


def test_create_transcriber_uses_aitunnel_backend_with_external_default_model() -> None:
    transcriber = create_transcriber(
        {
            "backend": "aitunnel",
            "model": "base",
            "language": "ru",
            "api_key_env": "AITUNNEL_KEY",
            "base_url": "https://api.aitunnel.ru/v1/",
            "env_file": "",
            "timeout_seconds": 180,
            "max_upload_mb": 25,
        }
    )

    assert isinstance(transcriber, AITunnelTranscriber)
    assert transcriber.model_name == "whisper-large-v3-turbo"
    assert transcriber.api_key_env == "AITUNNEL_KEY"
    assert transcriber.base_url == "https://api.aitunnel.ru/v1/"
