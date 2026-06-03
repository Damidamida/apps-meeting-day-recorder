import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.services.summarization import (
    OPENAI_KEY_MISSING_ERROR,
    OpenAISummarizer,
    load_api_key,
    read_transcript_text,
    split_text,
)


def _summary_config(**overrides):
    config = {
        "enabled": True,
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "",
        "env_file": "",
        "timeout_seconds": 120,
        "max_chars_per_chunk": 20000,
    }
    config.update(overrides)
    return config


def _write_completed_transcript(meeting_folder: Path, text: str = "Обсудили план проекта.") -> None:
    (meeting_folder / "transcript.json").write_text(
        json.dumps({"status": "completed", "text": text, "segments": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_disabled_summary_does_not_call_openai(tmp_path: Path) -> None:
    def forbidden_client(**kwargs):
        raise AssertionError("OpenAI client should not be created")

    summarizer = OpenAISummarizer(
        _summary_config(enabled=False),
        client_factory=forbidden_client,
    )

    metadata = summarizer.summarize_meeting(tmp_path, {"transcription_status": "completed"})

    assert metadata == {
        "summary_status": "disabled",
        "summary_error": "Генерация итогов выключена в настройках.",
    }


def test_missing_api_key_returns_openai_unavailable_without_printing_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _write_completed_transcript(tmp_path)

    summarizer = OpenAISummarizer(_summary_config())

    metadata = summarizer.summarize_meeting(tmp_path, {"transcription_status": "completed"})

    assert metadata == {
        "summary_status": "openai_unavailable",
        "summary_error": OPENAI_KEY_MISSING_ERROR,
    }
    assert "test-secret" not in str(metadata)


def test_not_ready_transcript_is_skipped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    (tmp_path / "transcript.json").write_text(
        json.dumps({"status": "placeholder", "segments": []}),
        encoding="utf-8",
    )

    summarizer = OpenAISummarizer(_summary_config())

    metadata = summarizer.summarize_meeting(tmp_path, {"transcription_status": "completed"})

    assert metadata == {
        "summary_status": "skipped",
        "summary_error": "Транскрипт еще не готов.",
    }


def test_successful_summary_generation_writes_draft_and_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    _write_completed_transcript(tmp_path)
    response = SimpleNamespace(
        output_text=(
            "# Итоги встречи\n\n"
            "## Кратко\n\nОбсудили план проекта.\n\n"
            "## Обсуждалось\n\nПлан проекта.\n\n"
            "## Решения\n\nНе зафиксировано\n\n"
            "## Задачи\n\n- исполнитель не указан, срок не указан: проверить план.\n\n"
            "## Риски / вопросы\n\nНе зафиксировано\n\n"
            "## Требует проверки\n\nПлан проекта.\n"
        ),
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: response))
    client_kwargs = {}

    summarizer = OpenAISummarizer(
        _summary_config(base_url="https://api.proxyapi.ru/openai/v1"),
        client_factory=lambda **kwargs: client_kwargs.update(kwargs) or client,
        now=lambda: datetime(2026, 6, 3, 12, 0),
    )

    metadata = summarizer.summarize_meeting(tmp_path, {"transcription_status": "completed"})

    summary_text = (tmp_path / "summary_draft.md").read_text(encoding="utf-8")
    assert metadata["summary_status"] == "draft_created"
    assert metadata["summary_provider"] == "openai"
    assert metadata["summary_model"] == "gpt-5.4-mini"
    assert metadata["summary_path"] == str(tmp_path / "summary_draft.md")
    assert metadata["summary_generated_at"] == "2026-06-03T12:00:00"
    assert metadata["summary_usage"] == {"input_tokens": 100, "output_tokens": 50}
    assert client_kwargs["base_url"] == "https://api.proxyapi.ru/openai/v1"
    assert client_kwargs["api_key"] == "test-secret"
    assert "# Итоги встречи" in summary_text
    assert "## Задачи" in summary_text
    assert "test-secret" not in str(metadata)


def test_api_failure_returns_failed_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    _write_completed_transcript(tmp_path)

    def failing_client(**kwargs):
        raise RuntimeError("boom")

    summarizer = OpenAISummarizer(_summary_config(), client_factory=failing_client)

    metadata = summarizer.summarize_meeting(tmp_path, {"transcription_status": "completed"})

    assert metadata == {
        "summary_status": "failed",
        "summary_error": "Не удалось подготовить черновик итогов через OpenAI.",
    }


def test_env_file_parser_reads_plain_and_quoted_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_path = tmp_path / ".env.local"

    env_path.write_text("OPENAI_API_KEY=test-plain\n", encoding="utf-8")
    assert load_api_key("OPENAI_API_KEY", env_path) == "test-plain"

    env_path.write_text('OPENAI_API_KEY="test-double"\n', encoding="utf-8")
    assert load_api_key("OPENAI_API_KEY", env_path) == "test-double"

    env_path.write_text("OPENAI_API_KEY='test-single'\n", encoding="utf-8")
    assert load_api_key("OPENAI_API_KEY", env_path) == "test-single"

    assert load_api_key("OPENAI_API_KEY", tmp_path / "missing.env") is None


def test_chunking_splits_long_transcript_and_combines_final_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    _write_completed_transcript(tmp_path, "строка 1\nстрока 2\nстрока 3\nстрока 4\n")
    calls = []

    def create_response(**kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            return SimpleNamespace(
                output_text=f"Конспект части {len(calls)}",
                usage=SimpleNamespace(input_tokens=10 * len(calls), output_tokens=5 * len(calls)),
            )
        return SimpleNamespace(
            output_text="# Итоги встречи\n\n## Кратко\n\nСводный итог.",
            usage=SimpleNamespace(input_tokens=30, output_tokens=15),
        )

    client = SimpleNamespace(responses=SimpleNamespace(create=create_response))
    summarizer = OpenAISummarizer(
        _summary_config(max_chars_per_chunk=18),
        client_factory=lambda **kwargs: client,
    )

    metadata = summarizer.summarize_meeting(tmp_path, {"transcription_status": "completed"})

    assert metadata["summary_status"] == "draft_created"
    assert metadata["summary_usage"] == {"input_tokens": 60, "output_tokens": 30}
    assert len(calls) == 3
    assert "Сводный итог" in (tmp_path / "summary_draft.md").read_text(encoding="utf-8")


def test_read_transcript_prefers_completed_json_segments(tmp_path: Path) -> None:
    (tmp_path / "transcript.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "text": "",
                "segments": [{"text": "Первый сегмент"}, {"text": "Второй сегмент"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert read_transcript_text(tmp_path) == "Первый сегмент\nВторой сегмент"


def test_split_text_handles_long_line() -> None:
    assert split_text("abcdef", 2) == ["ab", "cd", "ef"]
