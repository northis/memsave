"""OpenAI-compatible vision client, mirroring the duosave adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from . import config
from .prompt import AiAnswer, build_request, parse_answer


@dataclass
class AiError(RuntimeError):
    reason: str
    detail: str = ""
    retryable: bool = False

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


def available() -> bool:
    return bool(config.VISION_API_KEY.strip())


def _base_url() -> str:
    base = config.VISION_BASE_URL.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")].rstrip("/")
    return base


def recognize_bytes(data: bytes, media_type: str = "image/jpeg") -> AiAnswer:
    if not available():
        raise AiError("no_token", "MEMSAVE_VISION_API_KEY is not set", retryable=False)
    if len(data) > config.VISION_MAX_MB * 1024 * 1024:
        raise AiError("too_large", "image is too large for the vision request", retryable=False)

    payload = build_request(data, media_type, config.VISION_MODEL)
    headers = {
        "Authorization": f"Bearer {config.VISION_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(
            base_url=_base_url(),
            timeout=config.VISION_TIMEOUT,
            headers=headers,
        ) as client:
            response = client.post("/chat/completions", json=payload)
    except httpx.TimeoutException as exc:
        raise AiError("timeout", str(exc), retryable=True) from exc
    except httpx.TransportError as exc:
        raise AiError("transport", str(exc), retryable=True) from exc

    if response.status_code in (401, 402, 403):
        raise AiError(f"http_{response.status_code}", response.text[:300], retryable=False)
    if response.status_code == 429:
        raise AiError("http_429", response.text[:300], retryable=True)
    if response.status_code >= 500:
        raise AiError(f"http_{response.status_code}", response.text[:300], retryable=True)
    if response.status_code >= 400:
        raise AiError(f"http_{response.status_code}", response.text[:300], retryable=False)

    try:
        envelope = response.json()
        content = envelope["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AiError("bad_shape", "unexpected response envelope", retryable=False) from exc
    if not isinstance(content, str):
        raise AiError("bad_shape", "message content is not text", retryable=False)

    try:
        return parse_answer(content, config.VISION_MODEL)
    except ValueError as exc:
        raise AiError("bad_json", str(exc), retryable=False) from exc


def raw_json(answer: AiAnswer) -> str:
    return json.dumps(
        {
            "category": answer.category,
            "description": answer.description,
            "text": answer.text,
            "tags": answer.tags,
            "model": answer.model,
        },
        ensure_ascii=False,
    )
