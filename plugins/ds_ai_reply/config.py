"""aiconfig.toml / bot_config.toml 的加载与校验。

- aiconfig.toml：模型基础设施（接口、密钥、生成参数、上下文预算、抽取模型）
- bot_config.toml：人格、触发、群管理、限流、记忆策略、隐私
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PLUGIN_DIR = Path(__file__).parent
AI_CONFIG_PATH = PLUGIN_DIR / "aiconfig.toml"
BOT_CONFIG_PATH = PLUGIN_DIR / "bot_config.toml"
BOT_LOCAL_CONFIG_PATH = PLUGIN_DIR / "bot_config.local.toml"

DEFAULT_SYSTEM_PROMPT = (
    "你是 YumeBot（小Yume），一只住在 QQ 群里的猫娘。"
    "你不是客服，也不是写作助手，只是群里的一个普通群友。"
    "不确定的事情如实说不知道，不要编造。"
)

DEFAULT_BEHAVIOR_STYLE = (
    "你是群里的普通群友，不是随时待命的助手。"
    "不会没话找话，也不每条消息都接；读空气，觉得有意思再开口。"
    "有自己的小情绪，但不刻意卖萌，也不讨好。"
)

DEFAULT_REPLY_STYLE = (
    "像真人在 QQ 上打字：通常 1-2 句，最多 3 句；口语化，可以省略主语。"
    "不用列表、编号、Markdown、代码块；不写动作或心理描写（如（歪头）（小声））。"
    "不要用“首先/其次/最后/总结一下/最省事的办法是”这类条理词，"
    "也不要用“作为一个…”“我理解你…”这种助手腔，不要主动问“还需要什么”。"
    "不要每句都带“喵”，一句里最多一个语气词。"
    "不总结、不说教、不主动提出验证步骤，也不解释自己的系统或消息来源。"
)

VALID_PRIVATE_MODES = {"always", "whitelist", "off"}
VALID_GROUP_MODES = {"mention", "mention_or_wake", "all"}


class ConfigError(RuntimeError):
    """配置文件缺失、格式错误或字段非法。"""


def _section(data: dict, name: str) -> dict:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def _text(value: object, default: str = "") -> str:
    return str(value).strip() if value is not None else default


def _float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float
    trust_env: bool
    max_retries: int


@dataclass(frozen=True)
class GenerationConfig:
    temperature: float
    top_p: float
    max_tokens: int


@dataclass(frozen=True)
class ContextConfig:
    window_tokens: int
    chars_per_token: float
    reserve_output_tokens: int
    memory_tokens: int
    profile_tokens: int
    recent_message_tokens: int
    max_recent_messages: int
    cross_session_tokens: int


@dataclass(frozen=True)
class MemoryLLMConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout: float


@dataclass(frozen=True)
class VisionConfig:
    enabled: bool
    max_images: int
    max_bytes: int
    timeout: float


@dataclass(frozen=True)
class AIConfig:
    provider: ProviderConfig
    generation: GenerationConfig
    context: ContextConfig
    memory_llm: MemoryLLMConfig
    vision: VisionConfig


@dataclass(frozen=True)
class GeneralConfig:
    timezone: str
    data_dir: Path


@dataclass(frozen=True)
class PersonaConfig:
    name: str
    system_prompt: str
    behavior_style: str
    reply_style: str
    max_reply_chars: int


@dataclass(frozen=True)
class TriggerConfig:
    private_mode: str
    group_mode: str
    wake_words: list[str]
    random_reply_probability: float
    random_reply_cooldown_seconds: int


@dataclass(frozen=True)
class GroupOverride:
    enabled: bool | None = None
    trigger_mode: str | None = None
    random_reply_probability: float | None = None
    quiet_hours: list[str] = field(default_factory=list)
    persona_extra: str = ""


@dataclass(frozen=True)
class GroupsConfig:
    mode: str
    whitelist: list[str]
    blacklist: list[str]
    overrides: dict[str, GroupOverride]


@dataclass(frozen=True)
class PermissionsConfig:
    bot_admins: list[str]
    group_admin_can_toggle: bool


@dataclass(frozen=True)
class RateLimitConfig:
    per_user_per_minute: int
    per_group_per_minute: int
    cooldown_seconds: int
    max_concurrency: int
    daily_token_budget: int


@dataclass(frozen=True)
class ReplyConfig:
    quote: bool
    max_segments: int
    group_max_chars: int
    private_max_chars: int
    random_max_chars: int


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool
    raw_retention_days: int
    max_global_memories_per_user: int
    max_group_memories: int
    extraction_enabled: bool
    importance_threshold: int
    idle_seconds: int
    batch_size: int
    mix_context: bool
    private_chat_memory: bool
    cross_session_context: bool
    cross_session_limit: int
    cross_session_hours: int
    cross_session_include_private: bool
    cross_session_include_others: bool
    allow_user_self_view: bool
    allow_user_forget: bool


@dataclass(frozen=True)
class PrivacyConfig:
    store_private_messages: bool
    redact_sensitive: bool


@dataclass(frozen=True)
class DashboardConfig:
    enabled: bool
    host: str
    port: int
    token: str
    retention_days: int
    show_prompt: bool


@dataclass(frozen=True)
class BotConfig:
    general: GeneralConfig
    persona: PersonaConfig
    trigger: TriggerConfig
    groups: GroupsConfig
    permissions: PermissionsConfig
    rate_limit: RateLimitConfig
    reply: ReplyConfig
    memory: MemoryConfig
    privacy: PrivacyConfig
    dashboard: DashboardConfig


def _read_toml(path: Path, label: str) -> dict:
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except FileNotFoundError as error:
        raise ConfigError(f"找不到 {label}，请检查插件目录") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{label} 格式错误：{error}") from error


def load_ai_config(path: Path = AI_CONFIG_PATH) -> AIConfig:
    data = _read_toml(path, "aiconfig.toml")
    provider = _section(data, "provider")
    generation = _section(data, "generation")
    context = _section(data, "context")
    memory_llm = _section(data, "memory_llm")
    vision = _section(data, "vision")

    api_key = _text(provider.get("api_key"))
    if not api_key:
        raise ConfigError("aiconfig.toml 里的 provider.api_key 还没有填写")

    base_url = _text(provider.get("base_url"), "https://api.deepseek.com")
    return AIConfig(
        provider=ProviderConfig(
            base_url=base_url,
            api_key=api_key,
            model=_text(provider.get("model"), "deepseek-chat"),
            timeout=max(1.0, _float(provider.get("timeout"), 60.0)),
            trust_env=_bool(provider.get("trust_env"), False),
            max_retries=max(0, _int(provider.get("max_retries"), 1)),
        ),
        generation=GenerationConfig(
            temperature=min(2.0, max(0.0, _float(generation.get("temperature"), 0.8))),
            top_p=min(1.0, max(0.01, _float(generation.get("top_p"), 0.95))),
            max_tokens=max(64, _int(generation.get("max_tokens"), 800)),
        ),
        context=ContextConfig(
            window_tokens=max(1024, _int(context.get("window_tokens"), 32768)),
            chars_per_token=max(0.5, _float(context.get("chars_per_token"), 1.5)),
            reserve_output_tokens=max(
                128, _int(context.get("reserve_output_tokens"), 1200)
            ),
            memory_tokens=max(0, _int(context.get("memory_tokens"), 2500)),
            profile_tokens=max(0, _int(context.get("profile_tokens"), 1200)),
            recent_message_tokens=max(
                512, _int(context.get("recent_message_tokens"), 10000)
            ),
            max_recent_messages=min(
                200, max(4, _int(context.get("max_recent_messages"), 40))
            ),
            cross_session_tokens=max(
                0, _int(context.get("cross_session_tokens"), 1000)
            ),
        ),
        memory_llm=MemoryLLMConfig(
            base_url=_text(memory_llm.get("base_url")) or base_url,
            api_key=_text(memory_llm.get("api_key")) or api_key,
            model=_text(memory_llm.get("model"), _text(provider.get("model"), "deepseek-chat")),
            temperature=min(
                2.0, max(0.0, _float(memory_llm.get("temperature"), 0.1))
            ),
            max_tokens=max(256, _int(memory_llm.get("max_tokens"), 1200)),
            timeout=max(5.0, _float(memory_llm.get("timeout"), 40.0)),
        ),
        vision=VisionConfig(
            enabled=_bool(vision.get("enabled"), False),
            max_images=min(4, max(1, _int(vision.get("max_images"), 2))),
            max_bytes=min(
                20 * 1024 * 1024,
                max(64 * 1024, _int(vision.get("max_bytes"), 8 * 1024 * 1024)),
            ),
            timeout=max(2.0, _float(vision.get("timeout"), 15.0)),
        ),
    )


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：local 覆盖 base，嵌套 section 合并而不是整段替换。"""
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = value
    return result


