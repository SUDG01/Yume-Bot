"""聊天链路：记录消息、组装上下文、调用模型。"""

from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageEvent
from openai import APIConnectionError, APIStatusError, AuthenticationError

from . import ai_client, identity, jobs, media, trace
from .config import AIConfig, BotConfig
from .memory import retrieve, store
from .policy import (
    EffectiveGroup,
    is_meta_query,
    is_recall_query,
    looks_like_prompt_leak,
    rate_limiter,
    token_budget,
)
from .prompt import build_messages, message_char_count


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
    source = (
        f"群聊 {identity.group_id(event)}"
        if identity.is_group(event)
        else "私聊"
    )
    await trace.emit(
        "message",
        f"[{source}] {identity.sender_name(event)}：{text[:200]}",
        trace_id=identity.trace_id(event),
        session_id=session,
        group_id=identity.group_id(event),
        user_id=identity.user_id(event),
        detail={"message_id": event.message_id, "content": text},
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


def _sanitize_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """观测台里不保存 base64 图片，只留占位符。"""
    result: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            parts: list[Any] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": "[图片已省略]"},
                        }
                    )
                else:
                    parts.append(part)
            result.append({**message, "content": parts})
        else:
            result.append(message)
    return result


async def generate_reply(
    event: MessageEvent,
    text: str,
    trigger_kind: str,
    *,
    config: BotConfig,
    ai_config: AIConfig,
    effective: EffectiveGroup,
    decision_angle: str = "",
) -> ReplyResult:
    speaker = identity.user_id(event)
    group = identity.group_id(event)
    session = identity.session_id(event)
    trace_ctx: trace.TraceContext = {
        "trace_id": identity.trace_id(event),
        "session_id": session,
        "group_id": group,
        "user_id": speaker,
    }

    if not await token_budget.allows(config):
        await trace.emit(
            "decision", "今日 token 额度耗尽，跳过", **trace_ctx
        )
        raise ReplyBlocked("今天的 AI 额度用完啦，明天再聊喵。")

    if trigger_kind in {"random", "willing"}:
        if not rate_limiter.allow_random(
            group, config, config.trigger.willingness_cooldown_seconds
        ):
            await trace.emit(
                "decision", "主动接话被冷却或群限流拦截", **trace_ctx
            )
            raise ReplyBlocked(silent=True)
    elif not rate_limiter.allow_direct(speaker, group, config):
        await trace.emit("decision", "用户冷却或限流，跳过", **trace_ctx)
        raise ReplyBlocked("慢一点喵，让我喘口气……")

    recall = is_recall_query(text)
    meta_query = is_meta_query(text)
    try:
        memory_context = await build_memory_context(
            event, text, config, recall=recall
        )
    except Exception as error:
        logger.opt(exception=error).warning("ds_ai_reply 记忆检索失败，跳过记忆区")
        memory_context = retrieve.MemoryContext(
            display_name=identity.sender_name(event)
        )

    try:
        recent = await store.get_recent_messages(
            session, ai_config.context.max_recent_messages
        )
    except Exception as error:
        logger.opt(exception=error).warning("ds_ai_reply 读取历史消息失败")
        recent = []

    cross_messages: list[dict[str, Any]] = []
    if config.memory.cross_session_context:
        try:
            include_others = (
                not group and config.memory.cross_session_include_others
            )
            cross_messages = await store.get_cross_session_messages(
                speaker,
                session,
                max(
                    config.memory.cross_session_limit,
                    24 if include_others else 0,
                ),
                config.memory.cross_session_hours,
                config.memory.cross_session_include_private,
                include_others,
            )
        except Exception as error:
            logger.opt(exception=error).warning(
                "ds_ai_reply 读取跨会话消息失败"
            )

    image_data_urls: list[str] = []
    if ai_config.vision.enabled:
        image_sources = media.image_sources(event)
        if image_sources:
            image_data_urls = await media.fetch_image_data_urls(
                image_sources,
                max_images=ai_config.vision.max_images,
                max_bytes=ai_config.vision.max_bytes,
                timeout=ai_config.vision.timeout,
            )
            await trace.emit(
                "decision",
                f"图片处理：收到 {len(image_sources)} 张，"
                f"成功载入 {len(image_data_urls)} 张",
                **trace_ctx,
                detail={
                    "sources": len(image_sources),
                    "loaded": len(image_data_urls),
                },
            )

    recall_messages: list[dict[str, Any]] = []
    if recall:
        try:
            recall_messages = await store.search_user_messages(
                speaker,
                text,
                limit=24,
                days=config.memory.raw_retention_days,
                include_private=(
                    not group or config.memory.cross_session_include_private
                ),
            )
        except Exception as error:
            logger.opt(exception=error).warning(
                "ds_ai_reply 回忆聊天片段失败"
            )
        cross_ids = {int(item["id"]) for item in cross_messages}
        recall_messages = [
            item for item in recall_messages if int(item["id"]) not in cross_ids
        ]

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
        cross_messages=cross_messages,
        recall_messages=recall_messages,
        recall=recall,
        meta_query=meta_query,
        image_data_urls=image_data_urls,
        decision_angle=decision_angle,
    )

    await trace.emit(
        "context",
        f"准备回复：近聊 {len(recent)} 条，跨会话 {len(cross_messages)} 条，"
        f"记忆 {len(memory_context.memory_lines)} 条"
        + f"，图片 {len(image_data_urls)} 张"
        + ("（回忆模式）" if recall else "")
        + ("（内部设定询问）" if meta_query else ""),
        **trace_ctx,
        detail={
            "trigger": trigger_kind,
            "decision_angle": decision_angle,
            "recall": recall,
            "meta_query": meta_query,
            "images": len(image_data_urls),
            "recent_messages": len(recent),
            "cross_messages": len(cross_messages),
            "recall_messages": len(recall_messages),
            "profile": memory_context.profile_lines,
            "boundaries": memory_context.boundary_lines,
            "memories": memory_context.memory_lines,
            "cross": [
                {
                    "from": f"群聊 {item.get('group_id')}"
                    if item.get("group_id")
                    else "私聊",
                    "role": item.get("role"),
                    "nickname": item.get("nickname"),
                    "content": item.get("content"),
                }
                for item in cross_messages
            ],
            "recall": [
                {
                    "from": f"群聊 {item.get('group_id')}"
                    if item.get("group_id")
                    else "私聊",
                    "role": item.get("role"),
                    "nickname": item.get("nickname"),
                    "content": item.get("content"),
                    "at": item.get("created_at"),
                }
                for item in recall_messages
            ],
        },
    )

    request_detail: dict[str, Any] = {
        "model": ai_config.provider.model,
        "message_count": len(messages),
        "images": len(image_data_urls),
        "estimated_tokens": message_char_count(messages)
        // max(1, int(ai_config.context.chars_per_token)),
    }
    if trace.show_prompt():
        request_detail["messages"] = _sanitize_messages(messages)
    await trace.emit(
        "llm_request",
        f"请求模型 {ai_config.provider.model}（{len(messages)} 条消息"
        + (f"，{len(image_data_urls)} 张图" if image_data_urls else "")
        + "）",
        **trace_ctx,
        detail=request_detail,
    )

    started = time.perf_counter()
    try:
        async with _concurrency:
            result = await ai_client.request_reply(messages, ai_config)
    except AuthenticationError as error:
        logger.warning(f"ds_ai_reply API Key 无效：{error}")
        await trace.emit(
            "error",
            "模型请求失败：API Key 无效",
            **trace_ctx,
            detail={"error": str(error)},
        )
        raise ReplyBlocked("AI 的 API Key 无效，请检查 aiconfig.toml") from error
    except APIConnectionError as error:
        logger.warning(f"ds_ai_reply 连接失败：{error}")
        await trace.emit(
            "error",
            "模型请求失败：连接不上",
            **trace_ctx,
            detail={"error": str(error)},
        )
        raise ReplyBlocked("连不上 AI 接口，请检查网络或 base_url") from error
    except APIStatusError as error:
        logger.warning(f"ds_ai_reply 接口错误：{error.status_code} {error}")
        await trace.emit(
            "error",
            f"模型接口返回 {error.status_code}",
            **trace_ctx,
            detail={"status": error.status_code, "error": str(error)},
        )
        raise ReplyBlocked(f"AI 接口返回错误（{error.status_code}），稍后再试") from error
    except Exception as error:
        logger.opt(exception=error).error("ds_ai_reply 请求失败")
        await trace.emit(
            "error",
            f"模型请求失败：{type(error).__name__}",
            **trace_ctx,
            detail={"error": str(error)},
        )
        raise ReplyBlocked("AI 回复失败了，稍后再试喵") from error

    latency_ms = int((time.perf_counter() - started) * 1000)
    await trace.emit(
        "llm_response",
        f"模型返回 {len(result.text)} 字，耗时 {latency_ms}ms",
        **trace_ctx,
        detail={
            "text": result.text,
            "model": result.model,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "latency_ms": latency_ms,
        },
    )

    await token_budget.add(
        config, result.prompt_tokens, result.completion_tokens, result.model
    )

    if trigger_kind in {"random", "willing"}:
        max_chars = config.reply.random_max_chars
    elif group:
        max_chars = config.reply.group_max_chars
    else:
        max_chars = config.reply.private_max_chars
    max_chars = min(max_chars, config.persona.max_reply_chars)
    reply = result.text.strip()[:max_chars].strip()
    if not reply:
        await trace.emit("decision", "模型返回空内容，跳过", **trace_ctx)
        raise ReplyBlocked(silent=True)

    if meta_query and looks_like_prompt_leak(reply):
        await trace.emit(
            "decision",
            "回复疑似复述内部设定，已替换为简短拒绝",
            **trace_ctx,
            detail={"original": reply},
        )
        reply = "这个不方便说，换个话题？"

    await trace.emit(
        "reply",
        f"回复：{reply[:120]}",
        **trace_ctx,
        detail={
            "text": reply,
            "max_chars": max_chars,
            "memory_ids": memory_context.used_ids,
        },
    )

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
    recall: bool = False,
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
        recall=recall,
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


