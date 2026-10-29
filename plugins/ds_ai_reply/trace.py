"""结构化观测事件：消息、判断、上下文、模型请求/回复、记忆抽取。"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from nonebot import logger

from .memory import store

class TraceContext(TypedDict, total=False):
    trace_id: str
    session_id: str
    group_id: str
    user_id: str


_enabled = False
_show_prompt = True

_MAX_STRING = 2000
_MAX_DETAIL_BYTES = 60000


def configure(*, enabled: bool, show_prompt: bool = True) -> None:
    global _enabled, _show_prompt
    _enabled = enabled
    _show_prompt = show_prompt


def enabled() -> bool:
    return _enabled


def show_prompt() -> bool:
    return _enabled and _show_prompt


def _limit(value: Any) -> Any:
    if isinstance(value, str):
        return value[:_MAX_STRING]
    if isinstance(value, dict):
        return {str(key)[:80]: _limit(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_limit(item) for item in value[:200]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:_MAX_STRING]


async def emit(
    kind: str,
    summary: str,
    *,
    trace_id: str = "",
    session_id: str = "",
    group_id: str = "",
    user_id: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    if not _enabled:
        return
    try:
        payload = json.dumps(
            _limit(detail or {}), ensure_ascii=False, default=str
        )
        if len(payload.encode("utf-8")) > _MAX_DETAIL_BYTES:
            payload = json.dumps(
                {"truncated": True, "preview": payload[: _MAX_DETAIL_BYTES // 2]},
                ensure_ascii=False,
            )
        await store.add_trace(
            trace_id=trace_id,
            session_id=session_id,
            group_id=group_id,
            user_id=user_id,
            kind=kind,
            summary=summary,
            detail=payload,
        )
    except Exception as error:
        logger.opt(exception=error).debug("ds_ai_reply 写入观测事件失败")
