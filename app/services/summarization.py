import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from app.config import DEFAULT_SUMMARY_TEMPLATES
from app.services.ai_errors import ai_error_metadata, is_retryable_ai_error


SUMMARY_PROVIDER = "openai"
SUMMARY_DISABLED_ERROR = "Генерация итогов выключена в настройках."
TRANSCRIPT_NOT_READY_ERROR = "Транскрипт еще не готов."
TRANSCRIPT_EMPTY_ERROR = "Транскрипт пустой. Итоги не будут отправлены во внешний сервис."
TRANSCRIPT_SUSPECT_ERROR = (
    "Транскрипция требует проверки. Итоги не будут отправлены во внешний сервис."
)
SUMMARY_API_KEY_ENV_DEFAULT = "AITUNNEL_KEY"
OPENAI_KEY_MISSING_ERROR = "API key для генерации итогов не найден."
OPENAI_FAILED_ERROR = "Не удалось подготовить черновик итогов через внешний AI endpoint."
SUMMARY_SMOKE_TEXT = "Проверочный текст настройки BK Scribe: встреча создана только для проверки подключения."

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

DAY_SUMMARY_SYSTEM_PROMPT = """Ты готовишь полезную выжимку рабочего дня для project manager.

Ответ должен быть на русском языке и в Markdown.
Используй строго такую структуру:

# Итоги встреч

## Главное за день

## По встречам

## Решения

## Задачи и договоренности

## Риски / вопросы

## Что требует проверки

Правила:
- используй только переданные итоги встреч;
- не выдумывай факты;
- если у встречи отсутствуют итоги, явно укажи это в разделе "По встречам";
- если в текущем черновике уже есть ручные правки, сохрани их смысл и аккуратно дополни новыми встречами;
- не добавляй предупреждения от себя;
- не пиши, что ты AI-модель;
- итог должен быть короткой выжимкой, а не копией всех итогов встреч подряд.
"""

BASE_MEETING_RULES = [
    "ответ должен быть на русском языке и в Markdown",
    "не выдумывай факты",
    "если данных недостаточно, пиши \"Не зафиксировано\"",
    "не добавляй предупреждения от себя",
    "не пиши, что ты AI-модель",
    "итог должен быть пригоден для ручного ревью",
]

BASE_DAY_RULES = [
    "ответ должен быть на русском языке и в Markdown",
    "используй только переданные итоги встреч",
    "не выдумывай факты",
    "если у встречи отсутствуют итоги, явно укажи это",
    "не добавляй предупреждения от себя",
    "не пиши, что ты AI-модель",
    "итог должен быть короткой выжимкой, а не копией всех итогов встреч подряд",
]


