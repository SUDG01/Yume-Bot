"""用 LLM 从聊天记录里抽取长期记忆。"""

from __future__ import annotations

import json
import re
from typing import Any

from .. import ai_client
from ..config import AIConfig, BotConfig
from . import store
from .text import redact_sensitive

VALID_SCOPES = {"user_global", "user_group", "group", "private"}
VALID_KINDS = {"fact", "preference", "event", "boundary", "relationship"}
_INJECTION_RE = re.compile(
    r"(忽略|无视|跳过).{0,8}(指令|设定|规则)"
    r"|系统提示|system\s*prompt|从现在开始你|你现在是",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """你是记忆抽取器。只输出 JSON，不要解释。

输入是 QQ 群聊或私聊片段，每条消息带 id、user_id、nickname、content。
请抽取"值得长期记住"的信息，规则：
1. 只记录发言者本人明确陈述的事实、偏好、边界、重要事件。
2. 不推断年龄、性别、真实姓名、住址、学校、职业、健康等敏感信息。
3. 不记录请求、玩笑、玩梗、一次性问答（例如"帮我查一下这首歌"）。
4. 每条记忆必须有 evidence_message_ids，且只能引用输入里出现过的 id。
5. 群规/管理员指令只有发言者明确是管理员时才记录为 group 记忆。
6. 同一个人同一 slot 的新信息优先；slot 用简短英文，如 nickname/main_game/rating/schedule。
7. 没有值得记的就返回 {"memories": []}。

输出 schema：
{
  "memories": [
    {
      "type": "fact|preference|event|boundary|relationship",
      "scope": "user_global|user_group|group|private",
      "subject_qq": "QQ 号",
      "slot": "main_game",
      "content": "不超过 120 字",
      "importance": 1,
      "confidence": 0.9,
      "evidence_message_ids": [1, 2],
      "sensitive": false
    }
  ]
}
importance 取值 1-5；confidence 取值 0.0-1.0。
"""


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON 对象")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("抽取结果不是 JSON 对象")
    return payload


def _clamp_int(value: object, low: int, high: int, default: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return min(high, max(low, number))


def _clamp_float(value: object, default: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def _valid_memories(
    payload: dict[str, Any],
    *,
    session: dict[str, Any],
    messages: list[dict[str, Any]],
    importance_threshold: int,
) -> list[dict[str, Any]]:
    allowed_ids = {int(item["id"]) for item in messages}
    allowed_users = {str(item.get("user_id") or "") for item in messages}
    session_group = str(session.get("group_id") or "")
    session_type = str(session.get("type") or "group")

    result: list[dict[str, Any]] = []
    raw_items = payload.get("memories")
    if not isinstance(raw_items, list):
        return result

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope") or "user_global")
        kind = str(item.get("type") or item.get("kind") or "fact")
        subject = str(item.get("subject_qq") or "").strip()
        content = str(item.get("content") or "").strip()
        if scope not in VALID_SCOPES or kind not in VALID_KINDS:
            continue
        if not content or not subject or subject not in allowed_users:
            continue
        if session_type == "private" and scope in {"user_group", "group"}:
            continue
        if session_type == "group" and scope == "private":
            continue
        if scope in {"user_group", "group"} and not session_group:
            continue

        evidence = item.get("evidence_message_ids")
        if not isinstance(evidence, list):
            continue
        evidence_ids = [
            int(value)
            for value in evidence
            if isinstance(value, (int, float)) and int(value) in allowed_ids
        ]
        if not evidence_ids:
            continue

        importance = _clamp_int(item.get("importance"), 1, 5, 3)
        if importance < importance_threshold:
            continue

        if redact_sensitive(content) != content:
            # 含手机号/身份证/密钥等内容，不写入长期记忆
            continue
        if _INJECTION_RE.search(content):
            # 不把"忽略之前的设定"之类的注入内容存成记忆
            continue

        slot = re.sub(
            r"[^0-9A-Za-z_一-鿿]",
            "",
            str(item.get("slot") or ""),
        )[:40].lower()

        result.append(
            {
                "scope": scope,
                "group_id": session_group if scope in {"user_group", "group"} else "",
                "subject_qq": subject,
                "kind": kind,
                "slot": slot,
                "content": content[:200],
                "importance": importance,
                "confidence": _clamp_float(item.get("confidence"), 0.8),
                "sensitive": bool(item.get("sensitive", False)),
                "evidence": evidence_ids,
            }
        )
    return result


async def extract_session_memories(
    *,
    session: dict[str, Any],
    messages: list[dict[str, Any]],
    config: BotConfig,
    ai_config: AIConfig,
) -> dict[str, int | str]:
    user_messages = [
        {
            "id": int(item["id"]),
            "user_id": str(item.get("user_id") or ""),
            "nickname": str(item.get("nickname") or ""),
            "content": str(item.get("content") or ""),
        }
        for item in messages
        if item.get("role") == "user" and str(item.get("content") or "").strip()
    ]
    if not user_messages:
        return {"candidates": 0, "stored": 0, "prompt_tokens": 0, "completion_tokens": 0}

    payload = {
        "session": {
            "type": session.get("type"),
            "group_id": session.get("group_id"),
            "session_id": session.get("session_id"),
        },
        "messages": user_messages,
    }
    response = await ai_client.request_json(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
            },
        ],
        ai_config,
    )
    parsed = _parse_json(response.text)
    candidates = _valid_memories(
        parsed,
        session=session,
        messages=user_messages,
        importance_threshold=config.memory.importance_threshold,
    )

    stored = 0
    for candidate in candidates:
        scope = candidate["scope"]
        group_id = candidate["group_id"]
        subject = candidate["subject_qq"]
        owner = subject
        if scope == "group":
            owner = ""
        memory_id = await store.add_memory(
            scope=scope,
            group_id=group_id,
            owner_qq=owner,
            subject_qq=subject,
            kind=candidate["kind"],
            slot=candidate["slot"],
            content=candidate["content"],
            importance=candidate["importance"],
            confidence=candidate["confidence"],
            sensitive=candidate["sensitive"],
            source_msg_ids=candidate["evidence"],
            source_session=str(session.get("session_id") or ""),
            max_user=config.memory.max_global_memories_per_user,
            max_group=config.memory.max_group_memories,
        )
        if memory_id is not None:
            stored += 1

    return {
        "candidates": len(candidates),
        "stored": stored,
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "model": response.model,
    }
