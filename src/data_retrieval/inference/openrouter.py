from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OpenRouterError(RuntimeError):
    """Raised when OpenRouter cannot produce a valid structured response."""


class _OpenRouterHttpError(OpenRouterError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class OpenRouterJsonClient:
    """Small dependency-free client for strict OpenRouter structured outputs."""

    api_key: str = field(repr=False)
    model: str = "openai/gpt-5.6-luna"
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 120.0
    reasoning_effort: str = "low"
    max_output_tokens: int = 4_000
    max_retries: int = 5
    data_collection: str = "deny"

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("OpenRouter API key cannot be empty")
        if not self.model.strip():
            raise ValueError("OpenRouter model cannot be empty")
        if not self.base_url.strip():
            raise ValueError("OpenRouter base URL cannot be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.reasoning_effort not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise ValueError("unsupported reasoning_effort")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.data_collection not in {"allow", "deny"}:
            raise ValueError("data_collection must be 'allow' or 'deny'")

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            "reasoning": {"effort": self.reasoning_effort, "exclude": True},
            "provider": {
                "require_parameters": True,
                "data_collection": self.data_collection,
            },
            "max_tokens": self.max_output_tokens,
        }
        last_error: OpenRouterError | None = None
        for attempt in range(self.max_retries + 1):
            response = self._post_with_retries("/chat/completions", payload)
            try:
                choice = response["choices"][0]
                message = choice["message"]
                refusal = message.get("refusal")
                if refusal:
                    raise OpenRouterError(
                        f"OpenRouter model refused the request: {refusal}"
                    )
                content = message["content"]
                if not isinstance(content, str):
                    raise TypeError("message content must be text")
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise TypeError("structured response must be a JSON object")
                return parsed
            except OpenRouterError:
                raise
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
                last_error = OpenRouterError(
                    "OpenRouter returned an invalid structured response"
                )
                if attempt >= self.max_retries:
                    raise last_error from error
                time.sleep(min(2**attempt, 8))
        assert last_error is not None
        raise last_error

    def _post_with_retries(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: OpenRouterError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._post_json(path, payload)
            except OpenRouterError as error:
                last_error = error
                if isinstance(error, _OpenRouterHttpError) and not error.retryable:
                    raise
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2**attempt, 8))
        assert last_error is not None
        raise last_error

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{path}"
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            retryable = error.code == 429 or error.code >= 500
            message = f"OpenRouter HTTP {error.code}: {detail[:500]}"
            raise _OpenRouterHttpError(message, retryable=retryable) from error
        except (URLError, TimeoutError) as error:
            raise OpenRouterError(f"cannot reach OpenRouter at {url}: {error}") from error
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            raise OpenRouterError("OpenRouter returned non-JSON data") from error
        if not isinstance(parsed, dict):
            raise OpenRouterError("OpenRouter response must be a JSON object")
        return parsed
