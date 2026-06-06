from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from backend.config import settings

FAST_MODEL = settings.ollama_fast_model
SMART_MODEL = settings.ollama_smart_model


class OllamaOfflineError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Make sure Ollama is running: ollama serve")


@dataclass
class OllamaClient:
    base_url: str = settings.ollama_base_url
    timeout_seconds: float = 180.0
    retries: int = 3
    retry_backoff_seconds: float = 2.0

    async def generate(self, prompt: str, model: str = FAST_MODEL, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self._temperature_for(prompt, json_mode)},
        }
        if json_mode:
            payload["format"] = "json"
        data = await self._post_with_retries("/api/generate", payload, model)
        self._log_usage(endpoint="/api/generate", model=model, data=data)
        return str(data.get("response", "")).strip()

    async def chat(self, messages: list[dict], model: str, json_mode: bool = False) -> str:
        content = "\n".join(str(message.get("content", "")) for message in messages)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self._temperature_for(content, json_mode)},
        }
        if json_mode:
            payload["format"] = "json"
        data = await self._post_with_retries("/api/chat", payload, model)
        self._log_usage(endpoint="/api/chat", model=model, data=data)
        message = data.get("message", {})
        if isinstance(message, dict):
            return str(message.get("content", "")).strip()
        return str(data.get("response", "")).strip()

    async def _post_with_retries(self, endpoint: str, payload: dict[str, Any], model: str) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{endpoint}"
        last_error: BaseException | None = None
        for attempt in range(1, self.retries + 1):
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(url, json=payload)
                    response.raise_for_status()
                    data = response.json()
                data["_client_latency_seconds"] = time.perf_counter() - started
                return data
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(self.retry_backoff_seconds)
                    continue
                raise OllamaOfflineError() from exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {502, 503, 504}:
                    last_error = exc
                    if attempt < self.retries:
                        await asyncio.sleep(self.retry_backoff_seconds)
                        continue
                    raise OllamaOfflineError() from exc
                raise RuntimeError(f"Ollama request failed for {model}: {exc.response.text}") from exc
        raise OllamaOfflineError() from last_error

    @staticmethod
    def _temperature_for(prompt: str, json_mode: bool) -> float:
        prompt_lower = prompt.lower()
        creative_markers = (
            "cover letter",
            "cold email",
            "networking email",
            "professional but conversational",
            "creative",
        )
        scoring_markers = (
            "ats",
            "score",
            "classification",
            "classify",
            "confidence",
            "extract top",
            "required vs preferred",
        )
        if any(marker in prompt_lower for marker in creative_markers):
            return 0.7
        if json_mode or any(marker in prompt_lower for marker in scoring_markers):
            return 0.3
        return 0.7

    @staticmethod
    def _log_usage(endpoint: str, model: str, data: dict[str, Any]) -> None:
        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        completion_tokens = int(data.get("eval_count") or 0)
        total_tokens = prompt_tokens + completion_tokens
        latency = float(data.get("_client_latency_seconds") or 0.0)
        ollama_duration = int(data.get("total_duration") or 0) / 1_000_000_000
        print(
            "[ollama] "
            f"endpoint={endpoint} model={model} "
            f"prompt_tokens={prompt_tokens} completion_tokens={completion_tokens} "
            f"total_tokens={total_tokens} latency={latency:.2f}s ollama_duration={ollama_duration:.2f}s"
        )


ollama_client = OllamaClient()
