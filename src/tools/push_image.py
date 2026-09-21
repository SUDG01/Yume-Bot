"""Render a compact, actionable B50 improvement plan."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageDraw, ImageFont, ImageOps

from src.tools.push_recommend import PushPlan, PushRecommendation


CANVAS_W, CANVAS_H = 1200, 1500
MARGIN = 40
HEADER_Y, HEADER_H = 30, 220
SUMMARY_Y, SUMMARY_H = 270, 42
CARD_W, CARD_H = 550, 195
CARD_GAP_X, CARD_GAP_Y = 20, 18
CARDS_Y = 330

STATIC_DIR = Path(__file__).parent.parent / "static"
FONT_PATH = STATIC_DIR / "ttf" / "msyb.ttf"
COVER_DIR = STATIC_DIR / "cover"
BACKGROUND_PATH = STATIC_DIR / "b50" / "background.png"
FALLBACK_COVER = STATIC_DIR / "b50" / "fallback_cover.png"

LEVEL_LABELS = ["BASIC", "ADVANCED", "EXPERT", "MASTER", "Re:MASTER"]
LEVEL_COLORS = [
    (72, 188, 111),
    (239, 190, 55),
    (241, 85, 92),
    (164, 77, 207),
    (199, 151, 226),
]


def _load_fonts() -> dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    sizes = (14, 16, 18, 20, 22, 24, 26, 30, 38, 48, 64)
    fonts: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
    for size in sizes:
        try:
            fonts[size] = ImageFont.truetype(str(FONT_PATH), size)
        except OSError:
            fonts[size] = ImageFont.load_default(size=size)
    return fonts


def _load_background(height: int) -> Image.Image:
    try:
        with Image.open(BACKGROUND_PATH) as source:
            return ImageOps.fit(
                source.convert("RGBA"),
                (CANVAS_W, height),
                method=Image.Resampling.LANCZOS,
            )
    except (OSError, ValueError):
        return Image.new("RGBA", (CANVAS_W, height), (200, 225, 246, 255))


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
    return Image.new("RGBA", (400, 400), (191, 210, 239, 255))


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
    shortened = text
    while len(shortened) > 1:
        shortened = shortened[:-1]
        candidate = shortened.rstrip() + "…"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            return candidate
    return "…"


def _draw_pill(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    *,
    height: int = 26,
    pad_x: int = 9,
    text_fill: tuple[int, int, int, int] = (255, 255, 255, 255),
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
        fill=text_fill,
    )
    return width


def _add_shadow(
    canvas: Image.Image,
    xy: tuple[int, int],
    size: tuple[int, int],
    radius: int,
) -> None:
    width, height = size
    layer = Image.new("RGBA", (width, height + 6), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        (0, 6, width - 1, height + 5),
        radius=radius,
        fill=(38, 49, 88, 45),
    )
    canvas.alpha_composite(layer, xy)


def _difficulty_style(entry: Mapping[str, object]) -> tuple[str, tuple[int, int, int]]:
    try:
        level_index = int(entry.get("level_index", 0))
    except (TypeError, ValueError):
        level_index = 0
    index = max(0, min(level_index, len(LEVEL_LABELS) - 1))
    return LEVEL_LABELS[index], LEVEL_COLORS[index]


def _draw_header(
    canvas: Image.Image,
    plan: PushPlan,
    player_name: str,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    panel_w = CANVAS_W - MARGIN * 2
    _add_shadow(canvas, (MARGIN, HEADER_Y), (panel_w, HEADER_H), 28)
    panel = Image.new("RGBA", (panel_w, HEADER_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    draw.rounded_rectangle(
        (0, 0, panel_w - 1, HEADER_H - 1),
        radius=28,
        fill=(255, 255, 255, 224),
        outline=(255, 255, 255, 246),
        width=2,
    )

    logo = _rounded_thumbnail(_load_cover(None), (164, 164), 24)
    panel.alpha_composite(logo, (22, 28))
    draw.rounded_rectangle(
        (22, 28, 185, 191),
        radius=24,
        outline=(105, 191, 229, 255),
        width=4,
    )

    draw.text((210, 20), "YumeBot  ·  RATING BOOST", font=fonts[20], fill=(86, 103, 146, 255))
    player_name = _fit_text(draw, player_name, fonts[38], 515)
    draw.text((208, 54), player_name, font=fonts[38], fill=(39, 48, 78, 255))
    draw.text((210, 105), "推分计划", font=fonts[30], fill=(92, 104, 143, 255))

    chip_x = 210
    chip_y = 157
    chips = [
        (f"推荐 {len(plan.recommendations)} 首", (87, 158, 217, 235)),
        (f"预计 +{plan.projected_gain}", (65, 174, 137, 235)),
    ]
    if plan.level_filter:
        chips.append((f"难度 {plan.level_filter}", (153, 99, 203, 235)))
    for label, color in chips:
        chip_x += _draw_pill(
            draw,
            (chip_x, chip_y),
            label,
            fonts[18],
            color,
            height=32,
            pad_x=13,
        ) + 9

    rating_x = 735
    draw.text((rating_x, 24), "CURRENT", font=fonts[18], fill=(102, 111, 145, 255))
    draw.text(
        (rating_x, 50),
        str(plan.current_total),
        font=fonts[64],
        fill=(47, 61, 99, 255),
    )
    draw.text((rating_x + 190, 75), "→", font=fonts[38], fill=(135, 111, 191, 255))
    draw.text((rating_x + 240, 24), "PROJECTED", font=fonts[18], fill=(102, 111, 145, 255))
    draw.text(
        (rating_x + 240, 58),
        str(plan.projected_total),
        font=fonts[48],
        fill=(132, 76, 190, 255),
    )

    if plan.target_rating is not None:
        remaining = max(plan.target_rating - plan.projected_total, 0)
        target_text = f"目标 {plan.target_rating}"
        if remaining:
            target_text += f"  ·  计划后还差 {remaining}"
        else:
            target_text += "  ·  本计划可达成"
        draw.text((rating_x, 150), target_text, font=fonts[18], fill=(91, 100, 135, 255))

    canvas.alpha_composite(panel, (MARGIN, HEADER_Y))


def _draw_summary(
    canvas: Image.Image,
    plan: PushPlan,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    width = CANVAS_W - MARGIN * 2
    bar = Image.new("RGBA", (width, SUMMARY_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(bar)
    draw.rounded_rectangle(
        (0, 0, width - 1, SUMMARY_H - 1),
        radius=16,
        fill=(255, 255, 255, 210),
        outline=(255, 255, 255, 240),
        width=2,
    )
    text = "按单位达成率的真实 B50 边际收益排序"
    if plan.target_rating:
        text += f"  ·  目标 {plan.target_rating}"
    draw.text((18, 8), text, font=fonts[18], fill=(75, 87, 123, 255))
    canvas.alpha_composite(bar, (MARGIN, SUMMARY_Y))


def _draw_card(
    canvas: Image.Image,
    recommendation: PushRecommendation,
    index: int,
    x: int,
    y: int,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    level_label, level_color = _difficulty_style(recommendation.entry)
    _add_shadow(canvas, (x, y), (CARD_W, CARD_H), 20)
    card = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        (0, 0, CARD_W - 1, CARD_H - 1),
        radius=20,
        fill=(255, 255, 255, 239),
        outline=(*level_color, 245),
        width=3,
    )
    draw.rounded_rectangle((1, 1, CARD_W - 2, 12), radius=10, fill=(*level_color, 245))

    cover = _rounded_thumbnail(_load_cover(recommendation.entry.get("id")), (145, 145), 16)
    card.alpha_composite(cover, (14, 25))
    _draw_pill(
        draw,
        (19, 30),
        f"#{index:02d}",
        fonts[16],
        (34, 43, 68, 215),
        height=25,
        pad_x=7,
    )
    chart_type = str(recommendation.entry.get("type", "")).lower()
    type_label = "DX" if chart_type == "dx" else "STD"
    type_fill = (236, 132, 62, 230) if chart_type == "dx" else (62, 158, 209, 230)
    _draw_pill(
        draw,
        (104 if type_label == "DX" else 89, 140),
        type_label,
        fonts[16],
        type_fill,
        height=25,
        pad_x=7,
    )

    content_x = 176
    title = str(recommendation.entry.get("song_name", "Unknown"))
    title = _fit_text(draw, title, fonts[24], 265)
    draw.text((content_x, 18), title, font=fonts[24], fill=(43, 50, 77, 255))

    gain_text = f"+{recommendation.gain} Ra"
    gain_bbox = draw.textbbox((0, 0), gain_text, font=fonts[18])
    gain_width = gain_bbox[2] - gain_bbox[0] + 22
    _draw_pill(
        draw,
        (CARD_W - gain_width - 12, 18),
        gain_text,
        fonts[18],
        (60, 174, 133, 238),
        height=30,
        pad_x=11,
    )

    badge_x = content_x
    badge_x += _draw_pill(
        draw,
        (badge_x, 55),
        level_label,
        fonts[14],
        (*level_color, 235),
        height=24,
        pad_x=8,
    ) + 6
    badge_x += _draw_pill(
        draw,
        (badge_x, 55),
        f"定数 {recommendation.constant:.1f}",
        fonts[14],
        (99, 116, 159, 225),
        height=24,
        pad_x=8,
    ) + 6
    _draw_pill(
        draw,
        (badge_x, 55),
        recommendation.category_label,
        fonts[14],
        (145, 99, 201, 225),
        height=24,
        pad_x=8,
    )

    draw.text(
        (content_x, 91),
        f"当前  {recommendation.current_achievement:.4f}%  ·  {recommendation.current_rating} Ra",
        font=fonts[18],
        fill=(105, 113, 145, 255),
    )
    draw.text(
        (content_x, 119),
        f"目标  {recommendation.target_achievement:.4f}%  ·  {recommendation.target_rating} Ra",
        font=fonts[22],
        fill=(55, 64, 98, 255),
    )

    status = "当前已在榜" if recommendation.in_b50 else "Selection 候选入榜"
    draw.text((content_x, 158), status, font=fonts[16], fill=(113, 120, 151, 255))
    effort_text = f"需提升 {recommendation.effort:.4f}%"
    effort_bbox = draw.textbbox((0, 0), effort_text, font=fonts[16])
    effort_width = effort_bbox[2] - effort_bbox[0]
    draw.text(
        (CARD_W - effort_width - 14, 158),
        effort_text,
        font=fonts[16],
        fill=(*level_color, 255),
    )

    canvas.alpha_composite(card, (x, y))


def _render_push_image(plan: PushPlan, player_name: str) -> Image.Image:
    fonts = _load_fonts()
    row_count = (len(plan.recommendations) + 1) // 2
    cards_height = row_count * CARD_H + max(row_count - 1, 0) * CARD_GAP_Y
    required_height = CARDS_Y + cards_height + 90
    canvas_height = max(700, min(CANVAS_H, ((required_height + 49) // 50) * 50))
    canvas = _load_background(canvas_height)
    canvas.alpha_composite(Image.new("RGBA", canvas.size, (255, 255, 255, 20)))
    _draw_header(canvas, plan, player_name, fonts)
    _draw_summary(canvas, plan, fonts)

    for index, recommendation in enumerate(plan.recommendations):
        row, column = divmod(index, 2)
        x = MARGIN + column * (CARD_W + CARD_GAP_X)
        y = CARDS_Y + row * (CARD_H + CARD_GAP_Y)
        _draw_card(canvas, recommendation, index + 1, x, y, fonts)

    footer = (
        "结果为目标达成率下的 B50 估算  ·  实际数据以 LXNet 更新为准"
        f"  ·  {datetime.now():%Y-%m-%d %H:%M}"
    )
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), footer, font=fonts[16])
    footer_width = bbox[2] - bbox[0]
    draw.text(
        ((CANVAS_W - footer_width) // 2, canvas_height - 42),
        footer,
        font=fonts[16],
        fill=(74, 87, 127, 220),
        stroke_width=1,
        stroke_fill=(255, 255, 255, 180),
    )
    return canvas.convert("RGB")


async def generate_push_image(plan: PushPlan, player_name: str) -> Image.Image:
    """Render the recommendation poster without blocking the bot event loop."""
    return await asyncio.to_thread(_render_push_image, plan, player_name)
