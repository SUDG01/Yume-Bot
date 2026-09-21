"""Best 50 poster renderer with local covers and optional LXNet player assets."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps

from src.tools.data_cache import song_cache


# ── Canvas and assets ────────────────────────────────────────────────
CANVAS_W, CANVAS_H = 1440, 1920
MARGIN_X = 38
HEADER_Y, HEADER_H = 28, 218
SECTION_LABEL_H = 48
SECTION_GAP = 28
CARD_W, CARD_H = 263, 132
CARD_GAP_X, CARD_GAP_Y = 12, 12
COLS = 5
SD_ROWS, DX_ROWS = 7, 3

STATIC_DIR = Path(__file__).parent.parent / "static"
FONT_PATH = STATIC_DIR / "ttf" / "msyb.ttf"
COVER_DIR = STATIC_DIR / "cover"
B50_ASSET_DIR = STATIC_DIR / "b50"
BACKGROUND_PATH = B50_ASSET_DIR / "background.png"
FALLBACK_COVER = B50_ASSET_DIR / "fallback_cover.png"

LEVEL_LABELS = ["BSC", "ADV", "EXP", "MAS", "Re:M"]
LEVEL_COLORS = [
    (72, 188, 111),
    (239, 190, 55),
    (241, 85, 92),
    (164, 77, 207),
    (199, 151, 226),
]
RATE_LABELS = {
    "sssp": "SSS+",
    "sss": "SSS",
    "ssp": "SS+",
    "ss": "SS",
    "sp": "S+",
    "s": "S",
    "aaa": "AAA",
    "aa": "AA",
    "a": "A",
    "bbb": "BBB",
    "bb": "BB",
    "b": "B",
    "c": "C",
    "d": "D",
}
RATE_COLORS = {
    "sssp": (247, 150, 72),
    "sss": (236, 178, 55),
    "ssp": (232, 143, 57),
    "ss": (218, 126, 66),
    "sp": (107, 175, 224),
    "s": (83, 157, 215),
}
FC_LABELS = {"app": "AP+", "ap": "AP", "fcp": "FC+", "fc": "FC"}
FS_LABELS = {
    "fsdp": "FDX+",
    "fsd": "FDX",
    "fsp": "FS+",
    "fs": "FS",
    "sync": "SYNC",
}


def _load_fonts() -> dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    sizes = (14, 16, 18, 20, 22, 24, 28, 32, 46, 64)
    fonts: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
    for size in sizes:
        try:
            fonts[size] = ImageFont.truetype(str(FONT_PATH), size)
        except OSError:
            fonts[size] = ImageFont.load_default(size=size)
    return fonts


def _procedural_background() -> Image.Image:
    """Fallback used when the generated project asset is unavailable."""
    top = (142, 219, 247)
    bottom = (255, 205, 222)
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H))
    draw = ImageDraw.Draw(img)
    for y in range(CANVAS_H):
        ratio = y / max(CANVAS_H - 1, 1)
        color = tuple(int(a + (b - a) * ratio) for a, b in zip(top, bottom))
        draw.line((0, y, CANVAS_W, y), fill=(*color, 255))
    return img


def _load_background() -> Image.Image:
    try:
        with Image.open(BACKGROUND_PATH) as source:
            return ImageOps.fit(
                source.convert("RGBA"),
                (CANVAS_W, CANVAS_H),
                method=Image.Resampling.LANCZOS,
            )
    except (OSError, ValueError):
        return _procedural_background()


def _load_cover(song_id: int | None) -> Image.Image:
    candidates: list[Path] = []
    if song_id is not None:
        candidates.append(COVER_DIR / f"{song_id}.png")
    candidates.append(FALLBACK_COVER)

    for path in candidates:
        try:
            with Image.open(path) as cover:
                return cover.convert("RGBA")
        except (OSError, ValueError):
            continue

    return Image.new("RGBA", (400, 400), (190, 210, 240, 255))


def _decode_asset(data: bytes | None) -> Image.Image | None:
    if not data:
        return None
    try:
        with Image.open(BytesIO(data)) as source:
            return source.convert("RGBA")
    except (OSError, ValueError):
        return None


def _rounded_thumbnail(source: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    thumb = ImageOps.fit(source.convert("RGBA"), size, method=Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    thumb.putalpha(mask)
    return thumb


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
    text_fill: tuple[int, int, int, int] = (255, 255, 255, 255),
    *,
    height: int = 24,
    pad_x: int = 9,
) -> int:
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    width = text_w + pad_x * 2
    draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2, fill=fill)
    text_y = y + (height - text_h) // 2 - bbox[1]
    draw.text((x + pad_x, text_y), text, font=font, fill=text_fill)
    return width


def _add_shadow(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    radius: int,
    *,
    offset: tuple[int, int] = (0, 5),
    opacity: int = 42,
) -> None:
    x0, y0, x1, y1 = box
    ox, oy = offset
    width = x1 - x0
    height = y1 - y0
    layer = Image.new(
        "RGBA",
        (width + max(ox, 0), height + max(oy, 0)),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(
        (max(ox, 0), max(oy, 0), width + ox - 1, height + oy - 1),
        radius=radius,
        fill=(37, 49, 91, opacity),
    )
    canvas.alpha_composite(layer, (x0 + min(ox, 0), y0 + min(oy, 0)))


def _collection_name(player: Mapping[str, object], key: str) -> str:
    value = player.get(key)
    if isinstance(value, Mapping):
        name = value.get("name")
        if isinstance(name, str):
            return name
    return ""


def _draw_header(
    canvas: Image.Image,
    player_name: str,
    player: Mapping[str, object],
    player_assets: Mapping[str, bytes],
    standard: Sequence[Mapping[str, object]],
    dx: Sequence[Mapping[str, object]],
    sd_rating: int,
    dx_rating: int,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    panel_w = CANVAS_W - MARGIN_X * 2
    _add_shadow(
        canvas,
        (MARGIN_X, HEADER_Y, MARGIN_X + panel_w, HEADER_Y + HEADER_H),
        28,
        offset=(0, 7),
        opacity=50,
    )

    panel = Image.new("RGBA", (panel_w, HEADER_H), (255, 255, 255, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle(
        (0, 0, panel_w - 1, HEADER_H - 1),
        radius=28,
        fill=(255, 255, 255, 222),
        outline=(255, 255, 255, 245),
        width=2,
    )

    frame = _decode_asset(player_assets.get("frame"))
    if frame is not None:
        fitted = ImageOps.fit(frame, (panel_w, HEADER_H), method=Image.Resampling.LANCZOS)
        fitted.putalpha(fitted.getchannel("A").point(lambda alpha: min(82, alpha)))
        panel.alpha_composite(fitted)

    icon = _decode_asset(player_assets.get("icon"))
    if icon is None:
        icon = _load_cover(None)
    avatar = _rounded_thumbnail(icon, (170, 170), 24)
    panel_draw.rounded_rectangle((20, 20, 198, 198), radius=28, fill=(255, 255, 255, 250))
    panel.alpha_composite(avatar, (24, 24))
    panel_draw.rounded_rectangle(
        (24, 24, 193, 193),
        radius=24,
        outline=(105, 191, 229, 255),
        width=4,
    )

    plate = _decode_asset(player_assets.get("name_plate"))
    if plate is not None:
        plate_img = ImageOps.fit(plate, (650, 102), method=Image.Resampling.LANCZOS)
        plate_img.putalpha(plate_img.getchannel("A").point(lambda alpha: min(100, alpha)))
        panel.alpha_composite(plate_img, (214, 35))
        panel_draw.rounded_rectangle((214, 35, 864, 137), radius=18, fill=(255, 255, 255, 112))

    panel_draw.text((222, 16), "YumeBot  ·  BEST 50", font=fonts[20], fill=(89, 105, 150, 255))
    fitted_name = _fit_text(panel_draw, player_name, fonts[46], 625)
    panel_draw.text(
        (220, 47),
        fitted_name,
        font=fonts[46],
        fill=(35, 43, 72, 255),
        stroke_width=1,
        stroke_fill=(255, 255, 255, 190),
    )
    trophy = _collection_name(player, "trophy")
    if trophy:
        trophy = _fit_text(panel_draw, trophy, fonts[18], 620)
        panel_draw.text((222, 108), trophy, font=fonts[18], fill=(98, 94, 126, 255))

    total = sd_rating + dx_rating
    player_rating = player.get("rating", total)
    try:
        player_rating = int(player_rating)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        player_rating = total

    rating_x = 938
    panel_draw.text((rating_x, 24), "DX RATING", font=fonts[20], fill=(94, 104, 142, 255))
    rating_text = str(player_rating)
    rating_bbox = panel_draw.textbbox((0, 0), rating_text, font=fonts[64])
    rating_w = rating_bbox[2] - rating_bbox[0]
    panel_draw.text((rating_x, 51), rating_text, font=fonts[64], fill=(48, 63, 101, 255))
    panel_draw.text(
        (rating_x + rating_w + 14, 83),
        f"{sd_rating} + {dx_rating}",
        font=fonts[20],
        fill=(101, 113, 149, 255),
    )

    all_entries = [*standard, *dx]
    sssp_count = sum(1 for entry in all_entries if entry.get("rate") == "sssp")
    ap_count = sum(1 for entry in all_entries if entry.get("fc") in {"ap", "app"})
    chip_y = 158
    chip_x = 220
    chips = (
        (f"BEST 35  {sd_rating}", (94, 159, 220, 235)),
        (f"BEST 15  {dx_rating}", (148, 102, 205, 235)),
        (f"SSS+  {sssp_count}", (241, 146, 77, 235)),
        (f"AP  {ap_count}", (65, 174, 137, 235)),
    )
    for label, color in chips:
        chip_x += (
            _draw_pill(
                panel_draw,
                (chip_x, chip_y),
                label,
                fonts[18],
                color,
                height=32,
                pad_x=13,
            )
            + 10
        )

    canvas.alpha_composite(panel, (MARGIN_X, HEADER_Y))


def _chart_constant(entry: Mapping[str, object]) -> float | None:
    song_id = entry.get("id")
    level_index = entry.get("level_index")
    chart_type = entry.get("type")
    if not isinstance(song_id, int) or not isinstance(level_index, int):
        return None

    song = song_cache.get(song_id)
    if song is None:
        return None

    for chart in song.charts:
        if chart.level == level_index and chart.type == chart_type:
            return chart.difficulty
    for chart in song.charts:
        if chart.level == level_index:
            return chart.difficulty
    return None


def _draw_card(
    canvas: Image.Image,
    entry: Mapping[str, object],
    rank: int,
    x: int,
    y: int,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    try:
        level_index = int(entry.get("level_index", 0))
    except (TypeError, ValueError):
        level_index = 0
    safe_level = max(0, min(level_index, len(LEVEL_COLORS) - 1))
    level_color = LEVEL_COLORS[safe_level]

    _add_shadow(
        canvas,
        (x, y, x + CARD_W, y + CARD_H),
        16,
        offset=(0, 4),
        opacity=45,
    )
    card = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        (0, 0, CARD_W - 1, CARD_H - 1),
        radius=16,
        fill=(255, 255, 255, 238),
        outline=(*level_color, 245),
        width=3,
    )
    draw.rounded_rectangle((1, 1, CARD_W - 2, 12), radius=10, fill=(*level_color, 245))

    song_id = entry.get("id")
    cover = _rounded_thumbnail(
        _load_cover(song_id if isinstance(song_id, int) else None),
        (90, 90),
        12,
    )
    card.alpha_composite(cover, (9, 17))

    _draw_pill(
        draw,
        (13, 21),
        f"#{rank:02d}",
        fonts[14],
        (34, 43, 68, 210),
        height=21,
        pad_x=6,
    )
    chart_type = str(entry.get("type", "")).lower()
    type_label = "DX" if chart_type == "dx" else "STD"
    type_fill = (236, 132, 62, 225) if chart_type == "dx" else (62, 158, 209, 225)
    _draw_pill(
        draw,
        (55 if type_label == "DX" else 43, 82),
        type_label,
        fonts[14],
        type_fill,
        height=21,
        pad_x=6,
    )

    content_x = 108
    content_w = CARD_W - content_x - 9
    title = entry.get("song_name")
    if not isinstance(title, str) or not title.strip():
        song = song_cache.get(song_id) if isinstance(song_id, int) else None
        title = song.title if song else "Unknown"
    title = _fit_text(draw, title, fonts[16], content_w)
    draw.text((content_x, 15), title, font=fonts[16], fill=(48, 54, 76, 255))

    try:
        achievements = float(entry.get("achievements", 0.0))
    except (TypeError, ValueError):
        achievements = 0.0
    achievement_text = f"{achievements:.4f}%"
    achievement_font = fonts[28] if len(achievement_text) <= 9 else fonts[24]
    draw.text(
        (content_x, 38),
        achievement_text,
        font=achievement_font,
        fill=(43, 51, 82, 255),
    )

    try:
        rating = math.floor(float(entry.get("dx_rating", 0)))
    except (TypeError, ValueError):
        rating = 0
    constant = _chart_constant(entry)
    level = str(entry.get("level", "?"))
    constant_text = (
        f"{constant:.1f}  →  {rating}"
        if constant is not None
        else f"Lv {level}  →  {rating}"
    )
    draw.text((content_x, 72), constant_text, font=fonts[16], fill=(*level_color, 255))

    dx_score = entry.get("dx_score")
    if isinstance(dx_score, (int, float)) and dx_score > 0:
        draw.text(
            (content_x, 92),
            f"DX {int(dx_score)}",
            font=fonts[14],
            fill=(118, 126, 151, 255),
        )

    draw.line((9, 108, CARD_W - 9, 108), fill=(184, 192, 214, 135), width=1)
    pill_x = 10
    rate_key = str(entry.get("rate") or "").lower()
    rate_label = RATE_LABELS.get(rate_key, rate_key.upper() or "—")
    rate_color = RATE_COLORS.get(rate_key, (104, 126, 168))
    pill_x += (
        _draw_pill(
            draw,
            (pill_x, 110),
            rate_label,
            fonts[14],
            (*rate_color, 238),
            height=19,
            pad_x=6,
        )
        + 5
    )

    fc_key = str(entry.get("fc") or "").lower()
    fc_label = FC_LABELS.get(fc_key)
    if fc_label:
        pill_x += (
            _draw_pill(
                draw,
                (pill_x, 110),
                fc_label,
                fonts[14],
                (48, 166, 119, 235),
                height=19,
                pad_x=6,
            )
            + 5
        )

    fs_key = str(entry.get("fs") or "").lower()
    fs_label = FS_LABELS.get(fs_key)
    if fs_label and pill_x < CARD_W - 45:
        _draw_pill(
            draw,
            (pill_x, 110),
            fs_label,
            fonts[14],
            (64, 157, 207, 235),
            height=19,
            pad_x=6,
        )

    level_label = LEVEL_LABELS[safe_level]
    label_bbox = draw.textbbox((0, 0), level_label, font=fonts[14])
    label_w = label_bbox[2] - label_bbox[0]
    draw.text(
        (CARD_W - label_w - 10, 91),
        level_label,
        font=fonts[14],
        fill=(*level_color, 230),
    )

    canvas.alpha_composite(card, (x, y))


def _draw_empty_card(
    canvas: Image.Image,
    rank: int,
    x: int,
    y: int,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    card = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        (1, 1, CARD_W - 2, CARD_H - 2),
        radius=16,
        fill=(255, 255, 255, 108),
        outline=(255, 255, 255, 185),
        width=2,
    )
    draw.text((14, 13), f"#{rank:02d}", font=fonts[16], fill=(106, 117, 151, 160))
    message = "NO RECORD"
    bbox = draw.textbbox((0, 0), message, font=fonts[20])
    draw.text(
        ((CARD_W - (bbox[2] - bbox[0])) // 2, 56),
        message,
        font=fonts[20],
        fill=(104, 118, 156, 145),
    )
    canvas.alpha_composite(card, (x, y))


def _draw_section_header(
    canvas: Image.Image,
    y: int,
    title: str,
    subtitle: str,
    accent: tuple[int, int, int],
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    width = CANVAS_W - MARGIN_X * 2
    bar = Image.new("RGBA", (width, SECTION_LABEL_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(bar)
    draw.rounded_rectangle(
        (0, 0, width - 1, SECTION_LABEL_H - 1),
        radius=18,
        fill=(255, 255, 255, 208),
        outline=(255, 255, 255, 238),
        width=2,
    )
    label_w = _draw_pill(draw, (10, 7), title, fonts[20], (*accent, 238), height=34, pad_x=16)
    draw.text((label_w + 30, 12), subtitle, font=fonts[18], fill=(74, 84, 119, 255))
    canvas.alpha_composite(bar, (MARGIN_X, y))


def _draw_section(
    canvas: Image.Image,
    entries: Sequence[Mapping[str, object]],
    rows: int,
    start_y: int,
    fonts: Mapping[int, ImageFont.FreeTypeFont | ImageFont.ImageFont],
) -> None:
    block_w = COLS * CARD_W + (COLS - 1) * CARD_GAP_X
    start_x = (CANVAS_W - block_w) // 2
    total_slots = COLS * rows
    for index in range(total_slots):
        row, col = divmod(index, COLS)
        x = start_x + col * (CARD_W + CARD_GAP_X)
        y = start_y + row * (CARD_H + CARD_GAP_Y)
        if index < len(entries):
            _draw_card(canvas, entries[index], index + 1, x, y, fonts)
        else:
            _draw_empty_card(canvas, index + 1, x, y, fonts)


def _render_b50_image(
    standard: Sequence[Mapping[str, object]],
    dx: Sequence[Mapping[str, object]],
    player_name: str,
    sd_rating: int,
    dx_rating: int,
    player: Mapping[str, object] | None,
    player_assets: Mapping[str, bytes] | None,
) -> Image.Image:
    standard = list(standard[: COLS * SD_ROWS])
    dx = list(dx[: COLS * DX_ROWS])
    player = player or {}
    player_assets = player_assets or {}
    fonts = _load_fonts()

    canvas = _load_background()
    canvas.alpha_composite(Image.new("RGBA", canvas.size, (255, 255, 255, 18)))

    _draw_header(
        canvas,
        player_name,
        player,
        player_assets,
        standard,
        dx,
        sd_rating,
        dx_rating,
        fonts,
    )

    sd_label_y = HEADER_Y + HEADER_H + 24
    sd_avg = sd_rating / len(standard) if standard else 0
    _draw_section_header(
        canvas,
        sd_label_y,
        "BEST 35 · 往期版本",
        f"{len(standard)}/35 records   ·   average rating {sd_avg:.1f}",
        (84, 156, 217),
        fonts,
    )
    sd_cards_y = sd_label_y + SECTION_LABEL_H + 12
    _draw_section(canvas, standard, SD_ROWS, sd_cards_y, fonts)

    sd_height = SD_ROWS * CARD_H + (SD_ROWS - 1) * CARD_GAP_Y
    dx_label_y = sd_cards_y + sd_height + SECTION_GAP
    dx_avg = dx_rating / len(dx) if dx else 0
    _draw_section_header(
        canvas,
        dx_label_y,
        "BEST 15 · 当前版本",
        f"{len(dx)}/15 records   ·   average rating {dx_avg:.1f}",
        (154, 99, 202),
        fonts,
    )
    dx_cards_y = dx_label_y + SECTION_LABEL_H + 12
    _draw_section(canvas, dx, DX_ROWS, dx_cards_y, fonts)

    footer = f"Data from LXNet  ·  Generated by YumeBot  ·  {datetime.now():%Y-%m-%d %H:%M}"
    footer_draw = ImageDraw.Draw(canvas)
    bbox = footer_draw.textbbox((0, 0), footer, font=fonts[16])
    footer_w = bbox[2] - bbox[0]
    footer_draw.text(
        ((CANVAS_W - footer_w) // 2, CANVAS_H - 45),
        footer,
        font=fonts[16],
        fill=(73, 88, 128, 220),
        stroke_width=1,
        stroke_fill=(255, 255, 255, 180),
    )
    return canvas.convert("RGB")


async def generate_b50_image(
    standard: Sequence[Mapping[str, object]],
    dx: Sequence[Mapping[str, object]],
    player_name: str,
    sd_rating: int,
    dx_rating: int,
    *,
    player: Mapping[str, object] | None = None,
    player_assets: Mapping[str, bytes] | None = None,
) -> Image.Image:
    """Render the poster in a worker thread so Pillow does not block the bot loop."""
    return await asyncio.to_thread(
        _render_b50_image,
        standard,
        dx,
        player_name,
        sd_rating,
        dx_rating,
        player,
        player_assets,
    )