_SENTENCE_END = re.compile(r"(?<=[。！？!?…~])")
_LONG_PAUSE = re.compile(r"(?<=[，,、；;])")


def _hard_split(text: str, max_chars: int) -> list[str]:
    return [
        text[index : index + max_chars]
        for index in range(0, len(text), max_chars)
        if text[index : index + max_chars].strip()
    ]


def _split_long_piece(piece: str, max_chars: int) -> list[str]:
    if len(piece) <= max_chars:
        return [piece]

    parts: list[str] = []
    current = ""
    for chunk in _LONG_PAUSE.split(piece):
        if not chunk:
            continue
        if len(current) + len(chunk) <= max_chars:
            current += chunk
            continue
        if current:
            parts.append(current)
            current = ""
        if len(chunk) > max_chars:
            parts.extend(_hard_split(chunk, max_chars))
        else:
            current = chunk
    if current:
        parts.append(current)
    return [part for part in parts if part.strip()]


def split_reply(
    text: str,
    *,
    enabled: bool,
    min_chars: int,
    max_chars: int,
    max_segments: int,
) -> list[str]:
    """按句子/停顿拆成多条短消息，保留全部内容。"""
    text = text.strip()
    if not text:
        return []
    if not enabled or max_segments <= 1 or len(text) <= min_chars:
        return [text]

    pieces: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for sentence in _SENTENCE_END.split(line):
            sentence = sentence.strip()
            if sentence:
                pieces.extend(_split_long_piece(sentence, max_chars))

    total = sum(len(piece) for piece in pieces)
    target_count = min(max_segments, max(1, math.ceil(total / max_chars)))
    target = min(max_chars, max(1, math.ceil(total / target_count)))

    segments: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}{piece}".strip() if current else piece
        if len(candidate) <= target:
            current = candidate
            continue
        if current:
            segments.append(current)
        current = piece
    if current:
        segments.append(current)

    if len(segments) > max_segments:
        head = segments[: max_segments - 1]
        tail = "".join(segments[max_segments - 1 :]).strip()
        segments = head + ([tail] if tail else [])

    if (
        len(segments) > 1
        and len(segments[-1]) < 8
        and len(segments[-2]) + len(segments[-1]) <= max_chars
    ):
        segments[-2] += segments[-1]
        segments.pop()

    return [segment for segment in segments if segment.strip()]
