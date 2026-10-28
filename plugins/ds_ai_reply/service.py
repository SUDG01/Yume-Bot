"""聊天链路：记录消息、组装上下文、调用模型。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageEvent
from openai import APIConnectionError, APIStatusError, AuthenticationError

from . import ai_client, identity, jobs
from .config import AIConfig, BotConfig
from .memory import retrieve, store
from .policy import EffectiveGroup, rate_limiter, token_budget
from .prompt import build_messages


class ReplyBlocked(RuntimeError):
    def __init__(self, message: str = "", silent: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.silent = silent


_concurrency = asyncio.Semaphore(2)


def configure_concurrency(limit: int) -> None:
    global _concurrency
    _concurrency = asyncio.Semaphore(max(1, limit))


@dataclass
class ReplyResult:
    text: str
    memory_ids: list[int] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


async def record_incoming(event: MessageEvent, text: str) -> None:
    """记录消息、会话和身份；命令消息不记录。"""
    if not text:
        return
    await identity.record_identity(event)
    session = identity.session_id(event)
    await store.touch_session(
        session,
        identity.session_type(event),
        identity.group_id(event),
        identity.user_id(event),
    )
    await store.add_message(
        session,
        identity.group_id(event),
        identity.user_id(event),
        "user",
        identity.sender_name(event),
        text,
    )


async def record_assistant(
    event: MessageEvent, text: str, config: BotConfig
) -> None:
    await store.add_message(
        identity.session_id(event),
        identity.group_id(event),
        "",
        "assistant",
        config.persona.name,
        text,
    )


async def generate_reply(
    event: MessageEvent,
    text: str,
    trigger_kind: str,
    *,
    config: BotConfig,
    ai_config: AIConfig,
    effective: EffectiveGroup,
) -> ReplyResult:
    speaker = identity.user_id(event)
    group = identity.group_id(event)

    if not await token_budget.allows(config):
        raise ReplyBlocked("今天的 AI 额度用完啦，明天再聊喵。")

    if trigger_kind == "random":
        if not rate_limiter.allow_random(
            group, config, config.trigger.random_reply_cooldown_seconds
        ):
            raise ReplyBlocked(silent=True)
    elif not rate_limiter.allow_direct(speaker, group, config):
        raise ReplyBlocked("慢一点喵，让我喘口气……")

    try:
        memory_context = await build_memory_context(event, text, config)
    except Exception as error:
        logger.opt(exception=error).warning("ds_ai_reply 记忆检索失败，跳过记忆区")
        memory_context = retrieve.MemoryContext(
            display_name=identity.sender_name(event)
        )

    session = identity.session_id(event)
    try:
        recent = await store.get_recent_messages(
            session, ai_config.context.max_recent_messages
        )
    except Exception as error:
        logger.opt(exception=error).warning("ds_ai_reply 读取历史消息失败")
        recent = []
    messages = build_messages(
        config=config,
        ai_config=ai_config,
        effective=effective,
        recent_messages=recent,
        current_text=text,
        display_name=memory_context.display_name or identity.sender_name(event),
        speaker_qq=speaker,
        group_id=group,
        trigger_kind=trigger_kind,
        memory=memory_context,
    )

    try:
        async with _concurrency:
            result = await ai_client.request_reply(messages, ai_config)
    except AuthenticationError as error:
        logger.warning(f"ds_ai_reply API Key 无效：{error}")
        raise ReplyBlocked("AI 的 API Key 无效，请检查 aiconfig.toml") from error
    except APIConnectionError as error:
        logger.warning(f"ds_ai_reply 连接失败：{error}")
        raise ReplyBlocked("连不上 AI 接口，请检查网络或 base_url") from error
    except APIStatusError as error:
        logger.warning(f"ds_ai_reply 接口错误：{error.status_code} {error}")
        raise ReplyBlocked(f"AI 接口返回错误（{error.status_code}），稍后再试") from error
    except Exception as error:
        logger.opt(exception=error).error("ds_ai_reply 请求失败")
        raise ReplyBlocked("AI 回复失败了，稍后再试喵") from error

    await token_budget.add(
        config, result.prompt_tokens, result.completion_tokens, result.model
    )

    reply = result.text.strip()[: config.persona.max_reply_chars].strip()
    if not reply:
        raise ReplyBlocked(silent=True)

    if memory_context.used_ids:
        await store.touch_memories(memory_context.used_ids)

    return ReplyResult(
        text=reply,
        memory_ids=list(memory_context.used_ids),
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )


async def build_memory_context(
    event: MessageEvent,
    text: str,
    config: BotConfig,
) -> retrieve.MemoryContext:
    speaker = identity.user_id(event)
    group = identity.group_id(event)
    display = await identity.display_name(event)
    return await retrieve.build_memory_context(
        query_text=text,
        speaker_qq=speaker,
        group_id=group,
        display_name=display,
        mix_context=config.memory.mix_context,
    )


async def after_reply(
    event: MessageEvent, result: ReplyResult, config: BotConfig
) -> None:
    try:
        await record_assistant(event, result.text, config)
        if config.memory.enabled and config.memory.extraction_enabled:
            await jobs.enqueue_extract(
                identity.session_id(event), config.memory.idle_seconds
            )
    except Exception as error:
        # 记录失败不应该阻止回复发出
        logger.opt(exception=error).warning("ds_ai_reply 保存回复失败")


def split_reply(text: str, max_chars: int, max_segments: int) -> list[str]:
    """按段落拆分过长回复，最多 max_segments 条。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    segments: list[str] = []
    current = ""
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = f"{current}\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            segments.append(current)
        while len(paragraph) > max_chars:
            segments.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        current = paragraph
        if len(segments) >= max_segments:
            break
    if current and len(segments) < max_segments:
        segments.append(current)
    return segments[:max_segments]
