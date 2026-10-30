"""ds_ai_reply：群聊/私聊 AI 回复 + 长期记忆。

行为：
- 群聊：@bot、唤醒词，或按聊天意愿 + 模型决策主动接话
- 私聊：按 private_mode 回复
- 所有非命令消息作为上下文保存，长期记忆由后台任务异步抽取
- 记忆跨群/私聊混用，但会标注来源；私聊内容不会主动在群里复述
"""

from __future__ import annotations

import asyncio
import random

from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment

from . import commands as _commands  # noqa: F401  注册 /ai 命令
from . import dashboard, decision, identity, jobs, policy, service, trace
from .config import ConfigError, get_ai_config, get_bot_config
from .policy import get_effective_group, in_quiet_hours, is_command
from .memory import store

driver = get_driver()

record_matcher = on_message(priority=1, block=False)
willing_matcher = on_message(priority=9, block=False)
direct_matcher = on_message(priority=10, block=True)

_ready = False


@driver.on_startup
async def _startup() -> None:
    global _ready
    try:
        config = get_bot_config()
    except ConfigError as error:
        logger.error(f"ds_ai_reply 配置加载失败，插件不可用：{error}")
        return

    await store.init_db(config.general.data_dir)
    trace.configure(
        enabled=config.dashboard.enabled,
        show_prompt=config.dashboard.show_prompt,
    )
    await dashboard.start(config.dashboard)
    service.configure_concurrency(config.rate_limit.max_concurrency)
    _ready = True

    try:
        ai_config = get_ai_config()
    except ConfigError as error:
        logger.warning(f"ds_ai_reply 未就绪，AI 回复暂不可用：{error}")
        await trace.emit(
            "system",
            "AI 配置缺失，聊天暂不可用",
            detail={"error": str(error)},
        )
        return

    await jobs.start_workers()
    await trace.emit(
        "system",
        f"ds_ai_reply 已就绪：{ai_config.provider.model}",
        detail={
            "base_url": ai_config.provider.base_url,
            "chat_willingness": config.trigger.chat_willingness,
            "dashboard": f"http://{config.dashboard.host}:{config.dashboard.port}/",
        },
    )
    logger.info(
        f"ds_ai_reply 已就绪：{ai_config.provider.model} @ "
        f"{ai_config.provider.base_url}；"
        f"聊天意愿 {config.trigger.chat_willingness:.2f}"
    )


@driver.on_shutdown
async def _shutdown() -> None:
    await jobs.stop_workers()
    await trace.emit("system", "ds_ai_reply 已停止")
    await dashboard.stop()


def _command_starts() -> set[str]:
    return set(get_driver().config.command_start or {"/"})


MAX_MESSAGE_CHARS = 2000


def _text(event: MessageEvent) -> str:
    """保留文字，同时把图片/语音等非文字消息标成占位符，避免模型以为没收到。"""
    parts: list[str] = []
    for segment in event.get_message():
        if segment.type == "text":
            parts.append(str(segment.data.get("text") or ""))
        elif segment.type == "image":
            parts.append("[图片]")
        elif segment.type == "face":
            parts.append("[表情]")
        elif segment.type == "record":
            parts.append("[语音]")
        elif segment.type == "video":
            parts.append("[视频]")
        elif segment.type == "forward":
            parts.append("[转发消息]")
    return "".join(parts).strip()[:MAX_MESSAGE_CHARS]


def _trace_ctx(event: MessageEvent) -> trace.TraceContext:
    return {
        "trace_id": identity.trace_id(event),
        "session_id": identity.session_id(event),
        "group_id": identity.group_id(event),
        "user_id": identity.user_id(event),
    }


def _direct_trigger(
    event: MessageEvent,
    text: str,
    effective: policy.EffectiveGroup | None,
    config,
) -> str | None:
    if not identity.is_group(event):
        if config.trigger.private_mode == "off":
            return None
        if config.trigger.private_mode == "whitelist":
            return "private" if policy.is_bot_admin(event, config) else None
        return "private"

    if effective is None or not effective.enabled:
        return None

    if getattr(event, "to_me", False):
        return "mention"
    if effective.quiet_hours and in_quiet_hours(
        effective.quiet_hours, config.general.timezone
    ):
        return None
    if effective.trigger_mode == "all":
        return "all"
    if effective.trigger_mode == "mention_or_wake" and policy.contains_wake_word(
        text, config.trigger.wake_words
    ):
        return "wake"
    return None


