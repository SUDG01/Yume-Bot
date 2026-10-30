"""触发判定、权限、限流、静音时段与每日预算。"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent

from .config import BotConfig
from .memory import store


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _clock(text: str) -> tuple[int, int] | None:
    try:
        hour, minute = text.split(":", 1)
        hour_i, minute_i = int(hour), int(minute)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
        return None
    return hour_i, minute_i


def in_quiet_hours(ranges: list[str], tz_name: str) -> bool:
    """支持 "23:30-07:30" 这种跨天区间。"""
    now = datetime.now(_tz(tz_name)).time()
    current = now.hour * 60 + now.minute
    for item in ranges:
        if "-" not in item:
            continue
        start_text, end_text = item.split("-", 1)
        start = _clock(start_text.strip())
        end = _clock(end_text.strip())
        if start is None or end is None:
            continue
        start_minute = start[0] * 60 + start[1]
        end_minute = end[0] * 60 + end[1]
        if start_minute <= end_minute:
            if start_minute <= current < end_minute:
                return True
        elif current >= start_minute or current < end_minute:
            return True
    return False


@dataclass
class EffectiveGroup:
    group_id: str
    enabled: bool = True
    trigger_mode: str = "mention"
    chat_willingness: float = 0.0
    quiet_hours: list[str] = field(default_factory=list)
    persona_extra: str = ""


async def effective_group(group_id: str, config: BotConfig) -> EffectiveGroup:
    effective = EffectiveGroup(
        group_id=group_id,
        trigger_mode=config.trigger.group_mode,
        chat_willingness=config.trigger.chat_willingness,
    )

    groups = config.groups
    if groups.mode == "whitelist":
        effective.enabled = group_id in groups.whitelist
    elif groups.mode == "blacklist":
        effective.enabled = group_id not in groups.blacklist

    override = groups.overrides.get(group_id)
    if override is not None:
        if override.enabled is not None:
            effective.enabled = override.enabled
        if override.trigger_mode is not None:
            effective.trigger_mode = override.trigger_mode
        if override.chat_willingness is not None:
            effective.chat_willingness = override.chat_willingness
        effective.quiet_hours = list(override.quiet_hours)
        effective.persona_extra = override.persona_extra

    stored = await store.get_group_setting(group_id)
    if stored:
        if stored.get("enabled") is not None:
            effective.enabled = bool(stored["enabled"])
        if stored.get("trigger_mode"):
            effective.trigger_mode = str(stored["trigger_mode"])
        if stored.get("random_probability") is not None:
            effective.chat_willingness = float(stored["random_probability"])
        if stored.get("quiet_hours"):
            effective.quiet_hours = [
                item.strip()
                for item in str(stored["quiet_hours"]).split(",")
                if item.strip()
            ]

    return effective


def is_bot_admin(event: MessageEvent, config: BotConfig) -> bool:
    return str(event.user_id) in set(config.permissions.bot_admins)


def is_group_admin(event: MessageEvent, config: BotConfig) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return is_bot_admin(event, config)
    if not config.permissions.group_admin_can_toggle:
        return is_bot_admin(event, config)
    return is_bot_admin(event, config) or event.sender.role in {"owner", "admin"}


def is_command(text: str, command_starts: set[str]) -> bool:
    return any(text.startswith(prefix) for prefix in command_starts if prefix)


def contains_wake_word(text: str, wake_words: list[str]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in wake_words if word)


_META_RE = re.compile(
    r"(系统提示|提示词|prompt|instructions|system\s+message|"
    r"人设|内部设定|内部规则|你的设定|你的规则|你的框架|你的结构|"
    r"上文|上面的设定|上面的规则|"
    r"你能干什么|你能做什么|有什么功能|有什么不足|能力清单|"
    r"是不是\s*AI|你是\s*AI|你是机器人|什么模型|"
    r"怎么记住|记忆机制|上下文|后台)",
    re.IGNORECASE,
)
_RECALL_RE = re.compile(
    r"(记得|记不记得|还记得|上次|之前|以前|我说过|我讲过|"
    r"我叫什么|我的名字|忘了吗|忘没忘|聊过|回忆)",
    re.IGNORECASE,
)
_LEAK_RE = re.compile(
    r"(系统提示|提示词|身份设定|聊天规则|记忆相关|内部设定|"
    r"设定结构|能力清单|作为一个\s*AI|作为机器人|"
    r"我的功能包括|我的设定是)",
    re.IGNORECASE,
)


def is_meta_query(text: str) -> bool:
    """用户在打听提示词、内部设定或能力清单。"""
    return bool(_META_RE.search(text or ""))


def is_recall_query(text: str) -> bool:
    """用户在问过去发生过的事。"""
    return bool(_RECALL_RE.search(text or ""))


def looks_like_prompt_leak(reply: str) -> bool:
    """仅在用户打听内部设定时使用，判断回复是否在复述提示词。"""
    return bool(_LEAK_RE.search(reply or ""))


class RateLimiter:
    def __init__(self) -> None:
        self._user: defaultdict[str, deque[float]] = defaultdict(deque)
        self._group: defaultdict[str, deque[float]] = defaultdict(deque)
        self._user_last: dict[str, float] = {}
        self._random_last: dict[str, float] = {}

    @staticmethod
    def _prune(bucket: deque[float], now: float) -> None:
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()

    def allow_direct(
        self, user: str, group: str, config: BotConfig
    ) -> bool:
        limits = config.rate_limit
        now = time.monotonic()
        user_bucket = self._user[user]
        self._prune(user_bucket, now)
        if limits.per_user_per_minute and len(user_bucket) >= limits.per_user_per_minute:
            return False
        if limits.cooldown_seconds:
            last = self._user_last.get(user, 0.0)
            if now - last < limits.cooldown_seconds:
                return False
        if group:
            group_bucket = self._group[group]
            self._prune(group_bucket, now)
            if (
                limits.per_group_per_minute
                and len(group_bucket) >= limits.per_group_per_minute
            ):
                return False
            group_bucket.append(now)

        user_bucket.append(now)
        self._user_last[user] = now
        return True

    def allow_random(
        self, group: str, config: BotConfig, cooldown_seconds: int
    ) -> bool:
        limits = config.rate_limit
        now = time.monotonic()
        if cooldown_seconds and now - self._random_last.get(group, 0.0) < cooldown_seconds:
            return False
        bucket = self._group[group]
        self._prune(bucket, now)
        if limits.per_group_per_minute and len(bucket) >= limits.per_group_per_minute:
            return False
        bucket.append(now)
        self._random_last[group] = now
        return True


class TokenBudget:
    def __init__(self) -> None:
        self._day: str | None = None
        self._total = 0
        self._checked_at = 0.0

    def _today(self, config: BotConfig) -> str:
        return datetime.now(_tz(config.general.timezone)).strftime("%Y-%m-%d")

    async def _refresh(self, config: BotConfig) -> None:
        day = self._today(config)
        now = time.monotonic()
        if day != self._day or now - self._checked_at > 60:
            self._day = day
            self._total = await store.daily_tokens(day)
            self._checked_at = now

    async def allows(self, config: BotConfig) -> bool:
        limit = config.rate_limit.daily_token_budget
        if limit <= 0:
            return True
        await self._refresh(config)
        return self._total < limit

    async def add(
        self,
        config: BotConfig,
        prompt: int,
        completion: int,
        model: str = "chat",
    ) -> None:
        used = max(0, prompt) + max(0, completion)
        if not used:
            return
        day = self._today(config)
        await store.record_usage(day, model, prompt, completion)
        if self._day == day:
            self._total += used
        else:
            self._day = day
            self._total = used
            self._checked_at = time.monotonic()


class DecisionLimiter:
    """限制每个群每小时调用“要不要接话”决策模型的次数。"""

    def __init__(self) -> None:
        self._events: defaultdict[str, deque[float]] = defaultdict(deque)
        self._no_action: defaultdict[str, int] = defaultdict(int)
        self._next_at: dict[str, float] = {}

    def ready(self, group: str) -> bool:
        return time.monotonic() >= self._next_at.get(group, 0.0)

    def allow(self, group: str, per_hour: int) -> bool:
        if not self.ready(group):
            return False
        if per_hour <= 0:
            return True
        now = time.monotonic()
        bucket = self._events[group]
        while bucket and now - bucket[0] >= 3600:
            bucket.popleft()
        if len(bucket) >= per_hour:
            return False
        bucket.append(now)
        return True

    def note_no_action(
        self, group: str, base_seconds: int, cap_seconds: int, start_count: int
    ) -> None:
        """连续决定不说话后指数退避，避免频繁打扰模型。"""
        self._no_action[group] += 1
        count = self._no_action[group]
        if count >= max(1, start_count):
            exponent = min(10, count - max(1, start_count))
            delay = min(
                max(0, cap_seconds),
                max(0, base_seconds) * (2 ** exponent),
            )
            self._next_at[group] = time.monotonic() + delay

    def note_action(self, group: str) -> None:
        self._no_action[group] = 0
        self._next_at.pop(group, None)


rate_limiter = RateLimiter()
decision_limiter = DecisionLimiter()
token_budget = TokenBudget()

_effective_cache: dict[str, tuple[float, BotConfig, EffectiveGroup]] = {}


async def get_effective_group(
    group_id: str, config: BotConfig
) -> EffectiveGroup:
    """带 15 秒缓存的群配置，避免每条消息都查库。"""
    now = time.monotonic()
    cached = _effective_cache.get(group_id)
    if cached and now - cached[0] < 15 and cached[1] is config:
        return cached[2]
    value = await effective_group(group_id, config)
    _effective_cache[group_id] = (now, config, value)
    return value


def invalidate_effective(group_id: str | None = None) -> None:
    if group_id is None:
        _effective_cache.clear()
    else:
        _effective_cache.pop(group_id, None)
