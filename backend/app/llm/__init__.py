"""LLM integration: Anthropic client wrapper, prompt assembly, leak tags."""

from app.config import get_settings
from app.llm.anthropic import (
    AnalysisResult,
    AnthropicClient,
    LLMClient,
    StreamChunk,
)
from app.llm.prompts import (
    GENERAL_COACHING_LABEL,
    SYSTEM_PROMPT,
    build_analysis_prompt,
    compute_prompt_hash,
)
from app.llm.tags import LEAK_TAGS, filter_leak_tags, parse_llm_response


def get_llm_client() -> LLMClient:
    """FastAPI dependency producing the default Anthropic-backed client.

    Tests should override this with ``app.dependency_overrides`` to inject
    a fake that records calls without hitting the network.
    """
    settings = get_settings()
    return AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.ANTHROPIC_MODEL,
        max_tokens=settings.ANTHROPIC_MAX_TOKENS,
    )


__all__ = [
    "AnthropicClient",
    "LLMClient",
    "AnalysisResult",
    "StreamChunk",
    "GENERAL_COACHING_LABEL",
    "SYSTEM_PROMPT",
    "build_analysis_prompt",
    "compute_prompt_hash",
    "LEAK_TAGS",
    "filter_leak_tags",
    "parse_llm_response",
    "get_llm_client",
]
