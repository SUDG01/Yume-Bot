from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import to_me

from src.tools.mai_music import Song
from src.tools.song_search import chart_level_text, resolve_song


LEVEL_NAMES = {0: "绿", 1: "黄", 2: "红", 3: "紫", 4: "白"}

get_song_info = on_command(
    "info",
    aliases={"maiinfo"},
    rule=to_me(),
    priority=5,
    block=True,
)


def _key_chart_summary(song: Song) -> str:
    parts: list[str] = []
    for chart_type, label in (("dx", "DX"), ("standard", "STD")):
        charts = [
            chart
            for chart in song.charts
            if chart.type == chart_type and chart.level >= 3
        ]
        if charts:
            values = "/".join(
                f"{LEVEL_NAMES.get(chart.level, '?')}{chart.difficulty:g}"
                for chart in charts
            )
            parts.append(f"{label} {values}")
    return " · ".join(parts) or "暂无常规谱面"


def _song_detail(song: Song) -> str:
    metadata = [f"曲名：{song.title}"]
    if song.artist:
        metadata.append(f"艺术家：{song.artist}")
    metadata.append(f"分类：{song.genre}")
    if song.bpm is not None:
        metadata.append(f"BPM：{song.bpm}")
    if song.disabled:
        metadata.append("状态：当前版本不可用")

    chart_lines: list[str] = []
    for chart in sorted(song.charts, key=lambda item: (item.type, item.level)):
        difficulty_name = LEVEL_NAMES.get(chart.level, f"难度{chart.level}")
        chart_type = "DX" if chart.type == "dx" else "STD"
        line = (
            f"{difficulty_name}谱 Lv.{chart_level_text(chart)} / "
            f"定数 {chart.difficulty:g} [{chart_type}]"
        )
        if chart.level >= 3:
            line += (
                f" / SSS {chart.calculate_score_sss}Ra"
                f" / SSS+ {chart.calculate_score_sssp}Ra"
            )
        if chart.desn:
            line += f" / 谱师 {chart.desn}"
        chart_lines.append(line)

    return "\n".join(
        [
            *metadata,
            "",
            *chart_lines,
            "",
            "—— Info From LXNet ——",
        ]
    )


@get_song_info.handle()
async def song_info(arg: Message = CommandArg()):
    user_input = arg.extract_plain_text().strip()
    if not user_input:
        await get_song_info.finish(
            "使用方法：/info <歌曲 ID、曲名或别名>\n例如：/info 834"
        )
        return

    song, matches = resolve_song(user_input)
    if song is None:
        if not matches:
            await get_song_info.finish(
                "喵？小Yume找不到这首歌... 可以试试 /查歌 <关键词>"
            )
            return
        lines = ["找到多个可能结果，请使用 /info <ID> 精确查询："]
        for match in matches[:8]:
            lines.append(
                f"#{match.song.id}  {match.song.title}\n"
                f"  {_key_chart_summary(match.song)}"
            )
        await get_song_info.finish("\n".join(lines))
        return

    image_url = f"https://assets2.lxns.net/maimai/jacket/{song.id}.png"
    await get_song_info.finish(
        MessageSegment.image(image_url) + _song_detail(song)
    )
