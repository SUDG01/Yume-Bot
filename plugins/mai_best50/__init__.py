import base64
import math
from io import BytesIO

import httpx
from nonebot import on_command
from nonebot.rule import to_me
from nonebot.adapters.onebot.v11 import Event, MessageSegment

from src.tools.database import get_api_key
from src.tools.b50_image import generate_b50_image

LXNET_BASE = "https://maimai.lxns.net/api/v0"
PLAYER_URL = f"{LXNET_BASE}/user/maimai/player"
BESTS_URL = f"{LXNET_BASE}/user/maimai/player/bests"

b50_cmd = on_command("b50", rule=to_me(), priority=5, block=True)


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
            player_resp = await client.get(PLAYER_URL, headers=headers)
            bests_resp = await client.get(BESTS_URL, headers=headers)
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

    # 计算 SD / DX rating 总和
    sd_rating = sum(math.floor(e["dx_rating"]) for e in sd_list)
    dx_rating = sum(math.floor(e["dx_rating"]) for e in dx_list)

    try:
        img = await generate_b50_image(sd_list, dx_list, player_name, sd_rating, dx_rating)
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