async def _deliver(
    matcher,
    event: MessageEvent,
    text: str,
    config,
    *,
    quote: bool,
) -> None:
    segments = service.split_reply(
        text,
        enabled=config.reply.split_enabled,
        min_chars=config.reply.split_min_chars,
        max_chars=config.reply.split_max_chars,
        max_segments=config.reply.max_segments,
    )
    if not segments:
        return

    delays = [
        config.reply.split_delay_ms
        + random.randint(0, config.reply.split_jitter_ms)
        for _ in range(max(0, len(segments) - 1))
    ]
    if len(segments) > 1:
        await trace.emit(
            "reply_split",
            f"回复拆成 {len(segments)} 条发送",
            **_trace_ctx(event),
            detail={"segments": segments, "delays_ms": delays},
        )

    for index, segment in enumerate(segments):
        message = MessageSegment.text(segment)
        if index == 0 and quote and config.reply.quote:
            message = MessageSegment.reply(event.message_id) + message
        if index == len(segments) - 1:
            await matcher.finish(message)
        else:
            await matcher.send(message)
            await asyncio.sleep(delays[index] / 1000)


@record_matcher.handle()
async def _record(event: MessageEvent) -> None:
    if not _ready:
        return
    text = _text(event)
    if not text:
        return
    if str(event.user_id) == str(event.self_id):
        return
    if is_command(text, _command_starts()):
        await trace.emit(
            "decision", "命令消息，跳过记录", **_trace_ctx(event)
        )
        return

    try:
        config = get_bot_config()
    except ConfigError as error:
        await trace.emit(
            "error",
            "读取 bot_config 失败",
            **_trace_ctx(event),
            detail={"error": str(error)},
        )
        return

    effective = None
    if identity.is_group(event):
        effective = await get_effective_group(identity.group_id(event), config)
        if not effective.enabled:
            await trace.emit(
                "decision", "群 AI 已关闭，不记录", **_trace_ctx(event)
            )
            return
    elif not config.privacy.store_private_messages:
        await trace.emit(
            "decision", "私聊消息存储已关闭", **_trace_ctx(event)
        )
        return

    try:
        await service.record_incoming(event, text)
        if config.memory.enabled and config.memory.extraction_enabled:
            await jobs.enqueue_extract(
                identity.session_id(event), config.memory.idle_seconds
            )
    except Exception as error:
        logger.opt(exception=error).warning("ds_ai_reply 记录消息失败")
        await trace.emit(
            "error",
            "记录消息失败",
            **_trace_ctx(event),
            detail={"error": str(error)},
        )


