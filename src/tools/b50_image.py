"""简易 Best 50 图片生成器 — 上下排版，大卡片，仅依赖曲绘封面。"""

import math
from pathlib import Path
from typing import List

from PIL import Image, ImageDraw, ImageFont

from src.tools.data_cache import song_cache
from src.tools.mai_music import Song

# ── 布局常量 ──────────────────────────────────
CARD_W, CARD_H = 130, 96
GAP = 4
MARGIN = 10
HEADER_H = 90
SECTION_GAP = 20
SECTION_LABEL_H = 22

FONT_PATH = Path(__file__).parent.parent / "static" / "ttf" / "msyb.ttf"
COVER_DIR = Path(__file__).parent.parent / "static" / "cover"
FALLBACK_COVER = COVER_DIR / "1000.png"

LEVEL_NAMES = ["Basic", "Advanced", "Expert", "Master", "Re:Master"]
LEVEL_COLORS = [
    (100, 200, 80),    # Basic 绿
    (240, 200, 40),    # Advanced 黄
    (240, 100, 100),   # Expert 红
    (160, 80, 220),    # Master 紫
    (220, 200, 240),   # Re:Master 白
]
RATE_MAP = {
    "sssp": "SSS+", "sss": "SSS", "ssp": "SS+", "ss": "SS",
    "sp": "S+", "s": "S", "aaa": "AAA", "aa": "AA", "a": "A",
    "bbb": "BBB", "bb": "BB", "b": "B", "c": "C", "d": "D",
}
FC_MAP = {"app": "AP+", "ap": "AP", "fcp": "FC+", "fc": "FC"}
FS_MAP = {"fsdp": "FDX+", "fsd": "FDX", "fsp": "FS+", "fs": "FS", "sync": "SYNC"}

# SD: 5列×7行=35   DX: 5列×3行=15   → 同列数，上下对齐
SD_COLS, SD_ROWS = 5, 7
DX_COLS, DX_ROWS = 5, 3


def _load_cover(song_id: int) -> Image.Image:
    p = COVER_DIR / f"{song_id}.png"
    if not p.exists():
        p = FALLBACK_COVER
    return Image.open(p).convert("RGB")


def _draw_card(draw: ImageDraw.ImageDraw, entry: dict,
               font_sm: ImageFont.FreeTypeFont,
               font_md: ImageFont.FreeTypeFont,
               font_lg: ImageFont.FreeTypeFont):
    """在一张卡片上绘制所有文字和标记。"""

    # ── 难度色三角（右上角） ──
    lvl_idx = entry["level_index"]
    color = LEVEL_COLORS[min(lvl_idx, 4)]
    tri = [(CARD_W, 0), (CARD_W - 28, 0), (CARD_W, 28)]
    draw.polygon(tri, fill=color)

    # ── 曲名（左上，中字） ──
    title = entry["song_name"]
    while draw.textbbox((0, 0), title + "…", font=font_md)[2] > CARD_W - 32 and len(title) > 3:
        title = title[:-1]
    if len(title) < len(entry["song_name"]):
        title += "…"
    draw.text((8, 6), title, fill=(255, 255, 255), font=font_md)

    # ── 定数 → Ra（左上第二行，小字） ──
    song: Song | None = song_cache.get(entry["id"])
    ra = math.floor(entry["dx_rating"])
    if song and lvl_idx < len(song.charts):
        ds = song.charts[lvl_idx].difficulty
        draw.text((8, 28), f"{ds} → {ra}", fill=(190, 210, 255), font=font_sm)
    else:
        draw.text((8, 28), f"→ {ra}", fill=(190, 210, 255), font=font_sm)

    # ── 达成率（中央偏下，大字） ──
    ach = entry["achievements"]
    ach_text = f"{ach:.4f}%"
    bbox = draw.textbbox((0, 0), ach_text, font=font_lg)
    tw = bbox[2] - bbox[0]
    draw.text(((CARD_W - tw) / 2, 44), ach_text, fill=(255, 255, 255), font=font_lg)

    # ── 评级 + FC/FS（底部中间） ──
    rate = RATE_MAP.get(entry.get("rate", ""), entry.get("rate", "").upper())
    fc = FC_MAP.get(entry.get("fc", ""), "")
    fs = FS_MAP.get(entry.get("fs", ""), "")
    parts = [rate]
    if fc:
        parts.append(fc)
    if fs:
        parts.append(fs)
    badge = "  ".join(parts)
    bbox = draw.textbbox((0, 0), badge, font=font_md)
    bw = bbox[2] - bbox[0]
    draw.text(((CARD_W - bw) / 2, 76), badge, fill=(255, 220, 100), font=font_md)


