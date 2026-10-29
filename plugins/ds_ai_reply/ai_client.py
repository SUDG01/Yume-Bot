"""OpenAI 兼容接口调用（主对话 + 记忆抽取）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from .config import AIConfig, MemoryLLMConfig, ProviderConfig, get_ai_config


@dataclass
class AIResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


_main_client: AsyncOpenAI | None = None
_memory_client: AsyncOpenAI | None = None
_main_signature: tuple[str, str, float, bool] | None = None
_memory_signature: tuple[str, str, float, bool] | None = None


def _make_client(
    base_url: str,
    api_key: str,
    timeout: float,
    trust_env: bool,
    max_retries: int,
) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
        http_client=DefaultAsyncHttpxClient(trust_env=trust_env),
    )


def _provider_client(config: AIConfig) -> AsyncOpenAI:
    global _main_client, _main_signature
    provider = config.provider
    signature = (
        provider.base_url,
        provider.api_key,
        provider.timeout,
        provider.trust_env,
    )
    if _main_client is None or _main_signature != signature:
        _main_client = _make_client(
            provider.base_url,
            provider.api_key,
            provider.timeout,
            provider.trust_env,
            provider.max_retries,
        )
        _main_signature = signature
    return _main_client


def _memory_llm_client(config: AIConfig) -> AsyncOpenAI:
    global _memory_client, _memory_signature
    memory: MemoryLLMConfig = config.memory_llm
    signature = (
        memory.base_url,
        memory.api_key,
        memory.timeout,
        config.provider.trust_env,
    )
    if _memory_client is None or _memory_signature != signature:
        _memory_client = _make_client(
            memory.base_url,
            memory.api_key,
            memory.timeout,
            config.provider.trust_env,
            1,
        )
        _memory_signature = signature
    return _memory_client


def _extract_usage(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


async def request_reply(
    messages: list[dict[str, Any]], config: AIConfig | None = None
) -> AIResult:
    config = config or get_ai_config()
    provider: ProviderConfig = config.provider
    response = await _provider_client(config).chat.completions.create(
        model=provider.model,
        messages=messages,  # type: ignore[arg-type]
        temperature=config.generation.temperature,
        top_p=config.generation.top_p,
        max_tokens=config.generation.max_tokens,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("模型返回了空内容")
    prompt_tokens, completion_tokens = _extract_usage(response)
    return AIResult(content, provider.model, prompt_tokens, completion_tokens)


async def request_json(
    messages: list[dict[str, Any]], config: AIConfig | None = None
) -> AIResult:
    config = config or get_ai_config()
    memory = config.memory_llm
    response = await _memory_llm_client(config).chat.completions.create(
        model=memory.model,
        messages=messages,  # type: ignore[arg-type]
        temperature=memory.temperature,
        max_tokens=memory.max_tokens,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("记忆抽取模型返回了空内容")
    prompt_tokens, completion_tokens = _extract_usage(response)
    return AIResult(content, memory.model, prompt_tokens, completion_tokens)
