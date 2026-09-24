from __future__ import annotations

import json
import re
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.utils.errors import StorageError

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(content: str) -> dict[str, Any]:
    """Extract a JSON object from plain text or a Markdown fenced response."""

    candidates: list[str] = []
    fenced = _JSON_FENCE_RE.findall(content)
    candidates.extend(item.strip() for item in fenced)
    candidates.append(content.strip())

    object_match = _JSON_OBJECT_RE.search(content)
    if object_match:
        candidates.append(object_match.group(0))

    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise StorageError("LLM response does not contain a valid JSON object")


class JsonLLMClient(ABC):
    model: str

    def close(self) -> None:
        """Release adapter-owned resources when present."""

    @abstractmethod
    def request_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def request_typed(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[Any],
    ) -> Any:
        raw = self.request_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        try:
            return response_model.model_validate(raw)
        except ValidationError as exc:
            raise StorageError(f"LLM response validation failed: {exc}") from exc


class OpenAICompatibleClient(JsonLLMClient):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        client: httpx.Client | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = (base_url or self.settings.llm_base_url).rstrip("/")
        if not self.base_url:
            raise ValueError("LLM_BASE_URL is not configured")
        self.api_key = api_key or self.settings.llm_api_key.get_secret_value()
        self.model = model or self.settings.llm_model
        if not self.model:
            raise ValueError("LLM_MODEL is not configured")
        self._owns_client = client is None
        self.max_retries = max(0, self.settings.llm_max_retries)
        self.retry_backoff_seconds = max(
            0.0,
            self.settings.llm_retry_backoff_seconds,
        )
        self.client = client or httpx.Client(
            timeout=timeout_seconds or self.settings.llm_timeout_seconds
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _chat_endpoint(self) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def request_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        response: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.post(
                    self._chat_endpoint(),
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                retryable = status_code in {408, 429} or status_code >= 500
                if not retryable or attempt >= self.max_retries:
                    raise StorageError(f"LLM request failed: {exc}") from exc
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise StorageError(f"LLM request failed: {exc}") from exc
            if self.retry_backoff_seconds:
                time.sleep(self.retry_backoff_seconds * (2**attempt))

        if response is None:  # pragma: no cover - defensive invariant
            raise StorageError("LLM request failed without a response")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except Exception as exc:
            raise StorageError(f"LLM response parsing failed: {exc}") from exc
        if not isinstance(content, str):
            raise StorageError("LLM returned a non-text response")
        return extract_json_object(content)


class CodexExecClient(JsonLLMClient):
    """Adapter that asks a local Codex CLI process for one JSON response."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.executable = executable or self.settings.codex_exec_path
        self.model = model or self.settings.llm_model or "codex-default"
        self.timeout_seconds = (
            timeout_seconds or self.settings.codex_exec_timeout_seconds
        )

    def request_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        prompt = (
            f"{system_prompt.strip()}\n\n"
            "只输出一个合法 JSON 对象，不要输出 Markdown 代码围栏或额外解释。\n\n"
            f"{user_prompt.strip()}"
        )
        command = [
            self.executable,
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-",
        ]
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise StorageError(
                f"codex exec timed out after {self.timeout_seconds}s"
            ) from exc
        except OSError as exc:
            raise StorageError(f"cannot start codex exec: {exc}") from exc

        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "").strip()
            raise StorageError(
                f"codex exec failed with exit code {completed.returncode}: "
                f"{message[-1000:]}"
            )
        return extract_json_object(completed.stdout)


def build_llm_client(settings: Settings | None = None) -> JsonLLMClient:
    settings = settings or get_settings()
    if settings.llm_adapter == "openai_compatible":
        return OpenAICompatibleClient(settings=settings)
    if settings.llm_adapter == "codex_exec":
        return CodexExecClient(settings=settings)
    raise ValueError(f"unsupported LLM_ADAPTER: {settings.llm_adapter}")
