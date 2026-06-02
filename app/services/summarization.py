import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol


SUMMARY_PROVIDER = "openai"
SUMMARY_DISABLED_ERROR = "Генерация итогов выключена в настройках."
TRANSCRIPT_NOT_READY_ERROR = "Транскрипт еще не готов."
OPENAI_KEY_MISSING_ERROR = "OpenAI API key не найден."
OPENAI_FAILED_ERROR = "Не удалось подготовить черновик итогов через OpenAI."

SUMMARY_TEMPLATE = """# Итоги встречи

## Кратко

## Обсуждалось

## Решения

## Задачи

## Риски / вопросы

## Требует проверки
"""

SYSTEM_PROMPT = """Ты готовишь полезный черновик итогов встречи для project manager.

Ответ должен быть на русском языке и в Markdown.
Используй строго такую структуру:

# Итоги встречи

## Кратко

## Обсуждалось

## Решения

## Задачи

## Риски / вопросы

## Требует проверки

Правила:
- не выдумывай факты;
- если данных недостаточно, пиши "Не зафиксировано";
- задачи формулируй как action items;
- если исполнитель или срок не указаны, явно пиши "исполнитель не указан" и "срок не указан";
- сохраняй смысл технических терминов;
- не добавляй предупреждения от себя;
- не пиши, что ты AI-модель;
- итог должен быть пригоден для ручного ревью.
"""


class Summarizer(Protocol):
    def summarize_meeting(self, meeting_folder: Path, metadata: dict[str, Any]) -> dict[str, Any]:
        ...


class NoopSummarizer:
    def summarize_meeting(self, meeting_folder: Path, metadata: dict[str, Any]) -> dict[str, Any]:
        del meeting_folder, metadata
        return disabled_summary_metadata()


class OpenAISummarizer:
    def __init__(
        self,
        config: dict[str, Any],
        client_factory: Callable[..., Any] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.client_factory = client_factory or self._default_client_factory
        self.now = now or datetime.now

    def summarize_meeting(self, meeting_folder: Path, metadata: dict[str, Any]) -> dict[str, Any]:
        if not self.config.get("enabled", False):
            return disabled_summary_metadata()
        if metadata.get("transcription_status") != "completed":
            return skipped_summary_metadata()

        transcript = read_transcript_text(meeting_folder)
        if transcript is None:
            return skipped_summary_metadata()

        api_key = load_api_key(
            str(self.config.get("api_key_env") or "OPENAI_API_KEY"),
            self.config.get("env_file") or "",
        )
        if not api_key:
            return {
                "summary_status": "openai_unavailable",
                "summary_error": OPENAI_KEY_MISSING_ERROR,
            }

        try:
            client = self.client_factory(
                api_key=api_key,
                timeout=int(self.config.get("timeout_seconds") or 120),
            )
            summary_text, usage = self._summarize_text(client, transcript)
        except Exception:
            return {
                "summary_status": "failed",
                "summary_error": OPENAI_FAILED_ERROR,
            }

        summary_path = meeting_folder / "summary_draft.md"
        summary_path.write_text(summary_text.rstrip() + "\n", encoding="utf-8")
        result: dict[str, Any] = {
            "summary_status": "draft_created",
            "summary_provider": SUMMARY_PROVIDER,
            "summary_model": str(self.config.get("model") or ""),
            "summary_path": str(summary_path),
            "summary_generated_at": self.now().isoformat(),
        }
        if usage:
            result["summary_usage"] = usage
        return result

    def _summarize_text(self, client: Any, transcript: str) -> tuple[str, dict[str, int]]:
        chunks = split_text(transcript, int(self.config.get("max_chars_per_chunk") or 20000))
        if len(chunks) == 1:
            response = self._create_response(client, _meeting_summary_input(chunks[0]))
            return extract_response_text(response), extract_usage(response)

        chunk_summaries = []
        for index, chunk in enumerate(chunks, start=1):
            response = self._create_response(client, _chunk_summary_input(index, len(chunks), chunk))
            chunk_summaries.append(extract_response_text(response))

        combined = "\n\n".join(
            f"## Часть {index}\n\n{summary}"
            for index, summary in enumerate(chunk_summaries, start=1)
        )
        response = self._create_response(client, _final_summary_input(combined))
        return extract_response_text(response), extract_usage(response)

    def _create_response(self, client: Any, user_input: str) -> Any:
        return client.responses.create(
            model=str(self.config.get("model") or ""),
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_input},
            ],
        )

    @staticmethod
    def _default_client_factory(**kwargs: Any) -> Any:
        from openai import OpenAI

        return OpenAI(**kwargs)


