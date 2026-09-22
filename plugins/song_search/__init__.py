import re
import secrets

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import to_me

from src.tools.song_search import (
    ChartMatch,
    chart_level_text,
    search_charts,
    search_songs,
)


LEVEL_NAMES = {0: "绿", 1: "黄", 2: "红", 3: "紫", 4: "白"}
TYPE_TOKENS = {
    "dx": "dx",
    "标准": "standard",
    "std": "standard",
    "st": "standard",
    "standard": "standard",
}
LEVEL_TOKENS = {
    "绿": 0,
    "basic": 0,
    "黄": 1,
    "advanced": 1,
    "红": 2,
    "expert": 2,
    "紫": 3,
    "master": 3,
    "白": 4,
    "remaster": 4,
    "re:master": 4,
}
NUMBER_PATTERN = re.compile(r"^\d+(?:\.\d+)?$")
DISPLAY_LEVEL_PATTERN = re.compile(r"^\d{1,2}\+?$")

search_cmd = on_command(
    "查歌",
    aliases={"搜歌", "searchsong"},
    rule=to_me(),
    priority=5,
    block=True,
)
constant_cmd = on_command(
    "定数查歌",
    aliases={"定数"},
    rule=to_me(),
    priority=5,
    block=True,
)
random_cmd = on_command(
    "随歌",
    aliases={"随个", "随机歌曲"},
    rule=to_me(),
    priority=5,
    block=True,
)


def _chart_type_label(chart_type: str) -> str:
    return "DX" if chart_type == "dx" else "STD"


def _format_chart(match: ChartMatch) -> str:
    chart = match.chart
    difficulty = LEVEL_NAMES.get(chart.level, f"难度{chart.level}")
    return (
        f"[{_chart_type_label(chart.type)} {difficulty}] "
        f"{chart.difficulty:g}  #{match.song.id} {match.song.title}"
    )


def _parse_constant_filters(
    text: str,
) -> tuple[float, float, str | None, int | None]:
    chart_type: str | None = None
    level_index: int | None = None
    values: list[float] = []

    tokens = text.lower().replace("－", "-").split()
    for token in tokens:
        if token in TYPE_TOKENS:
            if chart_type is not None:
                raise ValueError
            chart_type = TYPE_TOKENS[token]
            continue
        if token in LEVEL_TOKENS:
            if level_index is not None:
                raise ValueError
            level_index = LEVEL_TOKENS[token]
            continue
        if "-" in token:
            pieces = token.split("-", 1)
            if len(pieces) != 2 or not all(NUMBER_PATTERN.fullmatch(piece) for piece in pieces):
                raise ValueError
            values.extend(float(piece) for piece in pieces)
            continue
        if NUMBER_PATTERN.fullmatch(token):
            values.append(float(token))
            continue
        raise ValueError

    if not 1 <= len(values) <= 2 or any(value <= 0 or value > 20 for value in values):
        raise ValueError
    if len(values) == 1:
        return values[0], values[0], chart_type, level_index
    return min(values), max(values), chart_type, level_index


def _parse_random_filters(
    text: str,
) -> tuple[str | None, int | None, str | None, float | None, str | None]:
    chart_type: str | None = None
    level_index: int | None = None
    display_level: str | None = None
    constant: float | None = None
    genre_parts: list[str] = []

    for raw_token in text.replace("＋", "+").split():
        token = raw_token.lower()
        if token in TYPE_TOKENS:
            if chart_type is not None:
                raise ValueError
            chart_type = TYPE_TOKENS[token]
        elif token in LEVEL_TOKENS:
            if level_index is not None:
                raise ValueError
            level_index = LEVEL_TOKENS[token]
        elif DISPLAY_LEVEL_PATTERN.fullmatch(token):
            if display_level is not None or constant is not None:
                raise ValueError
            display_level = token
        elif NUMBER_PATTERN.fullmatch(token) and "." in token:
            if constant is not None or display_level is not None:
                raise ValueError
            constant = float(token)
        else:
            genre_parts.append(raw_token)

    genre_query = " ".join(genre_parts).strip() or None
    return chart_type, level_index, display_level, constant, genre_query


@search_cmd.handle()
async def handle_search(args: Message = CommandArg()):
    query = args.extract_plain_text().strip()
    if not query:
        await search_cmd.finish("使用方法：/查歌 <曲名或别名关键词>")
        return

    matches = search_songs(query, limit=10)
    if not matches:
        await search_cmd.finish(f"喵？没有找到包含「{query}」的歌曲")
        return

    lines = [f"找到 {len(matches)} 个最接近的结果："]
    for match in matches:
        key_charts = [
            chart
            for chart in match.song.charts
            if chart.level >= 3
        ]
        chart_text = " / ".join(
            f"{_chart_type_label(chart.type)}{LEVEL_NAMES.get(chart.level, '?')}"
            f"{chart.difficulty:g}"
            for chart in key_charts
        )
        lines.append(
            f"#{match.song.id}  {match.song.title}"
            + (f"\n  {chart_text}" if chart_text else "")
        )
    lines.append("使用 /info <ID> 查看完整信息")
    await search_cmd.finish("\n".join(lines))


@constant_cmd.handle()
async def handle_constant_search(args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    try:
        low, high, chart_type, level_index = _parse_constant_filters(raw)
    except ValueError:
        await constant_cmd.finish(
            "使用方法：\n"
            "/定数查歌 13.7\n"
            "/定数查歌 13.5 14.0\n"
            "/定数查歌 14.0-14.4 DX 紫"
        )
        return

    matches = search_charts(
        constant_min=low,
        constant_max=high,
        chart_type=chart_type,
        level_index=level_index,
    )
    if not matches:
        await constant_cmd.finish("喵？这个条件下没有找到谱面")
        return

    range_text = f"{low:g}" if low == high else f"{low:g}～{high:g}"
    lines = [f"定数 {range_text}：共 {len(matches)} 张谱面"]
    lines.extend(_format_chart(match) for match in matches[:20])
    if len(matches) > 20:
        lines.append(f"……还有 {len(matches) - 20} 张未显示")
    await constant_cmd.finish("\n".join(lines))


@random_cmd.handle()
async def handle_random_song(args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    try:
        chart_type, level_index, display_level, constant, genre_query = (
            _parse_random_filters(raw)
        )
    except ValueError:
        await random_cmd.finish(
            "使用方法：\n"
            "/随歌\n"
            "/随歌 紫 14+\n"
            "/随歌 DX 13.7\n"
            "/随歌 紫 14+ POPS"
        )
        return

    matches = search_charts(
        constant_min=constant,
        constant_max=constant,
        chart_type=chart_type,
        level_index=level_index,
        display_level=display_level,
        genre_query=genre_query,
    )
    if not matches:
        await random_cmd.finish("喵？没有符合这些条件的谱面")
        return

    match = secrets.choice(matches)
    song = match.song
    chart = match.chart
    difficulty = LEVEL_NAMES.get(chart.level, f"难度{chart.level}")
    details = [
        "今日随机谱面：",
        f"#{song.id}  {song.title}",
    ]
    if song.artist:
        details.append(f"艺术家：{song.artist}")
    details.extend(
        [
            f"[{_chart_type_label(chart.type)} {difficulty}] "
            f"Lv.{chart_level_text(chart)} / 定数 {chart.difficulty:g}",
            f"分类：{song.genre}",
        ]
    )
    if chart.desn:
        details.append(f"谱师：{chart.desn}")

    image_url = f"https://assets2.lxns.net/maimai/jacket/{song.id}.png"
    await random_cmd.finish(
        MessageSegment.image(image_url) + "\n".join(details)
    )
