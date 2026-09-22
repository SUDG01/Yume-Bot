"""Render LXNet Rating trend, activity, and recent-score data."""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageDraw, ImageFont, ImageOps

from src.tools.data_cache import song_cache
from src.tools.trend_data import RecentScore, TrendSummary


CANVAS_W, CANVAS_H = 1200, 1500
MARGIN = 40
STATIC_DIR = Path(__file__).parent.parent / "static"
FONT_PATH = STATIC_DIR / "ttf" / "msyb.ttf"
COVER_DIR = STATIC_DIR / "cover"
BACKGROUND_PATH = STATIC_DIR / "b50" / "background.png"
FALLBACK_COVER = STATIC_DIR / "b50" / "fallback_cover.png"

LEVEL_COLORS = [
    (72, 188, 111),
    (239, 190, 55),
    (241, 85, 92),
    (164, 77, 207),
    (199, 151, 226),
]


def _load_fonts() -> dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    sizes = (13, 14, 16, 18, 20, 22, 24, 28, 30, 32, 38, 48, 64)
    fonts: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
    for size in sizes:
        try:
            fonts[size] = ImageFont.truetype(str(FONT_PATH), size)
        except OSError:
            fonts[size] = ImageFont.load_default(size=size)
    return fonts


def _load_background() -> Image.Image:
    try:
        with Image.open(BACKGROUND_PATH) as source:
            return ImageOps.fit(
                source.convert("RGBA"),
                (CANVAS_W, CANVAS_H),
                method=Image.Resampling.LANCZOS,
            )
    except (OSError, ValueError):
        return Image.new("RGBA", (CANVAS_W, CANVAS_H), (201, 226, 246, 255))


def _load_cover(song_id: object) -> Image.Image:
    candidates: list[Path] = []
    if isinstance(song_id, int):
        candidates.append(COVER_DIR / f"{song_id}.png")
    candidates.append(FALLBACK_COVER)
    for path in candidates:
        try:
            with Image.open(path) as source:
                return source.convert("RGBA")
        except (OSError, ValueError):
            continue
    return Image.new("RGBA", (400, 400), (190, 211, 239, 255))


def _rounded_thumbnail(source: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    image = ImageOps.fit(source.convert("RGBA"), size, method=Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1),
        radius=radius,
        fill=255,
    )
    image.putalpha(mask)
    return image


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> str:
    text = str(text).strip() or "Unknown"
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    while len(text) > 1:
        text = text[:-1]
        candidate = text.rstrip() + "…"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            return candidate
    return "…"


def _panel(size: tuple[int, int], radius: int = 24) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1),
        radius=radius,
        fill=(255, 255, 255, 224),
        outline=(255, 255, 255, 246),
        width=2,
    )
    return image


