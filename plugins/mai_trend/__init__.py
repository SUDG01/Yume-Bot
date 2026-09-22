import asyncio
import base64
from io import BytesIO

import httpx
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Event, MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import to_me

from src.tools.database import get_api_key
from src.tools.trend_data import build_trend_summary
from src.tools.trend_image import generate_trend_image


LXNET_BASE = "https://maimai.lxns.net/api/v0/user/maimai/player"
PLAYER_URL = LXNET_BASE
BESTS_URL = f"{LXNET_BASE}/bests"
TREND_URL = f"{LXNET_BASE}/trend"
HEATMAP_URL = f"{LXNET_BASE}/heatmap"
RECENTS_URL = f"{LXNET_BASE}/recents"
SUPPORTED_DAYS = {7, 30, 90}

trend_cmd = on_command(
    "成绩趋势",
    aliases={"趋势", "trend"},
    rule=to_me(),
    priority=5,
    block=True,
)


def _unwrap(response: httpx.Response, default: object) -> object:
    try:
        payload = response.json()
    except ValueError:
        return default
    if not isinstance(payload, dict) or not payload.get("success"):
        return default
    return payload.get("data", default)


@trend_cmd.handle()
async def handle_trend(event: Event, args: Message = CommandArg()):
    raw_days = args.extract_plain_text().strip()
    try:
        days = int(raw_days) if raw_days else 30
    except ValueError:
        days = 0
    if days not in SUPPORTED_DAYS:
        await trend_cmd.finish(
            "使用方法：/成绩趋势 [7|30|90]\n例如：/成绩趋势 30"
        )
        return

    api_key = await get_api_key(event.get_user_id())
    if not api_key:
        await trend_cmd.finish(
            "喵？你还没有绑定 LXNet API Key 呢\n"
            "请先使用 /bind <你的密钥> 绑定"
        )
        return

    headers = {"X-User-Token": api_key}
    async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
        try:
            (
                player_response,
                bests_response,
                trend_response,
                heatmap_response,
                recents_response,
            ) = await asyncio.gather(
                client.get(PLAYER_URL, headers=headers),
                client.get(BESTS_URL, headers=headers),
                client.get(TREND_URL, headers=headers),
                client.get(HEATMAP_URL, headers=headers),
                client.get(RECENTS_URL, headers=headers),
            )
        except httpx.RequestError:
            await trend_cmd.finish("喵... 网络请求失败，请稍后再试")
            return

    responses = (
        player_response,
        bests_response,
        trend_response,
        heatmap_response,
        recents_response,
    )
    if any(response.status_code == 401 for response in responses):
        await trend_cmd.finish("喵？你的 API Key 已失效，请重新绑定")
        return
    if player_response.status_code != 200 or trend_response.status_code != 200:
        await trend_cmd.finish(
            "喵... LXNet 趋势接口暂时不可用 "
            f"(player: {player_response.status_code}, "
            f"trend: {trend_response.status_code})"
        )
        return

    player = _unwrap(player_response, {})
    bests = (
        _unwrap(bests_response, {})
        if bests_response.status_code == 200
        else {}
    )
    trend = _unwrap(trend_response, [])
    heatmap = (
        _unwrap(heatmap_response, {})
        if heatmap_response.status_code == 200
        else {}
    )
    recents = (
        _unwrap(recents_response, [])
        if recents_response.status_code == 200
        else []
    )
    if not isinstance(player, dict):
        await trend_cmd.finish("喵... LXNet 返回的玩家数据格式不正确")
        return

    try:
        current_rating = int(player.get("rating", 0))
    except (TypeError, ValueError):
        current_rating = 0
    if isinstance(bests, dict):
        try:
            standard_rating = int(bests.get("standard_total", 0))
            dx_rating = int(bests.get("dx_total", 0))
        except (TypeError, ValueError):
            standard_rating = dx_rating = 0
    else:
        standard_rating = dx_rating = 0
    component_total = standard_rating + dx_rating
    if component_total > 0:
        current_rating = component_total
    summary = build_trend_summary(
        trend,
        heatmap,
        recents,
        days=days,
        fallback_total=current_rating,
        fallback_standard=standard_rating,
        fallback_dx=dx_rating,
    )
    if not summary.points or summary.current_total <= 0:
        await trend_cmd.finish("喵？LXNet 还没有可用的 Rating 趋势数据")
        return

    try:
        image = await generate_trend_image(
            summary,
            str(player.get("name", "Unknown")),
        )
    except Exception as error:
        await trend_cmd.finish(f"喵... 生成趋势图时出错: {error}")
        return

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    await trend_cmd.finish(MessageSegment.image(f"base64://{encoded}"))
