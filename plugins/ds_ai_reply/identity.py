"""QQ 身份、会话与别名的统一处理。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent

from .memory import store


def is_group(event: MessageEvent) -> bool:
    return isinstance(event, GroupMessageEvent)


def group_id(event: MessageEvent) -> str:
    return str(event.group_id) if isinstance(event, GroupMessageEvent) else ""


def user_id(event: MessageEvent) -> str:
    return str(event.user_id)


def session_id(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}"
    return f"private:{event.user_id}"


def session_type(event: MessageEvent) -> str:
    return "group" if isinstance(event, GroupMessageEvent) else "private"


def names(event: MessageEvent) -> list[tuple[str, str]]:
    """返回 (名字, 来源)：群名片 / QQ 昵称。"""
    result: list[tuple[str, str]] = []
    if isinstance(event, GroupMessageEvent) and event.sender.card:
        result.append((event.sender.card.strip(), "card"))
    if event.sender.nickname:
        result.append((event.sender.nickname.strip(), "nickname"))
    return [(name, source) for name, source in result if name]


def sender_name(event: MessageEvent) -> str:
    for name, _ in names(event):
        return name[:24]
    return str(event.user_id)


async def record_identity(event: MessageEvent) -> None:
    qq = user_id(event)
    group = group_id(event)
    await store.touch_user(qq, sender_name(event))
    for name, source in names(event):
        await store.add_alias(qq, group, name[:24], source)


async def display_name(event: MessageEvent) -> str:
    return await store.get_display_name(user_id(event), group_id(event))