def _draw_pill(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    *,
    height: int = 30,
    pad_x: int = 11,
) -> int:
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    width = text_width + pad_x * 2
    draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2, fill=fill)
    draw.text(
        (x + pad_x, y + (height - text_height) // 2 - bbox[1]),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return width


def _draw_header(
    canvas: Image.Image,
    summary: TrendSummary,
    player_name: str,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    header = _panel((CANVAS_W - MARGIN * 2, 210), 28)
    draw = ImageDraw.Draw(header)
    logo = _rounded_thumbnail(_load_cover(None), (156, 156), 24)
    header.alpha_composite(logo, (22, 27))
    draw.rounded_rectangle(
        (22, 27, 177, 182),
        radius=24,
        outline=(104, 190, 229, 255),
        width=4,
    )

    draw.text(
        (204, 22),
        f"YumeBot  ·  {summary.days} DAYS",
        font=fonts[20],
        fill=(87, 103, 145, 255),
    )
    player_name = _fit_text(draw, player_name, fonts[38], 490)
    draw.text((202, 56), player_name, font=fonts[38], fill=(39, 48, 78, 255))
    draw.text((204, 112), "成绩趋势", font=fonts[30], fill=(91, 103, 142, 255))

    gain = summary.rating_gain
    gain_text = f"{gain:+d} Ra"
    gain_color = (58, 174, 132, 238) if gain >= 0 else (231, 100, 108, 238)
    _draw_pill(draw, (204, 159), gain_text, fonts[18], gain_color, height=32)

    draw.text((790, 28), "CURRENT RATING", font=fonts[18], fill=(99, 109, 143, 255))
    draw.text(
        (788, 58),
        str(summary.current_total),
        font=fonts[64],
        fill=(47, 61, 99, 255),
    )
    draw.text(
        (792, 142),
        f"区间峰值 {summary.peak_total}",
        font=fonts[18],
        fill=(104, 113, 146, 255),
    )
    canvas.alpha_composite(header, (MARGIN, 30))


def _draw_stats(
    canvas: Image.Image,
    summary: TrendSummary,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    cards = (
        ("当前 Rating", str(summary.current_total), (78, 146, 214)),
        ("期间变化", f"{summary.rating_gain:+d}", (63, 173, 133)),
        (
            "B35 / B15",
            f"{summary.current_standard} / {summary.current_dx}",
            (147, 97, 201),
        ),
        (
            "活跃 / 更新",
            f"{summary.active_days} 天 / {summary.update_count} 条",
            (236, 145, 79),
        ),
    )
    card_width = 265
    for index, (label, value, accent) in enumerate(cards):
        card = _panel((card_width, 120), 20)
        draw = ImageDraw.Draw(card)
        draw.rounded_rectangle((0, 0, card_width - 1, 8), radius=8, fill=(*accent, 245))
        draw.text((18, 22), label, font=fonts[18], fill=(103, 112, 145, 255))
        value = _fit_text(draw, value, fonts[32], card_width - 36)
        draw.text((18, 56), value, font=fonts[32], fill=(47, 57, 91, 255))
        x = MARGIN + index * (card_width + 20)
        canvas.alpha_composite(card, (x, 265))


def _draw_line_chart(
    canvas: Image.Image,
    summary: TrendSummary,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    panel_width, panel_height = CANVAS_W - MARGIN * 2, 385
    chart = _panel((panel_width, panel_height), 24)
    draw = ImageDraw.Draw(chart)
    draw.text((24, 18), "DX Rating 变化", font=fonts[24], fill=(49, 59, 93, 255))

    left, top, right, bottom = 78, 72, panel_width - 30, panel_height - 48
    totals = [point.total for point in summary.points]
    minimum = min(totals, default=0)
    maximum = max(totals, default=1)
    spread = maximum - minimum
    padding = max(8, math.ceil(spread * 0.15))
    lower = max(0, minimum - padding)
    upper = maximum + padding
    if upper == lower:
        upper = lower + 10

    for index in range(5):
        y = top + (bottom - top) * index / 4
        value = round(upper - (upper - lower) * index / 4)
        draw.line((left, y, right, y), fill=(136, 151, 190, 70), width=1)
        draw.text((12, y - 9), str(value), font=fonts[14], fill=(103, 113, 148, 230))

    duration = max((summary.end_day - summary.start_day).days, 1)

    def point_xy(point) -> tuple[float, float]:
        elapsed = (point.day - summary.start_day).days
        x = left + (right - left) * elapsed / duration
        y = bottom - (bottom - top) * (point.total - lower) / (upper - lower)
        return x, y

    coordinates = [point_xy(point) for point in summary.points]
    if coordinates:
        area = [
            (coordinates[0][0], bottom),
            *coordinates,
            (coordinates[-1][0], bottom),
        ]
        draw.polygon(area, fill=(102, 145, 224, 45))
        if len(coordinates) == 1:
            draw.line(
                (left, coordinates[0][1], right, coordinates[0][1]),
                fill=(72, 120, 211, 255),
                width=4,
            )
        else:
            draw.line(coordinates, fill=(72, 120, 211, 255), width=5, joint="curve")
        if len(coordinates) <= 40:
            for x, y in coordinates:
                draw.ellipse(
                    (x - 5, y - 5, x + 5, y + 5),
                    fill=(255, 255, 255, 255),
                    outline=(72, 120, 211, 255),
                    width=3,
                )

    middle_day = summary.start_day + timedelta(days=duration // 2)
    date_labels = (
        (left, summary.start_day, "la"),
        ((left + right) / 2, middle_day, "mm"),
        (right, summary.end_day, "ra"),
    )
    for x, day, alignment in date_labels:
        label = day.strftime("%m-%d")
        bbox = draw.textbbox((0, 0), label, font=fonts[14])
        width = bbox[2] - bbox[0]
        if alignment == "mm":
            x -= width / 2
        elif alignment == "ra":
            x -= width
        draw.text((x, bottom + 14), label, font=fonts[14], fill=(104, 113, 147, 230))

    canvas.alpha_composite(chart, (MARGIN, 410))


def _draw_heatmap(
    canvas: Image.Image,
    summary: TrendSummary,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    panel_width, panel_height = CANVAS_W - MARGIN * 2, 205
    heatmap = _panel((panel_width, panel_height), 24)
    draw = ImageDraw.Draw(heatmap)
    draw.text((24, 17), "成绩同步活跃度", font=fonts[24], fill=(49, 59, 93, 255))
    draw.text(
        (250, 22),
        f"{summary.active_days} 个活跃日  ·  {summary.update_count} 条成绩更新",
        font=fonts[16],
        fill=(103, 113, 147, 255),
    )

    grid_start = summary.start_day - timedelta(days=summary.start_day.weekday())
    week_count = ((summary.end_day - grid_start).days // 7) + 1
    cell_size, gap = 18, 4
    grid_width = week_count * (cell_size + gap) - gap
    grid_x = (panel_width - grid_width) // 2
    grid_y = 50
    maximum = max(summary.activity.values(), default=0)

    for offset in range((summary.end_day - grid_start).days + 1):
        day = grid_start + timedelta(days=offset)
        week = offset // 7
        weekday = offset % 7
        x = grid_x + week * (cell_size + gap)
        y = grid_y + weekday * (cell_size + gap)
        if not summary.start_day <= day <= summary.end_day:
            color = (221, 227, 239, 110)
        else:
            count = summary.activity.get(day, 0)
            if count <= 0 or maximum <= 0:
                color = (213, 222, 239, 180)
            else:
                ratio = math.log1p(count) / math.log1p(maximum)
                color = (
                    int(154 - 77 * ratio),
                    int(211 - 45 * ratio),
                    int(226 - 45 * ratio),
                    240,
                )
        draw.rounded_rectangle(
            (x, y, x + cell_size, y + cell_size),
            radius=4,
            fill=color,
        )

    draw.text((24, 166), "浅", font=fonts[14], fill=(108, 117, 149, 230))
    for index, color in enumerate(
        ((213, 222, 239), (128, 199, 216), (91, 181, 199), (67, 160, 179))
    ):
        x = 52 + index * 25
        draw.rounded_rectangle((x, 165, x + 18, 183), radius=4, fill=(*color, 240))
    draw.text((154, 166), "深", font=fonts[14], fill=(108, 117, 149, 230))
    canvas.alpha_composite(heatmap, (MARGIN, 815))


def _recent_title(recent: RecentScore) -> str:
    title = recent.entry.get("song_name")
    if isinstance(title, str) and title:
        return title
    song_id = recent.entry.get("id")
    song = song_cache.get(song_id) if isinstance(song_id, int) else None
    return song.title if song else "Unknown"


def _draw_recent_card(
    parent: Image.Image,
    recent: RecentScore,
    x: int,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    width, height = 204, 245
    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    try:
        level_index = int(recent.entry.get("level_index", 0))
    except (TypeError, ValueError):
        level_index = 0
    level_index = max(0, min(level_index, len(LEVEL_COLORS) - 1))
    level_color = LEVEL_COLORS[level_index]
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=18,
        fill=(255, 255, 255, 220),
        outline=(*level_color, 235),
        width=3,
    )
    draw.rounded_rectangle((1, 1, width - 2, 9), radius=8, fill=(*level_color, 245))
    cover = _rounded_thumbnail(_load_cover(recent.entry.get("id")), (106, 106), 14)
    card.alpha_composite(cover, ((width - 106) // 2, 18))
    title = _fit_text(draw, _recent_title(recent), fonts[16], width - 20)
    draw.text((10, 132), title, font=fonts[16], fill=(48, 55, 79, 255))
    try:
        achievement = float(recent.entry.get("achievements", 0.0))
    except (TypeError, ValueError):
        achievement = 0.0
    draw.text((10, 158), f"{achievement:.4f}%", font=fonts[20], fill=(46, 56, 88, 255))
    chart_type = "DX" if recent.entry.get("type") == "dx" else "STD"
    level = str(recent.entry.get("level", "?"))
    draw.text((10, 188), f"{chart_type}  Lv.{level}", font=fonts[14], fill=(*level_color, 255))
    if recent.occurred_at is not None:
        time_text = recent.occurred_at.astimezone().strftime("%m-%d %H:%M")
        draw.text((10, 215), time_text, font=fonts[13], fill=(112, 120, 151, 255))
    parent.alpha_composite(card, (x, 58))


def _draw_recents(
    canvas: Image.Image,
    summary: TrendSummary,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    panel_width, panel_height = CANVAS_W - MARGIN * 2, 350
    panel = _panel((panel_width, panel_height), 24)
    draw = ImageDraw.Draw(panel)
    draw.text((24, 17), "最近成绩", font=fonts[24], fill=(49, 59, 93, 255))
    draw.text(
        (154, 22),
        "仅显示 LXNet Recent 中本区间最近 5 条",
        font=fonts[16],
        fill=(103, 113, 147, 255),
    )
    if summary.recents:
        for index, recent in enumerate(summary.recents):
            _draw_recent_card(panel, recent, 25 + index * 214, fonts)
    else:
        message = "本区间没有可用的 Recent 数据"
        bbox = draw.textbbox((0, 0), message, font=fonts[24])
        draw.text(
            ((panel_width - (bbox[2] - bbox[0])) // 2, 165),
            message,
            font=fonts[24],
            fill=(109, 119, 151, 210),
        )
    canvas.alpha_composite(panel, (MARGIN, 1040))


def _render_trend_image(
    summary: TrendSummary,
    player_name: str,
) -> Image.Image:
    fonts = _load_fonts()
    canvas = _load_background()
    canvas.alpha_composite(Image.new("RGBA", canvas.size, (255, 255, 255, 20)))
    _draw_header(canvas, summary, player_name, fonts)
    _draw_stats(canvas, summary, fonts)
    _draw_line_chart(canvas, summary, fonts)
    _draw_heatmap(canvas, summary, fonts)
    _draw_recents(canvas, summary, fonts)

    footer = (
        "趋势与上传活跃度来自 LXNet  ·  Generated by YumeBot  ·  "
        f"{datetime.now():%Y-%m-%d %H:%M}"
    )
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), footer, font=fonts[16])
    footer_width = bbox[2] - bbox[0]
    draw.text(
        ((CANVAS_W - footer_width) // 2, CANVAS_H - 45),
        footer,
        font=fonts[16],
        fill=(74, 87, 127, 220),
        stroke_width=1,
        stroke_fill=(255, 255, 255, 180),
    )
    return canvas.convert("RGB")


async def generate_trend_image(
    summary: TrendSummary,
    player_name: str,
) -> Image.Image:
    """Render the trend poster outside the bot event loop."""
    return await asyncio.to_thread(_render_trend_image, summary, player_name)
