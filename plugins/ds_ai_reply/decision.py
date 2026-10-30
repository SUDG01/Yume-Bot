"""聊天意愿决策：让模型读空气，决定要不要主动接话。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from . import ai_client
from .config import AIConfig, BotConfig

_SYSTEM_PROMPT = """你是群聊里的一个普通群友。根据最近的聊天，判断现在要不要主动插一句话。
只输出 JSON：{"speak": true/false, "reason": "为什么", "angle": "如果要说，想说什么方向"}
规则：
- 不要每条都接，不要为了刷存在感而说话。
- 别人在连续对话、话题严肃、你插不上话时，speak=false。
- 话题有趣、有人提问、冷场、你有相关记忆或想吐槽时，可以考虑 true。
- 被 @ 或叫到名字的情况不会走到这里。
- reason 不超过 30 字；angle 是如果要说，想说什么方向，不超过 30 字。
你的行为风格：
{behavior_style}
"""


@dataclass(frozen=True)
class WillingnessDecision:
    speak: bool
    reason: str = ""
    angle: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("决策模型没有返回 JSON")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("决策结果不是 JSON 对象")
    return payload


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "是", "说"}
    return bool(value)


def _format_recent(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in messages[-8:]:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if str(item.get("role")) == "assistant":
            speaker = "你"
        else:
            speaker = str(item.get("nickname") or "群友")
        lines.append(f"{speaker}：{content[:300]}")
    return "\n".join(lines) or "（还没有聊天记录）"


async def decide_willingness(
    *,
    config: BotConfig,
    ai_config: AIConfig,
    display_name: str,
    current_text: str,
    recent_messages: list[dict[str, Any]],
) -> WillingnessDecision:
    prompt = _SYSTEM_PROMPT.replace(
        "{behavior_style}", config.persona.behavior_style
    )
    user_content = (
        f"最近聊天：\n{_format_recent(recent_messages)}\n\n"
        f"当前消息：\n{display_name}：{current_text[:500]}\n\n"
        "现在要不要接话？只输出 JSON。"
    )
    result = await ai_client.request_json(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ],
        ai_config,
        max_tokens=160,
    )
    try:
        payload = _parse_json(result.text)
    except (ValueError, json.JSONDecodeError):
        return WillingnessDecision(
            speak=False,
            reason="决策解析失败",
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )

    speak = _as_bool(payload.get("speak", False))
    reason = str(payload.get("reason") or "")[:80].strip()
    angle = str(payload.get("angle") or "")[:80].strip()
    return WillingnessDecision(
        speak=speak,
        reason=reason,
        angle=angle,
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
