import asyncio
import base64
import math
from collections import OrderedDict
from io import BytesIO

import httpx
from nonebot import on_command
from nonebot.rule import to_me
from nonebot.adapters.onebot.v11 import Event, MessageSegment

from src.tools.database import get_api_key
from src.tools.b50_image import generate_b50_image

LXNET_BASE = "https://maimai.lxns.net/api/v0"
LXNET_ASSET_BASE = "https://assets2.lxns.net/maimai"
PLAYER_URL = f"{LXNET_BASE}/user/maimai/player"
BESTS_URL = f"{LXNET_BASE}/user/maimai/player/bests"
ASSET_CACHE_LIMIT = 48
ASSET_CACHE_BYTES_LIMIT = 64 * 1024 * 1024

_player_asset_cache: OrderedDict[str, bytes] = OrderedDict()
_player_asset_cache_bytes = 0

b50_cmd = on_command("b50", rule=to_me(), priority=5, block=True)


def _collection_id(player: dict, key: str) -> int | None:
    collection = player.get(key)
    if not isinstance(collection, dict):
        return None
    collection_id = collection.get("id")
    if (
        isinstance(collection_id, int)
        and not isinstance(collection_id, bool)
        and collection_id > 0
    ):
        return collection_id
    return None


async def _fetch_player_asset(
    client: httpx.AsyncClient,
    asset_type: str,
    asset_id: int,
) -> bytes | None:
    global _player_asset_cache_bytes

    cache_key = f"{asset_type}:{asset_id}"
    cached = _player_asset_cache.get(cache_key)
    if cached is not None:
        _player_asset_cache.move_to_end(cache_key)
        return cached

    try:
        response = await client.get(f"{LXNET_ASSET_BASE}/{asset_type}/{asset_id}.png")
    except httpx.RequestError:
        return None
    content_type = response.headers.get("content-type", "").lower()
    if (
        response.status_code != 200
        or not response.content
        or len(response.content) > 8 * 1024 * 1024
        or (content_type and not content_type.startswith("image/"))
    ):
        return None

    previous = _player_asset_cache.pop(cache_key, None)
    if previous is not None:
        _player_asset_cache_bytes -= len(previous)
    _player_asset_cache[cache_key] = response.content
    _player_asset_cache_bytes += len(response.content)
    _player_asset_cache.move_to_end(cache_key)
    while (
        len(_player_asset_cache) > ASSET_CACHE_LIMIT
        or _player_asset_cache_bytes > ASSET_CACHE_BYTES_LIMIT
    ):
        _, evicted = _player_asset_cache.popitem(last=False)
        _player_asset_cache_bytes -= len(evicted)
    return response.content


async def _fetch_player_assets(player: dict) -> dict[str, bytes]:
    specs = (
        ("icon", "icon"),
        ("name_plate", "plate"),
        ("frame", "frame"),
    )
    pending: list[tuple[str, str, int]] = []
    for player_key, asset_type in specs:
        asset_id = _collection_id(player, player_key)
        if asset_id is not None:
            pending.append((player_key, asset_type, asset_id))
    if not pending:
        return {}

    async with httpx.AsyncClient(trust_env=False, timeout=10.0, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(
                _fetch_player_asset(client, asset_type, asset_id)
                for _, asset_type, asset_id in pending
            )
        )
    return {
        player_key: content
        for (player_key, _, _), content in zip(pending, results)
        if content is not None
    }


@b50_cmd.handle()
async def handle_b50(event: Event):
    qq_id = event.get_user_id()

    # 1. 取绑定的 API Key
    api_key = await get_api_key(qq_id)
    if not api_key:
        await b50_cmd.finish(
            "喵？你还没有绑定 LXNet API Key 呢\n"
            "请先在 https://maimai.lxns.net 获取个人密钥，\n"
            "然后使用 /bind <你的密钥> 绑定"
        )
        return

    headers = {"X-User-Token": api_key}

    # 2. 获取玩家信息和 B50 数据
    async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
        try:
            player_resp, bests_resp = await asyncio.gather(
                client.get(PLAYER_URL, headers=headers),
                client.get(BESTS_URL, headers=headers),
            )
        except httpx.RequestError:
            await b50_cmd.finish("喵... 网络请求失败，请稍后再试")
            return

    if player_resp.status_code != 200 or bests_resp.status_code != 200:
        if player_resp.status_code == 401 or bests_resp.status_code == 401:
            await b50_cmd.finish("喵？你的 API Key 已失效，请重新绑定")
        else:
            await b50_cmd.finish(
                f"喵... LXNet 返回错误 "
                f"(player: {player_resp.status_code}, bests: {bests_resp.status_code})"
            )
        return

    player_data = player_resp.json()
    bests_data = bests_resp.json()

    if not player_data.get("success") or not bests_data.get("success"):
        await b50_cmd.finish("喵... LXNet 返回数据异常，请稍后再试")
        return

    player = player_data["data"]
    bests = bests_data["data"]

    # 3. 生成图片
    sd_list = bests.get("standard", [])
    dx_list = bests.get("dx", [])
    player_name = player.get("name", "Unknown")
    player_assets = await _fetch_player_assets(player)

    # 计算 SD / DX rating 总和
    sd_rating = sum(math.floor(e["dx_rating"]) for e in sd_list)
    dx_rating = sum(math.floor(e["dx_rating"]) for e in dx_list)

    try:
        img = await generate_b50_image(
            sd_list,
            dx_list,
            player_name,
            sd_rating,
            dx_rating,
            player=player,
            player_assets=player_assets,
        )
    except Exception as e:
        await b50_cmd.finish(f"喵... 生成图片时出错: {e}")
        return

    # 4. 转 base64 发送
    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    await b50_cmd.finish(
        MessageSegment.image(f"base64://{b64}")
    )