def create_summarizer(config: dict[str, Any]) -> Summarizer:
    if not config.get("enabled", False):
        return NoopSummarizer()
    if config.get("provider") != SUMMARY_PROVIDER:
        return NoopSummarizer()
    return OpenAISummarizer(config)


def disabled_summary_metadata() -> dict[str, str]:
    return {
        "summary_status": "disabled",
        "summary_error": SUMMARY_DISABLED_ERROR,
    }


def skipped_summary_metadata() -> dict[str, str]:
    return {
        "summary_status": "skipped",
        "summary_error": TRANSCRIPT_NOT_READY_ERROR,
    }


def summary_message(metadata: dict[str, Any]) -> str:
    status = metadata.get("summary_status")
    if status == "draft_created":
        return "Черновик итогов подготовлен."
    if status == "disabled":
        return "Итоги не подготовлены: генерация итогов выключена в настройках."
    if status == "skipped":
        return "Итоги не подготовлены: транскрипт еще не готов."
    if status == "openai_unavailable":
        return "Итоги не подготовлены: OpenAI API key не найден."
    if status == "failed":
        return "Итоги не подготовлены: не удалось подготовить черновик итогов через OpenAI."
    return "Итоги не подготовлены."


def read_transcript_text(meeting_folder: Path) -> str | None:
    json_text = _read_transcript_json(meeting_folder / "transcript.json")
    if json_text:
        return json_text

    transcript_path = meeting_folder / "transcript.md"
    if not transcript_path.is_file():
        return None
    text = transcript_path.read_text(encoding="utf-8").strip()
    if not text or _is_placeholder_transcript(text):
        return None
    return text


def load_api_key(api_key_env: str, env_file: str | Path | None = None) -> str | None:
    key = os.environ.get(api_key_env)
    if key:
        return key.strip()
    if not env_file:
        return None

    env_path = Path(env_file).expanduser()
    if not env_path.is_file():
        return None

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() != api_key_env:
            continue
        return value.strip().strip('"').strip("'") or None
    return None


def split_text(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in text.splitlines(keepends=True):
        if current and current_length + len(line) > max_chars:
            chunks.append("".join(current).strip())
            current = []
            current_length = 0
        if len(line) > max_chars:
            chunks.extend(_split_long_line(line, max_chars))
            continue
        current.append(line)
        current_length += len(line)
    if current:
        chunks.append("".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text).strip()

    output = getattr(response, "output", None) or []
    parts: list[str] = []
    for item in output:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                parts.append(str(text))
    text = "\n".join(parts).strip()
    if text:
        return text
    raise ValueError("OpenAI response does not contain text output.")


def extract_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}

    input_tokens = _usage_value(usage, "input_tokens")
    output_tokens = _usage_value(usage, "output_tokens")
    result: dict[str, int] = {}
    if input_tokens is not None:
        result["input_tokens"] = input_tokens
    if output_tokens is not None:
        result["output_tokens"] = output_tokens
    return result


def _read_transcript_json(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if payload.get("status") != "completed":
        return None

    text = str(payload.get("text") or "").strip()
    if text:
        return text

    segments = payload.get("segments") or []
    segment_lines = [
        str(segment.get("text") or "").strip()
        for segment in segments
        if str(segment.get("text") or "").strip()
    ]
    return "\n".join(segment_lines).strip() or None


def _is_placeholder_transcript(text: str) -> bool:
    return "Транскрипция пока не реализована" in text or "placeholder" in text.lower()


def _split_long_line(line: str, max_chars: int) -> list[str]:
    chunks = []
    start = 0
    while start < len(line):
        chunks.append(line[start : start + max_chars].strip())
        start += max_chars
    return [chunk for chunk in chunks if chunk]


def _usage_value(usage: Any, key: str) -> int | None:
    if isinstance(usage, dict):
        value = usage.get(key)
    else:
        value = getattr(usage, key, None)
    return int(value) if value is not None else None


def _meeting_summary_input(transcript: str) -> str:
    return f"Подготовь черновик итогов этой встречи по транскрипту:\n\n{transcript}"


def _chunk_summary_input(index: int, total: int, chunk: str) -> str:
    return (
        f"Подготовь краткий промежуточный конспект части {index} из {total}. "
        "Не делай финальные итоги всей встречи, только извлеки факты, решения, задачи, риски и вопросы из этой части.\n\n"
        f"{chunk}"
    )


def _final_summary_input(chunk_summaries: str) -> str:
    return (
        "Подготовь финальный черновик итогов одной встречи на основе промежуточных конспектов частей.\n\n"
        f"{chunk_summaries}"
    )
