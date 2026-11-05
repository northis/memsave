from __future__ import annotations

import json

import pytest

from memsave import ai, config


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(payload or {})[:300]

    def json(self):
        return self._payload


class FakeClient:
    response = FakeResponse(200)

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, path, json=None):
        FakeClient.last_request = {"path": path, "json": json, "headers": self.kwargs.get("headers")}
        return FakeClient.response


def test_vision_adapter_parses_openai_envelope(monkeypatch):
    monkeypatch.setattr(config, "VISION_API_KEY", "test-key")
    monkeypatch.setattr(ai.httpx, "Client", FakeClient)
    FakeClient.response = FakeResponse(
        200,
        {
            "choices": [
                {
                    "message": {
                        "content": '{"category":"reaction","description":"Кот удивлён","text":null,"tags":["кот"]}'
                    }
                }
            ]
        },
    )
    answer = ai.recognize_bytes(b"fake-image", "image/jpeg")
    assert answer.category == "reaction"
    assert answer.category_label == "Реакция"
    assert answer.tags == ["кот"]
    assert FakeClient.last_request["path"] == "/chat/completions"
    assert FakeClient.last_request["headers"]["Authorization"] == "Bearer test-key"
    assert FakeClient.last_request["json"]["messages"][0]["role"] == "system"


def test_vision_adapter_marks_429_as_retryable(monkeypatch):
    monkeypatch.setattr(config, "VISION_API_KEY", "test-key")
    monkeypatch.setattr(ai.httpx, "Client", FakeClient)
    FakeClient.response = FakeResponse(429, {"error": "rate limit"})
    with pytest.raises(ai.AiError) as error:
        ai.recognize_bytes(b"fake-image", "image/jpeg")
    assert error.value.reason == "http_429"
    assert error.value.retryable is True


def test_vision_adapter_requires_key(monkeypatch):
    monkeypatch.setattr(config, "VISION_API_KEY", "")
    with pytest.raises(ai.AiError) as error:
        ai.recognize_bytes(b"fake-image", "image/jpeg")
    assert error.value.reason == "no_token"
