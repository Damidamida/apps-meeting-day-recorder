from dataclasses import dataclass
from typing import Any


RETRYABLE_AI_ERROR_CODES = {408, 429, 502}

AI_TUNNEL_ERROR_MESSAGES = {
    400: (
        "bad_request",
        "AI Tunnel: неверный запрос. Проверьте модель, формат и параметры.",
    ),
    401: (
        "invalid_api_key",
        "AI Tunnel: API key недействителен. Проверьте ключ в .env.local.",
    ),
    402: (
        "insufficient_balance",
        "AI Tunnel: недостаточно баланса. Пополните баланс и повторите обработку.",
    ),
    403: (
        "moderation_blocked",
        "AI Tunnel: запрос заблокирован модерацией.",
    ),
    408: (
        "timeout",
        "AI Tunnel: запрос превысил время ожидания. Можно повторить обработку.",
    ),
    429: (
        "rate_limited",
        "AI Tunnel: превышен лимит запросов. Подождите и повторите обработку.",
    ),
    502: (
        "provider_unavailable",
        "AI Tunnel: модель или провайдер временно недоступны. Повторите позже "
        "или выберите другую модель.",
    ),
}


@dataclass(frozen=True)
class AIServiceErrorInfo:
    code: int | None
    kind: str | None
    message: str
    provider: str | None = None


def ai_error_metadata(
    prefix: str,
    error: BaseException,
    fallback_message: str,
) -> dict[str, Any]:
    info = parse_ai_service_error(error, fallback_message)
    metadata: dict[str, Any] = {f"{prefix}_error": info.message}
    if info.code is not None and info.kind is not None:
        metadata[f"{prefix}_error_code"] = info.code
        metadata[f"{prefix}_error_kind"] = info.kind
    if info.provider:
        metadata[f"{prefix}_error_provider"] = info.provider
    return metadata


def is_retryable_ai_error(error: BaseException) -> bool:
    info = parse_ai_service_error(error, "")
    return info.code in RETRYABLE_AI_ERROR_CODES


def parse_ai_service_error(
    error: BaseException,
    fallback_message: str,
) -> AIServiceErrorInfo:
    payload = _extract_payload(error)
    details = _error_details(payload)
    code = _extract_code(error, details)
    provider = _extract_provider(details)
    if code in AI_TUNNEL_ERROR_MESSAGES:
        kind, message = AI_TUNNEL_ERROR_MESSAGES[code]
        return AIServiceErrorInfo(code=code, kind=kind, message=message, provider=provider)
    return AIServiceErrorInfo(code=code, kind=None, message=fallback_message, provider=provider)


def _extract_payload(error: BaseException) -> Any:
    body = getattr(error, "body", None)
    if body:
        return body
    response = getattr(error, "response", None)
    if response is not None:
        try:
            return response.json()
        except Exception:
            return None
    return getattr(error, "error", None)


def _error_details(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        details = payload.get("error")
        if isinstance(details, dict):
            return details
        return payload
    return {}


def _extract_code(error: BaseException, details: dict[str, Any]) -> int | None:
    for value in (
        details.get("code"),
        details.get("status_code"),
        getattr(error, "status_code", None),
        getattr(error, "code", None),
    ):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_provider(details: dict[str, Any]) -> str | None:
    metadata = details.get("metadata")
    if isinstance(metadata, dict):
        provider = metadata.get("provider_name") or metadata.get("provider")
        if provider:
            return str(provider)
    provider = details.get("provider")
    return str(provider) if provider else None
