import asyncio
import json

import httpx
import pytest

from app.provider import OpenAICompatibleProvider, ProviderError


def test_glm_reads_secret_file_and_requests_bounded_json(monkeypatch, tmp_path):
    key = tmp_path / "llm"
    key.write_text("local-test-key\n", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY_FILE", str(key))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("LLM_MODEL", "glm-5.3-flash")

    def respond(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer local-test-key"
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["max_tokens"] > 0
        assert body["response_format"] == {"type": "json_object"}
        assert body["stream"] is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text='data: {"model":"glm-5.3-flash","choices":[{"delta":{"content":"{\\"ok\\":"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"true}"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n",
        )

    provider = OpenAICompatibleProvider(transport=httpx.MockTransport(respond))
    result = asyncio.run(provider.run("test", "json", {}, {}))
    assert result.payload == {"ok": True}
    assert result.model == "glm-5.3-flash"


def test_provider_error_reports_status_without_echoing_response(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("LLM_MODEL", "glm-5.3-flash")
    monkeypatch.delenv("LLM_API_KEY_FILE", raising=False)
    provider = OpenAICompatibleProvider(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text="private upstream diagnostic")
        )
    )
    with pytest.raises(ProviderError, match="401") as error:
        asyncio.run(provider.run("test", "json", {}, {}))
    assert "private upstream diagnostic" not in str(error.value)


def test_provider_accepts_one_fenced_json_object(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("LLM_MODEL", "glm-5.3-flash")
    monkeypatch.delenv("LLM_API_KEY_FILE", raising=False)
    provider = OpenAICompatibleProvider(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '```json\n{"answer": "ok"}\n```'}}
                    ]
                },
            )
        )
    )
    assert asyncio.run(provider.run("test", "json", {}, {})).payload == {"answer": "ok"}


def test_provider_retries_invalid_structured_output_once(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("LLM_MODEL", "glm-5.3-flash")
    monkeypatch.delenv("LLM_API_KEY_FILE", raising=False)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        content = "文本不能解析为 JSON" if len(requests) == 1 else '{"answer":"ok"}'
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    provider = OpenAICompatibleProvider(transport=httpx.MockTransport(respond))
    result = asyncio.run(
        provider.run("persona_qa", "json", {"event_ids": ["work"]}, {})
    )
    assert result.payload == {"answer": "ok"}
    assert len(requests) == 2
    assert requests[0]["messages"][1] == requests[1]["messages"][1]
    assert "JSON" in requests[1]["messages"][0]["content"]
