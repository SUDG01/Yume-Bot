"""/ai 管理命令。"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.params import CommandArg
from nonebot.rule import to_me

from . import trace
from .config import (
    VALID_GROUP_MODES,
    ConfigError,
    get_ai_config,
    get_bot_config,
    reload_bot_config,
)
from .identity import group_id, is_group, session_id, user_id
from .memory import store
from .policy import (
    get_effective_group,
    invalidate_effective,
    is_bot_admin,
    is_group_admin,
)

ai_cmd = on_command(
    "ai",
    aliases={"AI"},
    rule=to_me(),
    priority=4,
    block=True,
)

HELP_TEXT = """—— AI 聊天管理 ——
/ai status            查看当前群/会话状态
/ai on | off          开启/关闭本群 AI（群管理）
/ai trigger <模式>    mention / mention_or_wake / all（群管理）
/ai random <0-1>      设置随机接话概率（群管理）
/ai clear             清空当前会话的短期上下文
/ai memory me         查看我记得你什么（私聊）
/ai forget me         删除我记得的关于你的内容
/ai forget <QQ>       删除某人在本群的记忆（群管理）
/ai reload            重新加载 bot_config.toml（bot 管理员）
/ai stats             查看用量与任务队列（bot 管理员）
"""


def _target_qq(event: MessageEvent, raw: str) -> str:
    for segment in event.get_message():
        if segment.type == "at":
            qq = str(segment.data.get("qq", ""))
            if qq and qq != "all":
                return qq
    return raw.strip()


async def _require_manager(event: MessageEvent) -> tuple[bool, str]:
    config = get_bot_config()
    if is_group_admin(event, config):
        return True, ""
    return False, "喵？这个操作需要群管理员或 bot 管理员权限"


async def _require_bot_admin(event: MessageEvent) -> tuple[bool, str]:
    config = get_bot_config()
    if is_bot_admin(event, config):
        return True, ""
    return False, "喵？这个操作需要 bot 管理员权限"


@ai_cmd.handle()
async def handle_ai(event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    parts = raw.split()
    action = parts[0].lower() if parts else "help"
    config = get_bot_config()

    await trace.emit(
        "command",
        f"/ai {action}",
        trace_id=f"{session_id(event)}:{event.message_id}",
        session_id=session_id(event),
        group_id=group_id(event),
        user_id=user_id(event),
        detail={"args": parts[1:]},
    )

    if action in {"help", ""}:
        await ai_cmd.finish(HELP_TEXT)
        return

    if action == "status":
        await _status(event)
        return

    if action in {"on", "off"}:
        ok, message = await _require_manager(event)
        if not ok:
            await ai_cmd.finish(message)
            return
        if not is_group(event):
            await ai_cmd.finish("这个命令要在群里使用")
            return
        gid = group_id(event)
        await store.set_group_setting(gid, enabled=(action == "on"))
        invalidate_effective(gid)
        await ai_cmd.finish(f"喵~ 本群 AI 已{'开启' if action == 'on' else '关闭'}")
        return

    if action == "trigger":
        ok, message = await _require_manager(event)
        if not ok:
            await ai_cmd.finish(message)
            return
        if not is_group(event):
            await ai_cmd.finish("这个命令要在群里使用")
            return
        if len(parts) < 2 or parts[1] not in VALID_GROUP_MODES:
            await ai_cmd.finish(
                "用法：/ai trigger mention|mention_or_wake|all"
            )
            return
        gid = group_id(event)
        await store.set_group_setting(gid, trigger_mode=parts[1])
        invalidate_effective(gid)
        await ai_cmd.finish(f"喵~ 本群触发方式已设为 {parts[1]}")
        return

    if action == "random":
        ok, message = await _require_manager(event)
        if not ok:
            await ai_cmd.finish(message)
            return
        if not is_group(event):
            await ai_cmd.finish("这个命令要在群里使用")
            return
        try:
            probability = float(parts[1]) if len(parts) >= 2 else -1
        except ValueError:
            probability = -1
        if not 0 <= probability <= 1:
            await ai_cmd.finish("用法：/ai random 0.05（范围 0.00 ~ 1.00）")
            return
        gid = group_id(event)
        await store.set_group_setting(gid, random_probability=probability)
        invalidate_effective(gid)
        await ai_cmd.finish(f"喵~ 本群随机接话概率已设为 {probability:.2f}")
        return

    if action == "clear":
        if is_group(event) and not is_group_admin(event, config):
            await ai_cmd.finish("喵？这个操作需要群管理员或 bot 管理员权限")
            return
        await store.clear_session(session_id(event))
        await ai_cmd.finish("喵~ 当前会话的短期上下文已清空")
        return

    if action == "memory":
        if not config.memory.allow_user_self_view:
            await ai_cmd.finish("喵… 这个功能被关掉了")
            return
        target = _target_qq(event, parts[1] if len(parts) > 1 else "me")
        is_self = target in {"me", ""} or target == user_id(event)
        if is_self:
            target = user_id(event)
        elif not is_group_admin(event, config):
            await ai_cmd.finish("喵？查看别人的记忆需要管理员权限")
            return

        if is_group(event) and is_self:
            count = await store.count_user_memories(target)
            await ai_cmd.finish(
                f"喵~ 我记得关于你的 {count} 条内容，私聊我发 /ai memory me 查看详情"
            )
            return

        if is_group(event):
            memories = await store.get_user_group_memories(
                target, group_id(event), limit=20
            )
        else:
            memories = await store.get_user_memories(target, limit=20)
        if not memories:
            await ai_cmd.finish("喵？没有找到相关记忆")
            return
        lines = [f"我记得关于 {target} 的 {len(memories)} 条内容："]
        for memory in memories:
            scope = str(memory.get("scope", ""))
            lines.append(f"- [{scope}] {memory.get('content', '')}")
        await ai_cmd.finish("\n".join(lines))
        return

    if action == "forget":
        if not config.memory.allow_user_forget:
            await ai_cmd.finish("喵… 这个功能被关掉了")
            return
        target = _target_qq(event, parts[1] if len(parts) > 1 else "me")
        is_self = target in {"me", ""} or target == user_id(event)
        if is_self:
            target = user_id(event)
            deleted = await store.delete_user_data(target)
            await ai_cmd.finish(f"喵~ 已删除 {deleted} 条关于你的记忆")
            return
        if is_group(event):
            if not is_group_admin(event, config):
                await ai_cmd.finish("喵？删除别人的记忆需要管理员权限")
                return
            deleted = await store.delete_user_group_data(target, group_id(event))
            await ai_cmd.finish(
                f"喵~ 已删除 {deleted} 条关于 {target} 的本群记忆"
            )
            return
        if not is_bot_admin(event, config):
            await ai_cmd.finish("喵？删除别人的记忆需要 bot 管理员权限")
            return
        deleted = await store.delete_user_data(target)
        await ai_cmd.finish(f"喵~ 已删除 {deleted} 条关于 {target} 的记忆")
        return

    if action == "reload":
        ok, message = await _require_bot_admin(event)
        if not ok:
            await ai_cmd.finish(message)
            return
        try:
            reload_bot_config()
        except ConfigError as error:
            await ai_cmd.finish(f"喵… 配置加载失败，保留原配置：{error}")
            return
        invalidate_effective()
        from .service import configure_concurrency

        configure_concurrency(get_bot_config().rate_limit.max_concurrency)
        await ai_cmd.finish("喵~ bot_config.toml 已重新加载")
        return

    if action == "stats":
        ok, message = await _require_bot_admin(event)
        if not ok:
            await ai_cmd.finish(message)
            return
        try:
            ai_config = get_ai_config()
            model = ai_config.provider.model
        except ConfigError:
            model = "未配置"
        from datetime import datetime
        from zoneinfo import ZoneInfo

        try:
            zone = ZoneInfo(config.general.timezone)
        except Exception:
            zone = ZoneInfo("UTC")
        day = datetime.now(zone).strftime("%Y-%m-%d")
        used = await store.daily_tokens(day)
        pending = await store.pending_jobs()
        await ai_cmd.finish(
            f"—— AI 状态 ——\n"
            f"模型：{model}\n"
            f"今日 token：{used} / {config.rate_limit.daily_token_budget}\n"
            f"待处理记忆任务：{pending}"
        )
        return

    await ai_cmd.finish(HELP_TEXT)


def _local_time(value: object, timezone_name: str) -> str:
    if not value:
        return "暂无"
    try:
        moment = datetime.strptime(
            str(value), "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        return moment.astimezone(ZoneInfo(timezone_name)).strftime("%m-%d %H:%M")
    except (ValueError, ZoneInfoNotFoundError):
        return str(value)


async def _status(event: MessageEvent) -> None:
    config = get_bot_config()
    gid = group_id(event)
    stats = await store.session_stats(session_id(event))
    last_at = _local_time(stats.get("last_at"), config.general.timezone)
    memory_count = await store.count_user_memories(user_id(event))
    cross = "开启" if config.memory.cross_session_context else "关闭"
    mix = "开启" if config.memory.mix_context else "关闭"

    if gid:
        effective = await get_effective_group(gid, config)
        enabled = "开启" if effective.enabled else "关闭"
        await ai_cmd.finish(
            f"—— 本群 AI 状态 ——\n"
            f"状态：{enabled}\n"
            f"触发：{effective.trigger_mode}\n"
            f"随机接话概率：{effective.random_reply_probability:.2f}\n"
            f"已记录消息：{stats.get('total', 0)} 条（最近 {last_at}）\n"
            f"跨会话上下文：{cross}，记忆混用：{mix}\n"
            f"我的记忆：{memory_count} 条\n"
            f"人格：{config.persona.name}"
        )
        return
    await ai_cmd.finish(
        f"—— 私聊 AI 状态 ——\n"
        f"状态：{config.trigger.private_mode}\n"
        f"已记录消息：{stats.get('total', 0)} 条（最近 {last_at}）\n"
        f"跨会话上下文：{cross}，记忆混用：{mix}\n"
        f"我的记忆：{memory_count} 条\n"
        f"人格：{config.persona.name}"
    )