@willing_matcher.handle()
async def _willing_reply(event: MessageEvent) -> None:
    if not _ready or not identity.is_group(event):
        return
    text = _text(event)
    if not text or len(text) < 2:
        return
    if str(event.user_id) == str(event.self_id):
        return
    if is_command(text, _command_starts()):
        return

    try:
        config = get_bot_config()
    except ConfigError as error:
        await trace.emit(
            "error",
            "读取 bot_config 失败",
            **_trace_ctx(event),
            detail={"error": str(error)},
        )
        return
    effective = await get_effective_group(identity.group_id(event), config)
    if not effective.enabled:
        await trace.emit(
            "decision", "主动接话跳过：群 AI 已关闭", **_trace_ctx(event)
        )
        return
    if _direct_trigger(event, text, effective, config) is not None:
        await trace.emit(
            "decision", "主动接话跳过：该消息会直接触发", **_trace_ctx(event)
        )
        return
    if effective.chat_willingness <= 0:
        return
    if effective.quiet_hours and in_quiet_hours(
        effective.quiet_hours, config.general.timezone
    ):
        await trace.emit(
            "decision", "主动接话跳过：静音时段", **_trace_ctx(event)
        )
        return
    group = identity.group_id(event)
    if config.trigger.decision_with_llm and not policy.decision_limiter.ready(
        group
    ):
        return

    roll = random.random()
    hit = roll < effective.chat_willingness
    await trace.emit(
        "decision",
        f"聊天意愿：w={effective.chat_willingness:.2f} "
        f"roll={roll:.3f} → {'进入判断' if hit else '不接'}",
        **_trace_ctx(event),
        detail={
            "willingness": effective.chat_willingness,
            "roll": round(roll, 4),
            "hit": hit,
        },
    )
    if not hit:
        return

    try:
        ai_config = get_ai_config()
    except ConfigError as error:
        await trace.emit(
            "error",
            "AI 配置缺失",
            **_trace_ctx(event),
            detail={"error": str(error)},
        )
        return

    decision_angle = ""
    if config.trigger.decision_with_llm:
        if not policy.decision_limiter.allow(
            group, config.trigger.decision_max_per_hour
        ):
            await trace.emit(
                "decision",
                "意愿决策达到每小时上限，跳过",
                **_trace_ctx(event),
            )
            return

        try:
            recent = await store.get_recent_messages(
                identity.session_id(event), 8
            )
            verdict = await decision.decide_willingness(
                config=config,
                ai_config=ai_config,
                display_name=await identity.display_name(event),
                current_text=text,
                recent_messages=recent,
            )
        except Exception as error:
            logger.opt(exception=error).warning("ds_ai_reply 意愿决策失败")
            await trace.emit(
                "error",
                f"意愿决策失败：{type(error).__name__}",
                **_trace_ctx(event),
                detail={"error": str(error)},
            )
            return

        await policy.token_budget.add(
            config,
            verdict.prompt_tokens,
            verdict.completion_tokens,
            verdict.model or "decision",
        )
        await trace.emit(
            "willingness",
            f"意愿决策：{'接话' if verdict.speak else '不接'}",
            **_trace_ctx(event),
            detail={
                "speak": verdict.speak,
                "reason": verdict.reason,
                "angle": verdict.angle,
                "model": verdict.model,
                "prompt_tokens": verdict.prompt_tokens,
                "completion_tokens": verdict.completion_tokens,
            },
        )
        if not verdict.speak:
            policy.decision_limiter.note_no_action(
                group,
                config.trigger.decision_backoff_base_seconds,
                config.trigger.decision_backoff_cap_seconds,
                config.trigger.decision_backoff_start_count,
            )
            return
        policy.decision_limiter.note_action(group)
        decision_angle = verdict.angle
    else:
        await trace.emit(
            "willingness",
            "未启用 LLM 决策，按聊天意愿直接接话",
            **_trace_ctx(event),
        )

    try:
        result = await service.generate_reply(
            event,
            text,
            "willing",
            config=config,
            ai_config=ai_config,
            effective=effective,
            decision_angle=decision_angle,
        )
    except service.ReplyBlocked as blocked:
        if not blocked.silent and blocked.message:
            await willing_matcher.send(MessageSegment.text(blocked.message))
        return

    await service.after_reply(event, result, config)
    await _deliver(willing_matcher, event, result.text, config, quote=False)


@direct_matcher.handle()
async def _direct_reply(event: MessageEvent) -> None:
    if not _ready:
        return
    text = _text(event)
    if not text:
        return
    if str(event.user_id) == str(event.self_id):
        return
    if is_command(text, _command_starts()):
        return

    try:
        config = get_bot_config()
    except ConfigError as error:
        await trace.emit(
            "error",
            "读取 bot_config 失败",
            **_trace_ctx(event),
            detail={"error": str(error)},
        )
        return

    effective = None
    if identity.is_group(event):
        effective = await get_effective_group(identity.group_id(event), config)

    trigger_kind = _direct_trigger(event, text, effective, config)
    if trigger_kind is None:
        return

    await trace.emit(
        "decision",
        f"直接回复触发：{trigger_kind}",
        **_trace_ctx(event),
        detail={"trigger": trigger_kind},
    )

    try:
        ai_config = get_ai_config()
    except ConfigError as error:
        await trace.emit(
            "error",
            "AI 配置缺失",
            **_trace_ctx(event),
            detail={"error": str(error)},
        )
        await direct_matcher.finish(f"喵… AI 还没配置好：{error}")
        return

    try:
        result = await service.generate_reply(
            event,
            text,
            trigger_kind,
            config=config,
            ai_config=ai_config,
            effective=effective or policy.EffectiveGroup(group_id=""),
        )
    except service.ReplyBlocked as blocked:
        if not blocked.silent and blocked.message:
            await direct_matcher.finish(MessageSegment.text(blocked.message))
        return

    await service.after_reply(event, result, config)
    await _deliver(direct_matcher, event, result.text, config, quote=True)
