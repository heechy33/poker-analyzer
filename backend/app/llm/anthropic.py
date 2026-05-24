"""Async Anthropic SDK wrapper used by the hand-analysis pipeline.

The wrapper exposes two entry points:

* :meth:`AnthropicClient.analyze` — single-shot non-streaming completion.
* :meth:`AnthropicClient.stream_analyze` — async iterator yielding
  :class:`StreamChunk` objects (one per ``text`` delta, plus a final
  ``final`` chunk with usage data).

Both call the same ``messages`` endpoint; tests substitute a lightweight
fake (see :class:`LLMClient` protocol) via FastAPI dependency override.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from anthropic import AsyncAnthropic


@dataclass
class AnalysisResult:
    """Final result of a Claude analysis call."""

    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class StreamChunk:
    """One event emitted while streaming."""

    type: str
    text: str = ""
    result: AnalysisResult | None = None


class LLMClient(Protocol):
    """Minimal interface the analysis router depends on."""

    model: str

    async def analyze(
        self, *, system: str, user_prompt: str
    ) -> AnalysisResult:  # pragma: no cover - protocol only
        ...

    def stream_analyze(
        self, *, system: str, user_prompt: str
    ) -> AsyncIterator[StreamChunk]:  # pragma: no cover - protocol only
        ...


class AnthropicClient:
    """Concrete Anthropic-backed implementation of :class:`LLMClient`."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 1024,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._client = client if client is not None else AsyncAnthropic(api_key=api_key)

    async def analyze(
        self, *, system: str, user_prompt: str
    ) -> AnalysisResult:
        message = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = _join_text_blocks(message.content)
        return AnalysisResult(
            text=text,
            model=self.model,
            input_tokens=_safe_token(message, "input_tokens"),
            output_tokens=_safe_token(message, "output_tokens"),
        )

    async def stream_analyze(
        self, *, system: str, user_prompt: str
    ) -> AsyncIterator[StreamChunk]:
        async with self._client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield StreamChunk(type="token", text=text)
            final = await stream.get_final_message()
        full_text = _join_text_blocks(final.content)
        yield StreamChunk(
            type="final",
            result=AnalysisResult(
                text=full_text,
                model=self.model,
                input_tokens=_safe_token(final, "input_tokens"),
                output_tokens=_safe_token(final, "output_tokens"),
            ),
        )


def _join_text_blocks(content: Any) -> str:
    if not content:
        return ""
    out: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "")
            if text:
                out.append(text)
    return "".join(out)


def _safe_token(message: Any, attr: str) -> int:
    usage = getattr(message, "usage", None)
    if usage is None:
        return 0
    value = getattr(usage, attr, 0) or 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