class Summarizer(Protocol):
    def summarize_meeting(self, meeting_folder: Path, metadata: dict[str, Any]) -> dict[str, Any]:
        ...

    def summarize_day(
        self,
        day_folder: Path,
        current_summary: str,
        meeting_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...


class NoopSummarizer:
    def summarize_meeting(self, meeting_folder: Path, metadata: dict[str, Any]) -> dict[str, Any]:
        del meeting_folder, metadata
        return disabled_summary_metadata()

    def summarize_day(
        self,
        day_folder: Path,
        current_summary: str,
        meeting_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del day_folder, current_summary, meeting_summaries
        return disabled_day_summary_metadata()


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
        self.retry_attempts = max(0, int(config.get("retry_attempts", 2)))
        self.retry_sleep_seconds = max(0.0, float(config.get("retry_sleep_seconds", 1)))

    def summarize_meeting(self, meeting_folder: Path, metadata: dict[str, Any]) -> dict[str, Any]:
        if not self.config.get("enabled", False):
            return disabled_summary_metadata()
        if metadata.get("transcription_status") != "completed":
            return skipped_summary_metadata()
        if metadata.get("transcription_quality") == "suspect":
            return self._write_suspect_summary(meeting_folder)

        transcript_state = transcript_readiness(meeting_folder)
        if transcript_state["status"] == "suspect":
            return self._write_suspect_summary(meeting_folder)
        transcript = transcript_state.get("text")
        if transcript_state["status"] != "ready" or not transcript:
            return skipped_summary_metadata(transcript_state["message"])

        api_key = load_api_key(
            str(self.config.get("api_key_env") or SUMMARY_API_KEY_ENV_DEFAULT),
            self.config.get("env_file") or "",
        )
        if not api_key:
            return {
                "summary_status": "openai_unavailable",
                "summary_error": OPENAI_KEY_MISSING_ERROR,
            }

        try:
            client_kwargs: dict[str, Any] = {
                "api_key": api_key,
                "timeout": int(self.config.get("timeout_seconds") or 120),
            }
            base_url = str(self.config.get("base_url") or "").strip()
            if base_url:
                client_kwargs["base_url"] = base_url
            client = self.client_factory(
                **client_kwargs,
            )
            summary_text, usage = self._summarize_text(client, transcript)
        except Exception as error:
            return {
                "summary_status": "failed",
                **ai_error_metadata("summary", error, OPENAI_FAILED_ERROR),
            }

        summary_path = meeting_folder / "summary.md"
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

    def _write_suspect_summary(self, meeting_folder: Path) -> dict[str, Any]:
        summary_path = meeting_folder / "summary.md"
        summary_path.write_text(
            "# Итоги встречи\n\n"
            "Транскрипция требует проверки. Итоги не сформированы, "
            "потому что transcript выглядит подозрительно.\n\n"
            "Что можно сделать:\n\n"
            "- открыть transcript и проверить качество распознавания;\n"
            "- повторить обработку встречи после исправления настроек транскрипции;\n"
            "- при необходимости подготовить итоги вручную.\n",
            encoding="utf-8",
        )
        return {
            "summary_status": "skipped",
            "summary_error": TRANSCRIPT_SUSPECT_ERROR,
            "summary_path": str(summary_path),
            "summary_generated_at": self.now().isoformat(),
        }

    def summarize_day(
        self,
        day_folder: Path,
        current_summary: str,
        meeting_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.config.get("enabled", False):
            return disabled_day_summary_metadata()

        api_key = load_api_key(
            str(self.config.get("api_key_env") or SUMMARY_API_KEY_ENV_DEFAULT),
            self.config.get("env_file") or "",
        )
        if not api_key:
            return {
                "day_summary_status": "openai_unavailable",
                "day_summary_error": OPENAI_KEY_MISSING_ERROR,
            }

        try:
            client_kwargs: dict[str, Any] = {
                "api_key": api_key,
                "timeout": int(self.config.get("timeout_seconds") or 120),
            }
            base_url = str(self.config.get("base_url") or "").strip()
            if base_url:
                client_kwargs["base_url"] = base_url
            client = self.client_factory(**client_kwargs)
            response = self._create_response_with_retries(
                client,
                _day_summary_input(current_summary, meeting_summaries),
                self._system_prompt("day"),
            )
            summary_text = extract_response_text(response)
            usage = extract_usage(response)
        except Exception as error:
            return {
                "day_summary_status": "failed",
                **ai_error_metadata(
                    "day_summary",
                    error,
                    "Не удалось подготовить итоги дня через внешний AI endpoint.",
                ),
            }

        summary_path = day_folder / "00_day_summary.md"
        summary_path.write_text(summary_text.rstrip() + "\n", encoding="utf-8")
        result: dict[str, Any] = {
            "day_summary_status": "draft_created",
            "day_summary_provider": SUMMARY_PROVIDER,
            "day_summary_model": str(self.config.get("model") or ""),
            "day_summary_path": str(summary_path),
            "day_summary_generated_at": self.now().isoformat(),
        }
        if usage:
            result["day_summary_usage"] = usage
        return result

    def _summarize_text(self, client: Any, transcript: str) -> tuple[str, dict[str, int]]:
        chunks = split_text(transcript, int(self.config.get("max_chars_per_chunk") or 20000))
        system_prompt = self._system_prompt("meeting")
        if len(chunks) == 1:
            response = self._create_response_with_retries(
                client,
                _meeting_summary_input(chunks[0]),
                system_prompt,
            )
            return extract_response_text(response), extract_usage(response)

        chunk_summaries = []
        total_usage: dict[str, int] = {}
        for index, chunk in enumerate(chunks, start=1):
            response = self._create_response_with_retries(
                client,
                _chunk_summary_input(index, len(chunks), chunk),
                system_prompt,
            )
            chunk_summaries.append(extract_response_text(response))
            total_usage = merge_usage(total_usage, extract_usage(response))

        combined = "\n\n".join(
            f"## Часть {index}\n\n{summary}"
            for index, summary in enumerate(chunk_summaries, start=1)
        )
        response = self._create_response_with_retries(
            client,
            _final_summary_input(combined),
            system_prompt,
        )
        total_usage = merge_usage(total_usage, extract_usage(response))
        return extract_response_text(response), total_usage

    def _create_response_with_retries(
        self,
        client: Any,
        user_input: str,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> Any:
        max_attempts = self.retry_attempts + 1
        for attempt in range(1, max_attempts + 1):
            try:
                return self._create_response(client, user_input, system_prompt)
            except Exception as error:
                if attempt >= max_attempts or not is_retryable_ai_error(error):
                    raise
                if self.retry_sleep_seconds:
                    time.sleep(self.retry_sleep_seconds)
        raise RuntimeError("AI summary retry loop did not return a response.")

    def _create_response(
        self,
        client: Any,
        user_input: str,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> Any:
        return client.responses.create(
            model=str(self.config.get("model") or ""),
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
        )

    def _system_prompt(self, kind: str) -> str:
        return build_summary_system_prompt(self.config, kind)

    @staticmethod
    def _default_client_factory(**kwargs: Any) -> Any:
        from openai import OpenAI

        return OpenAI(**kwargs)


class SummarySmokeResult:
    def __init__(self, ok: bool, message: str) -> None:
        self.ok = ok
        self.message = message


def create_summarizer(config: dict[str, Any]) -> Summarizer:
    if not config.get("enabled", False):
        return NoopSummarizer()
    if config.get("provider") != SUMMARY_PROVIDER:
        return NoopSummarizer()
    return OpenAISummarizer(config)


def smoke_test_summary_connection(
    config: dict[str, Any],
    client_factory: Callable[..., Any] | None = None,
) -> SummarySmokeResult:
    api_key = load_api_key(
        str(config.get("api_key_env") or SUMMARY_API_KEY_ENV_DEFAULT),
        config.get("env_file") or "",
    )
    if not api_key:
        return SummarySmokeResult(False, "Сначала проверьте ключ AI Tunnel.")
    try:
        summarizer = OpenAISummarizer(
            {**config, "enabled": True, "retry_attempts": 0},
            client_factory=client_factory,
        )
        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": int(config.get("timeout_seconds") or 120),
        }
        base_url = str(config.get("base_url") or "").strip()
        if base_url:
            client_kwargs["base_url"] = base_url
        client = summarizer.client_factory(**client_kwargs)
        response = summarizer._create_response(
            client,
            (
                "Ответь одним словом «Готово», если подключение работает.\n\n"
                f"{SUMMARY_SMOKE_TEXT}"
            ),
            "Ты проверяешь подключение BK Scribe. Ответь кратко на русском языке.",
        )
        extract_response_text(response)
    except Exception:
        return SummarySmokeResult(False, "Сервис временно недоступен.")
    return SummarySmokeResult(True, "AI-итоги готовы.")


def build_summary_system_prompt(config: dict[str, Any], kind: str) -> str:
    template = summary_template_from_config(config, kind)
    title = str(template.get("title") or "").strip()
    sections = template.get("sections") or []
    rules = str(template.get("rules") or "").strip()
    if not title:
        title = str(DEFAULT_SUMMARY_TEMPLATES[kind]["title"])

    context = (
        "Ты готовишь полезный черновик итогов встречи для project manager."
        if kind == "meeting"
        else "Ты готовишь полезную выжимку рабочего дня для project manager."
    )
    base_rules = BASE_MEETING_RULES if kind == "meeting" else BASE_DAY_RULES

    lines = [
        context,
        "",
        "Используй строго такую структуру:",
        "",
        f"# {title}",
        "",
    ]
    for section in sections:
        section_title = str(section.get("title") or "").strip()
        if not section_title:
            continue
        lines.append(f"## {section_title}")
        instruction = str(section.get("instruction") or "").strip()
        if instruction:
            lines.append(instruction)
        lines.append("")

    lines.extend(["Базовые правила:"])
    lines.extend(f"- {rule}" for rule in base_rules)
    if rules:
        lines.extend(["", "Дополнительные правила пользователя:"])
        lines.extend(line for line in rules.splitlines() if line.strip())
    return "\n".join(lines).rstrip() + "\n"


def summary_template_from_config(config: dict[str, Any], kind: str) -> dict[str, Any]:
    if kind not in {"meeting", "day"}:
        kind = "meeting"
    templates = config.get("templates")
    if not isinstance(templates, dict):
        return DEFAULT_SUMMARY_TEMPLATES[kind]
    template = templates.get(kind)
    if not isinstance(template, dict):
        return DEFAULT_SUMMARY_TEMPLATES[kind]
    sections = template.get("sections")
    if not isinstance(sections, list) or not sections:
        return DEFAULT_SUMMARY_TEMPLATES[kind]
    normalized_sections = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        if not title:
            continue
        normalized_sections.append(
            {
                "title": title,
                "instruction": str(section.get("instruction") or "").strip(),
            }
        )
    if not normalized_sections:
        return DEFAULT_SUMMARY_TEMPLATES[kind]
    return {
        "title": str(template.get("title") or DEFAULT_SUMMARY_TEMPLATES[kind]["title"]).strip(),
        "sections": normalized_sections,
        "rules": str(template.get("rules") or "").strip()
        or str(DEFAULT_SUMMARY_TEMPLATES[kind].get("rules") or ""),
    }


def disabled_summary_metadata() -> dict[str, str]:
    return {
        "summary_status": "disabled",
        "summary_error": SUMMARY_DISABLED_ERROR,
    }


def disabled_day_summary_metadata() -> dict[str, str]:
    return {
        "day_summary_status": "disabled",
        "day_summary_error": SUMMARY_DISABLED_ERROR,
    }


def skipped_summary_metadata(reason: str = TRANSCRIPT_NOT_READY_ERROR) -> dict[str, str]:
    return {
        "summary_status": "skipped",
        "summary_error": reason,
    }


def summary_message(metadata: dict[str, Any]) -> str:
    status = metadata.get("summary_status")
    if status == "draft_created":
        return "Итоги подготовлены."
    if status == "disabled":
        return "Итоги не подготовлены: генерация итогов выключена в настройках."
    if status == "skipped":
        return f"Итоги не подготовлены: {metadata.get('summary_error') or TRANSCRIPT_NOT_READY_ERROR}"
    if status == "openai_unavailable":
        return "Итоги не подготовлены: API key для генерации итогов не найден."
    if status == "failed":
        return f"Итоги не подготовлены: {metadata.get('summary_error') or OPENAI_FAILED_ERROR}"
    return "Итоги не подготовлены."


def day_summary_message(metadata: dict[str, Any]) -> str:
    status = metadata.get("day_summary_status")
    if status == "draft_created":
        return "Итоги дня подготовлены."
    if status == "disabled":
        return "Итоги дня не подготовлены: генерация итогов выключена в настройках."
    if status == "openai_unavailable":
        return "Итоги дня не подготовлены: API key для генерации итогов не найден."
    if status == "failed":
        return f"Итоги дня не подготовлены: {metadata.get('day_summary_error') or 'не удалось подготовить итоги дня через внешний AI endpoint.'}"
    if status == "waiting_for_meetings":
        return "Итоги дня ожидают завершения обработки встреч."
    if status == "up_to_date":
        return "Итоги дня уже актуальны."
    return "Итоги дня не подготовлены."


def read_transcript_text(meeting_folder: Path) -> str | None:
    state = transcript_readiness(meeting_folder)
    return state.get("text") if state["status"] == "ready" else None


def transcript_readiness(meeting_folder: Path) -> dict[str, str]:
    json_path = meeting_folder / "transcript.json"
    if json_path.is_file():
        return _transcript_json_state(json_path)

    transcript_path = meeting_folder / "transcript.md"
    if not transcript_path.is_file():
        return {"status": "missing", "message": "Транскрипт не готов."}
    text = transcript_path.read_text(encoding="utf-8").strip()
    if not text or _is_placeholder_transcript(text):
        return {"status": "placeholder", "message": "Транскрипт не готов."}
    return {"status": "ready", "message": "Транскрипция завершена.", "text": text}


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


def merge_usage(*items: dict[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        for key in ("input_tokens", "output_tokens"):
            value = item.get(key)
            if value is not None:
                result[key] = result.get(key, 0) + value
    return result


def _transcript_json_state(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "invalid", "message": "Транскрипт не готов."}
    if payload.get("status") != "completed":
        return {"status": "placeholder", "message": "Транскрипт не готов."}

    text = str(payload.get("text") or "").strip()
    if payload.get("quality") == "suspect":
        return {"status": "suspect", "message": TRANSCRIPT_SUSPECT_ERROR}
    if text:
        return {"status": "ready", "message": "Транскрипция завершена.", "text": text}

    segments = payload.get("segments") or []
    segment_lines = [
        str(segment.get("text") or "").strip()
        for segment in segments
        if str(segment.get("text") or "").strip()
    ]
    segment_text = "\n".join(segment_lines).strip()
    if segment_text:
        return {
            "status": "ready",
            "message": "Транскрипция завершена.",
            "text": segment_text,
        }
    return {"status": "empty", "message": TRANSCRIPT_EMPTY_ERROR}


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


def _day_summary_input(current_summary: str, meeting_summaries: list[dict[str, Any]]) -> str:
    sections = []
    for index, item in enumerate(meeting_summaries, start=1):
        title = str(item.get("title") or item.get("folder") or f"Встреча {index}")
        started_at = str(item.get("started_at") or "время не указано")
        source = str(item.get("summary_source") or "missing")
        summary_text = str(item.get("summary_text") or "").strip()
        if summary_text:
            body = summary_text
        else:
            body = "Итоги отсутствуют у этой встречи."
        sections.append(
            f"## {index}. {title}\n"
            f"- Время: {started_at}\n"
            f"- Источник итогов: {source}\n\n"
            f"{body}"
        )

    current = current_summary.strip() or "Текущий черновик итогов дня пустой."
    meetings = "\n\n".join(sections) or "За день нет встреч."
    return (
        "Обнови черновик итогов дня.\n\n"
        "Текущий черновик итогов дня:\n\n"
        f"{current}\n\n"
        "Итоги встреч за день:\n\n"
        f"{meetings}"
    )
