"""长期记忆召回与排序。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import store
from .text import query_tokens

SOURCE_LABELS = {
    "user_global": "跨群",
    "user_group": "群内",
    "group": "群知识",
    "private": "私聊",
    "system": "系统",
}


@dataclass
class MemoryContext:
    display_name: str = ""
    profile_lines: list[str] = field(default_factory=list)
    boundary_lines: list[str] = field(default_factory=list)
    memory_lines: list[str] = field(default_factory=list)
    used_ids: list[int] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _age_days(timestamp: str | None) -> float:
    if not timestamp:
        return 3650.0
    try:
        created = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return 3650.0
    return max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86400)


def _source_label(memory: dict[str, Any]) -> str:
    scope = str(memory.get("scope", ""))
    label = SOURCE_LABELS.get(scope, scope or "记忆")
    origin_group = str(memory.get("group_id") or "")
    if scope in {"group", "user_group"} and origin_group:
        return f"{label}:{origin_group}"
    return label


def _is_visible(
    memory: dict[str, Any],
    speaker_qq: str,
    group_id: str,
    mix_context: bool,
) -> bool:
    scope = str(memory.get("scope", ""))
    if scope == "system":
        return True
    if scope == "group":
        return bool(group_id) and str(memory.get("group_id")) == group_id
    owner = str(memory.get("owner_qq") or "")
    subject = str(memory.get("subject_qq") or "")
    # 只允许当前发言者自己的记忆，避免把别人的私事带出来
    if speaker_qq and speaker_qq not in {owner, subject}:
        return False

    if not mix_context:
        # 关闭混用时退回旧策略：群聊只用 跨群+本群，私聊只用 跨群+私聊
        if group_id:
            if scope == "private":
                return False
            if scope == "user_group" and str(memory.get("group_id")) != group_id:
                return False
        elif scope in {"group", "user_group"}:
            return False
    return True


def _score(
    memory: dict[str, Any],
    position: int,
    total: int,
    speaker_qq: str,
) -> float:
    bm25_norm = 1.0 - (position / max(1, total))
    importance = min(5, max(1, int(memory.get("importance") or 3))) / 5
    recency = 0.5 ** (_age_days(memory.get("updated_at")) / 30)
    scope = str(memory.get("scope", ""))
    if speaker_qq and speaker_qq in {
        str(memory.get("owner_qq") or ""),
        str(memory.get("subject_qq") or ""),
    }:
        affinity = 1.0
    elif scope == "group":
        affinity = 0.6
    else:
        affinity = 0.2
    access = min(1.0, math.log1p(int(memory.get("access_count") or 0)) / 3)
    return (
        0.45 * bm25_norm
        + 0.20 * importance
        + 0.15 * recency
        + 0.10 * affinity
        + 0.10 * access
    )


def _profile_from_memories(
    memories: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """把结构化槽位整理成画像行；boundary 单独返回。"""
    slots: dict[str, dict[str, Any]] = {}
    boundaries: list[str] = []
    for memory in memories:
        kind = str(memory.get("kind", ""))
        if kind == "boundary":
            boundaries.append(str(memory.get("content", "")))
            continue
        slot = str(memory.get("slot") or "")
        if not slot or kind not in {"fact", "preference"}:
            continue
        current = slots.get(slot)
        if current is None or str(memory.get("updated_at", "")) > str(
            current.get("updated_at", "")
        ):
            slots[slot] = memory

    lines: list[str] = []
    for slot, memory in slots.items():
        content = str(memory.get("content", "")).strip()
        if content:
            lines.append(f"{slot}：{content}")
    return lines, boundaries


async def build_memory_context(
    *,
    query_text: str,
    speaker_qq: str,
    group_id: str,
    display_name: str,
    mix_context: bool,
    recall: bool = False,
    max_candidates: int = 60,
) -> MemoryContext:
    context = MemoryContext(display_name=display_name)

    user_memories = await store.get_user_memories(speaker_qq, limit=300)
    context.stats["user_total"] = len(user_memories)
    context.profile_lines, context.boundary_lines = _profile_from_memories(
        user_memories
    )

    # 长消息会产生大量 bigram，截断到 32 个唯一 token，避免 FTS 查询过重
    tokens = list(dict.fromkeys(query_tokens(query_text)))[:32]
    candidates = await store.search_memories(query_text, tokens, limit=120)

    group_memories: list[dict[str, Any]] = []
    if group_id:
        group_memories = await store.get_group_memories(group_id, limit=100)
        context.stats["group_total"] = len(group_memories)

    scored: list[tuple[float, dict[str, Any]]] = []
    seen: set[int] = set()

    for position, memory in enumerate(candidates):
        memory_id = int(memory.get("id", 0))
        if memory_id in seen or int(memory.get("sensitive") or 0):
            continue
        if str(memory.get("kind", "")) == "boundary":
            continue
        if not _is_visible(memory, speaker_qq, group_id, mix_context):
            continue
        seen.add(memory_id)
        scored.append(
            (_score(memory, position, len(candidates), speaker_qq), memory)
        )

    # 即使没有命中关键词，也保留少量当前发言者的重要记忆，保证"认识"是连续的。
    # 用户明确问"还记得吗/上次"时，扩大召回，避免检索不到就当成忘了。
    for memory in user_memories[:20 if recall else 8]:
        memory_id = int(memory.get("id", 0))
        if memory_id in seen or int(memory.get("sensitive") or 0):
            continue
        kind = str(memory.get("kind", ""))
        if kind in {"boundary"}:
            continue
        seen.add(memory_id)
        scored.append((0.4 + 0.03 * min(5, int(memory.get("importance") or 3)), memory))

    # 群知识即使没有命中关键词也保留少量，帮助理解群语境
    for memory in group_memories[:8 if recall else 5]:
        memory_id = int(memory.get("id", 0))
        if memory_id in seen or int(memory.get("sensitive") or 0):
            continue
        seen.add(memory_id)
        scored.append((0.42, memory))

    scored.sort(key=lambda item: item[0], reverse=True)

    threshold = 0.25 if recall else 0.35
    for score, memory in scored[: max_candidates]:
        if score < threshold:
            continue
        label = _source_label(memory)
        content = str(memory.get("content", "")).strip()
        if not content:
            continue
        updated = str(memory.get("updated_at") or "")[:10]
        stamp = f"[{updated}] " if updated else ""
        context.memory_lines.append(f"[{label}] {stamp}{content}")
        context.used_ids.append(int(memory["id"]))

    return context
