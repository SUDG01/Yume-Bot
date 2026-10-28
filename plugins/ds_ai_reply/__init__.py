"""ds_ai_reply：群聊/私聊 AI 回复 + 长期记忆。

行为：
- 群聊：@bot、唤醒词或按概率随机接话
- 私聊：按 private_mode 回复
- 所有非命令消息作为上下文保存，长期记忆由后台任务异步抽取
- 记忆跨群/私聊混用，但会标注来源；私聊内容不会主动在群里复述
"""

from __future__ import annotations

import random

from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment

from . import commands as _commands  # noqa: F401  注册 /ai 命令
from . import identity, jobs, policy, service
from .config import ConfigError, get_ai_config, get_bot_config
from .policy import get_effective_group, in_quiet_hours, is_command
from .memory import store

driver = get_driver()

record_matcher = on_message(priority=1, block=False)
random_matcher = on_message(priority=9, block=False)
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
    service.configure_concurrency(config.rate_limit.max_concurrency)
    _ready = True

    try:
        ai_config = get_ai_config()
    except ConfigError as error:
        logger.warning(f"ds_ai_reply 未就绪，AI 回复暂不可用：{error}")
        return

    await jobs.start_workers()
    logger.info(
        f"ds_ai_reply 已就绪：{ai_config.provider.model} @ "
        f"{ai_config.provider.base_url}；"
        f"随机接话概率 {config.trigger.random_reply_probability:.2f}"
    )


@driver.on_shutdown
async def _shutdown() -> None:
    await jobs.stop_workers()


def _command_starts() -> set[str]:
    return set(get_driver().config.command_start or {"/"})


MAX_MESSAGE_CHARS = 2000


def _text(event: MessageEvent) -> str:
    return event.get_plaintext().strip()[:MAX_MESSAGE_CHARS]


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
        text, config.persona.max_reply_chars, config.reply.max_segments
    )
    if not segments:
        return
    for index, segment in enumerate(segments):
        message = MessageSegment.text(segment)
        if index == 0 and quote and config.reply.quote:
            message = MessageSegment.reply(event.message_id) + message
        if index == len(segments) - 1:
            await matcher.finish(message)
        else:
            await matcher.send(message)


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
        return

    try:
        config = get_bot_config()
    except ConfigError:
        return

    effective = None
    if identity.is_group(event):
        effective = await get_effective_group(identity.group_id(event), config)
        if not effective.enabled:
            return
    elif not config.privacy.store_private_messages:
        return

    try:
        await service.record_incoming(event, text)
        if config.memory.enabled and config.memory.extraction_enabled:
            await jobs.enqueue_extract(
                identity.session_id(event), config.memory.idle_seconds
            )
    except Exception as error:
        logger.opt(exception=error).warning("ds_ai_reply 记录消息失败")


@random_matcher.handle()
async def _random_reply(event: MessageEvent) -> None:
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
    except ConfigError:
        return
    effective = await get_effective_group(identity.group_id(event), config)
    if not effective.enabled:
        return
    if _direct_trigger(event, text, effective, config) is not None:
        return
    if effective.random_reply_probability <= 0:
        return
    if effective.quiet_hours and in_quiet_hours(
        effective.quiet_hours, config.general.timezone
    ):
        return
    if random.random() >= effective.random_reply_probability:
        return

    try:
        ai_config = get_ai_config()
        result = await service.generate_reply(
            event,
            text,
            "random",
            config=config,
            ai_config=ai_config,
            effective=effective,
        )
    except service.ReplyBlocked as blocked:
        if not blocked.silent and blocked.message:
            await random_matcher.send(MessageSegment.text(blocked.message))
        return
    except ConfigError:
        return

    await service.after_reply(event, result, config)
    await _deliver(random_matcher, event, result.text, config, quote=False)


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
    except ConfigError:
        return

    effective = None
    if identity.is_group(event):
        effective = await get_effective_group(identity.group_id(event), config)

    trigger_kind = _direct_trigger(event, text, effective, config)
    if trigger_kind is None:
        return

    try:
        ai_config = get_ai_config()
    except ConfigError as error:
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