def _load_bot_data(path: Path) -> dict:
    data = _read_toml(path, "bot_config.toml")
    local_path = path.with_name("bot_config.local.toml")
    if local_path.exists():
        local = _read_toml(local_path, "bot_config.local.toml")
        data = _deep_merge(data, local)
    return data


def load_bot_config(path: Path = BOT_CONFIG_PATH) -> BotConfig:
    data = _load_bot_data(path)
    general = _section(data, "general")
    persona = _section(data, "persona")
    trigger = _section(data, "trigger")
    groups = _section(data, "groups")
    permissions = _section(data, "permissions")
    rate_limit = _section(data, "rate_limit")
    reply = _section(data, "reply")
    memory = _section(data, "memory")
    privacy = _section(data, "privacy")
    dashboard = _section(data, "dashboard")

    private_mode = _text(trigger.get("private_mode"), "always")
    group_mode = _text(trigger.get("group_mode"), "mention")
    if private_mode not in VALID_PRIVATE_MODES:
        raise ConfigError(f"trigger.private_mode 只能是 {sorted(VALID_PRIVATE_MODES)}")
    if group_mode not in VALID_GROUP_MODES:
        raise ConfigError(f"trigger.group_mode 只能是 {sorted(VALID_GROUP_MODES)}")

    groups_mode = _text(groups.get("mode"), "all")
    if groups_mode not in {"all", "whitelist", "blacklist"}:
        raise ConfigError("groups.mode 只能是 all / whitelist / blacklist")

    overrides: dict[str, GroupOverride] = {}
    raw_overrides = groups.get("overrides")
    if isinstance(raw_overrides, dict):
        for group_id, raw in raw_overrides.items():
            if not isinstance(raw, dict):
                continue
            enabled = raw.get("enabled")
            overrides[str(group_id)] = GroupOverride(
                enabled=enabled if isinstance(enabled, bool) else None,
                trigger_mode=(
                    _text(raw.get("trigger_mode"))
                    if _text(raw.get("trigger_mode")) in VALID_GROUP_MODES
                    else None
                ),
                random_reply_probability=(
                    min(1.0, max(0.0, _float(raw.get("random_reply_probability"), 0.0)))
                    if "random_reply_probability" in raw
                    else None
                ),
                quiet_hours=_str_list(raw.get("quiet_hours")),
                persona_extra=_text(raw.get("persona_extra")),
            )

    data_dir = Path(_text(general.get("data_dir"), "."))
    if not data_dir.is_absolute():
        data_dir = (PLUGIN_DIR / data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    return BotConfig(
        general=GeneralConfig(
            timezone=_text(general.get("timezone"), "Asia/Shanghai") or "Asia/Shanghai",
            data_dir=data_dir,
        ),
        persona=PersonaConfig(
            name=_text(persona.get("name"), "小Yume") or "小Yume",
            system_prompt=_text(persona.get("system_prompt")) or DEFAULT_SYSTEM_PROMPT,
            behavior_style=_text(persona.get("behavior_style"))
            or DEFAULT_BEHAVIOR_STYLE,
            reply_style=_text(persona.get("reply_style")) or DEFAULT_REPLY_STYLE,
            max_reply_chars=min(
                4000, max(20, _int(persona.get("max_reply_chars"), 300))
            ),
        ),
        trigger=TriggerConfig(
            private_mode=private_mode,
            group_mode=group_mode,
            wake_words=_str_list(trigger.get("wake_words")),
            random_reply_probability=min(
                1.0, max(0.0, _float(trigger.get("random_reply_probability"), 0.0))
            ),
            random_reply_cooldown_seconds=max(
                0, _int(trigger.get("random_reply_cooldown_seconds"), 60)
            ),
        ),
        groups=GroupsConfig(
            mode=groups_mode,
            whitelist=_str_list(groups.get("whitelist")),
            blacklist=_str_list(groups.get("blacklist")),
            overrides=overrides,
        ),
        permissions=PermissionsConfig(
            bot_admins=_str_list(permissions.get("bot_admins")),
            group_admin_can_toggle=_bool(
                permissions.get("group_admin_can_toggle"), True
            ),
        ),
        rate_limit=RateLimitConfig(
            per_user_per_minute=max(0, _int(rate_limit.get("per_user_per_minute"), 3)),
            per_group_per_minute=max(
                0, _int(rate_limit.get("per_group_per_minute"), 8)
            ),
            cooldown_seconds=max(0, _int(rate_limit.get("cooldown_seconds"), 4)),
            max_concurrency=max(1, _int(rate_limit.get("max_concurrency"), 2)),
            daily_token_budget=max(
                0, _int(rate_limit.get("daily_token_budget"), 2_000_000)
            ),
        ),
        reply=ReplyConfig(
            quote=_bool(reply.get("quote"), True),
            max_segments=min(5, max(1, _int(reply.get("max_segments"), 3))),
            group_max_chars=min(
                2000, max(20, _int(reply.get("group_max_chars"), 150))
            ),
            private_max_chars=min(
                4000, max(20, _int(reply.get("private_max_chars"), 300))
            ),
            random_max_chars=min(
                500, max(10, _int(reply.get("random_max_chars"), 80))
            ),
        ),
        memory=MemoryConfig(
            enabled=_bool(memory.get("enabled"), True),
            raw_retention_days=min(
                3650, max(1, _int(memory.get("raw_retention_days"), 30))
            ),
            max_global_memories_per_user=max(
                10, _int(memory.get("max_global_memories_per_user"), 300)
            ),
            max_group_memories=max(
                10, _int(memory.get("max_group_memories"), 500)
            ),
            extraction_enabled=_bool(memory.get("extraction_enabled"), True),
            importance_threshold=min(
                5, max(1, _int(memory.get("importance_threshold"), 2))
            ),
            idle_seconds=max(5, _int(memory.get("idle_seconds"), 45)),
            batch_size=min(50, max(1, _int(memory.get("batch_size"), 12))),
            mix_context=_bool(memory.get("mix_context"), True),
            private_chat_memory=_bool(memory.get("private_chat_memory"), True),
            cross_session_context=_bool(
                memory.get("cross_session_context"), True
            ),
            cross_session_limit=min(
                50, max(1, _int(memory.get("cross_session_limit"), 12))
            ),
            cross_session_hours=min(
                720, max(1, _int(memory.get("cross_session_hours"), 24))
            ),
            cross_session_include_private=_bool(
                memory.get("cross_session_include_private"), False
            ),
            cross_session_include_others=_bool(
                memory.get("cross_session_include_others"), False
            ),
            allow_user_self_view=_bool(memory.get("allow_user_self_view"), True),
            allow_user_forget=_bool(memory.get("allow_user_forget"), True),
        ),
        privacy=PrivacyConfig(
            store_private_messages=_bool(
                privacy.get("store_private_messages"), True
            ),
            redact_sensitive=_bool(privacy.get("redact_sensitive"), True),
        ),
        dashboard=DashboardConfig(
            enabled=_bool(dashboard.get("enabled"), True),
            host=_text(dashboard.get("host"), "127.0.0.1") or "127.0.0.1",
            port=min(65535, max(1, _int(dashboard.get("port"), 3133))),
            token=_text(dashboard.get("token")),
            retention_days=min(
                365, max(1, _int(dashboard.get("retention_days"), 7))
            ),
            show_prompt=_bool(dashboard.get("show_prompt"), True),
        ),
    )


_ai_config: AIConfig | None = None
_bot_config: BotConfig | None = None
_bot_mtime: tuple[float, float | None] | None = None


def get_ai_config(force: bool = False) -> AIConfig:
    global _ai_config
    if force or _ai_config is None:
        _ai_config = load_ai_config()
    return _ai_config


def _bot_config_mtimes() -> tuple[float, float | None]:
    base = BOT_CONFIG_PATH.stat().st_mtime
    local = (
        BOT_LOCAL_CONFIG_PATH.stat().st_mtime
        if BOT_LOCAL_CONFIG_PATH.exists()
        else None
    )
    return base, local


def get_bot_config(force: bool = False) -> BotConfig:
    """按 mtime 自动感知 bot_config.toml / bot_config.local.toml 的变化。

    文件改错时保留上一份可用配置，只记录一条告警，避免整条聊天链路失效。
    """
    global _bot_config, _bot_mtime
    try:
        signature = _bot_config_mtimes()
    except FileNotFoundError as error:
        raise ConfigError("找不到 bot_config.toml，请检查插件目录") from error

    if force or _bot_config is None or _bot_mtime != signature:
        try:
            loaded = load_bot_config()
        except ConfigError as error:
            if _bot_config is None:
                raise
            # 同一个错误 mtime 只尝试一次，修好文件后 mtime 变化会再次加载
            _bot_mtime = signature
            from nonebot import logger

            logger.warning(f"bot_config 加载失败，继续使用上一份配置：{error}")
            return _bot_config
        _bot_config = loaded
        _bot_mtime = signature
    return _bot_config


def reload_bot_config() -> BotConfig:
    """显式重载；失败时抛出 ConfigError，由命令回复给管理员。"""
    global _bot_config, _bot_mtime
    loaded = load_bot_config()
    _bot_config = loaded
    _bot_mtime = _bot_config_mtimes()
    return loaded
