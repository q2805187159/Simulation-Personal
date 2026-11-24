import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx


class ProviderError(RuntimeError):
    pass


class StructuredOutputError(ProviderError):
    pass


class OutputLengthError(ProviderError):
    pass


@dataclass(frozen=True)
class ProviderResult:
    payload: dict[str, Any]
    model: str


class LLMProvider(Protocol):
    name: str

    async def run(
        self,
        role: str,
        instruction: str,
        context: dict[str, Any],
        fallback: dict[str, Any],
    ) -> ProviderResult: ...


class MockProvider:
    name = "deterministic-mock"

    async def run(
        self,
        role: str,
        instruction: str,
        context: dict[str, Any],
        fallback: dict[str, Any],
    ) -> ProviderResult:
        return ProviderResult(payload=fallback, model=self.name)


class OpenAICompatibleProvider:
    name = "openai-compatible"

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
        self.model = os.environ.get("LLM_MODEL", "")
        self.transport = transport
        self.key_file = os.environ.get("LLM_API_KEY_FILE")

    def _key(self) -> str:
        if not self.key_file:
            return os.environ.get("LLM_API_KEY", "").strip()
        try:
            return Path(self.key_file).read_text(encoding="utf-8").strip()
        except OSError:
            raise ProviderError("后端无法读取模型密钥文件。") from None

    async def _complete(self, response: httpx.Response) -> tuple[str, str, str | None]:
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            await response.aread()
            result = response.json()
            choice = result["choices"][0]
            return (
                choice["message"]["content"],
                result.get("model") or self.model,
                choice.get("finish_reason"),
            )

        parts: list[str] = []
        model = self.model
        finish = None
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            value = line[5:].strip()
            if value == "[DONE]":
                break
            event = json.loads(value)
            if event.get("error"):
                raise ProviderError("模型服务在生成过程中返回错误，可以重试当前操作。")
            model = event.get("model") or model
            choices = event.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                if content := delta.get("content"):
                    parts.append(content)
                finish = choices[0].get("finish_reason") or finish
        if finish is None:
            raise ProviderError("模型响应在完成前中断，可以重试当前操作。")
        return "".join(parts), model, finish

    async def run(
        self,
        role: str,
        instruction: str,
        context: dict[str, Any],
        fallback: dict[str, Any],
    ) -> ProviderResult:
        if not self.base_url or not self.model:
            raise ProviderError("请配置 LLM_BASE_URL 和 LLM_MODEL。")

        headers = {"Content-Type": "application/json"}
        if api_key := self._key():
            headers["Authorization"] = f"Bearer {api_key}"
        max_tokens = 6144
        if role in {"flesh_generator", "flesh_repairer"}:
            max_tokens = min(max_tokens, 256 + 768 * len(context["entries"]))
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": f"职责：{role}。{instruction}"},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }
        stream = self.model.lower().startswith("glm-5")
        if stream:
            body["thinking"] = {"type": "disabled"}
            body["stream"] = True

        try:
            return await self._request(headers, body)
        except StructuredOutputError:
            body["messages"][0]["content"] += (
                "\n仅输出一个合法 JSON 对象。字符串中的引号与换行必须正确转义，不输出解释。"
            )
            return await self._request(headers, body)

    async def _request(self, headers: dict[str, str], body: dict) -> ProviderResult:
        try:
            async with (
                asyncio.timeout(165),
                httpx.AsyncClient(
                    timeout=httpx.Timeout(75, connect=8), transport=self.transport
                ) as client,
            ):
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                ) as response:
                    response.raise_for_status()
                    content, model, finish = await self._complete(response)
            if finish == "length":
                raise OutputLengthError("模型输出超过长度限制，可以重试当前操作。")
            content = content.strip()
            wrapped = re.fullmatch(
                r"```(?:json)?\s*([\s\S]+?)\s*```", content, re.IGNORECASE
            )
            payload = json.loads(wrapped[1] if wrapped else content)
            if not isinstance(payload, dict):
                raise ValueError("expected a JSON object")
            return ProviderResult(payload=payload, model=model)
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"模型服务返回 HTTP {exc.response.status_code}，请检查配置或稍后重试。"
            ) from None
        except (httpx.HTTPError, TimeoutError):
            raise ProviderError("模型服务连接失败或超时，可以重试当前操作。") from None
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            raise StructuredOutputError(
                "模型返回了无效的结构化结果，可以重试当前操作。"
            ) from None


def get_provider() -> LLMProvider:
    name = os.environ.get("LLM_PROVIDER", "mock").lower()
    if name == "openai_compatible":
        return OpenAICompatibleProvider()
    if name == "mock":
        return MockProvider()
    raise ProviderError(f"未知 LLM_PROVIDER：{name}")