async def generate_b50_image(
    standard: List[dict],
    dx: List[dict],
    player_name: str,
    sd_rating: int,
    dx_rating: int,
) -> Image.Image:

    # ── 计算画布：以上限宽度为准 ──
    sd_block_w = SD_COLS * CARD_W + (SD_COLS - 1) * GAP
    dx_block_w = DX_COLS * CARD_W + (DX_COLS - 1) * GAP
    canvas_w = max(sd_block_w, dx_block_w) + MARGIN * 2
    canvas_h = (
        HEADER_H
        + SECTION_LABEL_H + SD_ROWS * CARD_H + (SD_ROWS - 1) * GAP
        + SECTION_GAP
        + SECTION_LABEL_H + DX_ROWS * CARD_H + (DX_ROWS - 1) * GAP
        + MARGIN
    )

    img = Image.new("RGB", (canvas_w, canvas_h), color=(18, 18, 40))
    draw = ImageDraw.Draw(img)

    # ── 字体 ──
    try:
        font_sm = ImageFont.truetype(str(FONT_PATH), 13)
        font_md = ImageFont.truetype(str(FONT_PATH), 16)
        font_lg = ImageFont.truetype(str(FONT_PATH), 26)
        font_title = ImageFont.truetype(str(FONT_PATH), 24)
        font_label = ImageFont.truetype(str(FONT_PATH), 18)
    except OSError:
        font_sm = font_md = font_lg = font_title = font_label = ImageFont.load_default()

    # ── 头部 ──
    total_rating = sd_rating + dx_rating
    draw.text((MARGIN, 8), f"Best 50 — {player_name}", fill=(255, 255, 255), font=font_title)
    draw.text(
        (MARGIN, 40),
        f"SD: {sd_rating}  +  DX: {dx_rating}  =  {total_rating}",
        fill=(180, 200, 255),
        font=font_label,
    )
    draw.text((MARGIN, 65), f"共 {len(standard) + len(dx)} 首", fill=(140, 140, 160), font=font_sm)

    # ── 辅助：绘制一个区块 ──
    def draw_section(entries: list, cols: int, rows: int,
                     start_x: int, start_y: int, label: str):
        # 区块标签
        draw.text((start_x, start_y - SECTION_LABEL_H),
                  label, fill=(180, 180, 180), font=font_label)

        for idx, entry in enumerate(entries):
            if idx >= cols * rows:
                break
            r, c = divmod(idx, cols)
            x = start_x + c * (CARD_W + GAP)
            y = start_y + r * (CARD_H + GAP)

            cover = _load_cover(entry["id"])
            cover = cover.resize((CARD_W, int(cover.size[1] * CARD_W / cover.size[0])))
            cover = cover.crop((0, (cover.size[1] - CARD_H) // 2,
                                CARD_W, (cover.size[1] + CARD_H) // 2))
            cover = cover.point(lambda p: int(p * 0.35))

            card = Image.new("RGB", (CARD_W, CARD_H))
            card.paste(cover)
            cd = ImageDraw.Draw(card)
            _draw_card(cd, entry, font_sm, font_md, font_lg)
            img.paste(card, (x, y))

        # 不足的格子填缺省封面
        total_slots = cols * rows
        for idx in range(len(entries), total_slots):
            r, c = divmod(idx, cols)
            x = start_x + c * (CARD_W + GAP)
            y = start_y + r * (CARD_H + GAP)

            cover = Image.open(FALLBACK_COVER).convert("RGB")
            cover = cover.resize((CARD_W, int(cover.size[1] * CARD_W / cover.size[0])))
            cover = cover.crop((0, (cover.size[1] - CARD_H) // 2,
                                CARD_W, (cover.size[1] + CARD_H) // 2))
            cover = cover.point(lambda p: int(p * 0.25))
            img.paste(cover, (x, y))

    # ── SD 区块（居中） ──
    sd_start_x = (canvas_w - sd_block_w) // 2
    sd_start_y = HEADER_H + SECTION_LABEL_H
    draw_section(standard, SD_COLS, SD_ROWS, sd_start_x, sd_start_y, "STANDARD")

    # ── DX 区块（居中） ──
    dx_start_y = sd_start_y + SD_ROWS * CARD_H + (SD_ROWS - 1) * GAP + SECTION_GAP + SECTION_LABEL_H
    dx_start_x = (canvas_w - dx_block_w) // 2
    draw_section(dx, DX_COLS, DX_ROWS, dx_start_x, dx_start_y, "DX")

    return img
