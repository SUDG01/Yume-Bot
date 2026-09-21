import asyncio
import base64
import re
from io import BytesIO

import httpx
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Event, MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import to_me

from src.tools.database import get_api_key
from src.tools.push_image import generate_push_image
from src.tools.push_recommend import build_push_plan


LXNET_BASE = "https://maimai.lxns.net/api/v0"
PLAYER_URL = f"{LXNET_BASE}/user/maimai/player"
BESTS_URL = f"{LXNET_BASE}/user/maimai/player/bests"
LEVEL_PATTERN = re.compile(r"^(?:[1-9]|1[0-5])(?:\+|\.\d)?$")

recommend_cmd = on_command(
    "推分",
    aliases={"tifen", "recommend", "suggest"},
    rule=to_me(),
    priority=5,
    block=True,
)


def _usage() -> str:
    return (
        "使用方法：\n"
        "/推分              推荐综合收益最高的谱面\n"
        "/推分 14           只看 14 难度\n"
        "/推分 14+          只看 14+ 难度\n"
        "/推分 13.7         只看定数 13.7\n"
        "/推分 15000        制定目标 Rating 计划\n"
        "/推分 15000 14+    同时指定目标与难度"
    )


def _parse_options(text: str) -> tuple[int | None, str | None]:
    target_rating: int | None = None
    level_filter: str | None = None
    tokens = text.replace("＋", "+").split()
    if len(tokens) > 2:
        raise ValueError

    for token in tokens:
        if token.isdigit() and int(token) >= 1000:
            value = int(token)
            if target_rating is not None or value > 30000:
                raise ValueError
            target_rating = value
            continue
        if LEVEL_PATTERN.fullmatch(token):
            if level_filter is not None:
                raise ValueError
            level_filter = token
            continue
        raise ValueError
    return target_rating, level_filter


@recommend_cmd.handle()
async def handle_recommend(event: Event, args: Message = CommandArg()):
    try:
        target_rating, level_filter = _parse_options(
            args.extract_plain_text().strip()
        )
    except ValueError:
        await recommend_cmd.finish(_usage())
        return

    qq_id = event.get_user_id()
    api_key = await get_api_key(qq_id)
    if not api_key:
        await recommend_cmd.finish(
            "喵？你还没有绑定 LXNet API Key 呢\n"
            "请先使用 /bind <你的密钥> 绑定"
        )
        return

    headers = {"X-User-Token": api_key}
    async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
        try:
            player_response, bests_response = await asyncio.gather(
                client.get(PLAYER_URL, headers=headers),
                client.get(BESTS_URL, headers=headers),
            )
        except httpx.RequestError:
            await recommend_cmd.finish("喵... 网络请求失败，请稍后再试")
            return

    if player_response.status_code != 200 or bests_response.status_code != 200:
        if 401 in {player_response.status_code, bests_response.status_code}:
            await recommend_cmd.finish("喵？你的 API Key 已失效，请重新绑定")
        else:
            await recommend_cmd.finish(
                "喵... LXNet 返回错误 "
                f"(player: {player_response.status_code}, "
                f"bests: {bests_response.status_code})"
            )
        return

    player_payload = player_response.json()
    bests_payload = bests_response.json()
    if not player_payload.get("success") or not bests_payload.get("success"):
        await recommend_cmd.finish("喵... LXNet 返回数据异常，请稍后再试")
        return

    player = player_payload.get("data", {})
    bests = bests_payload.get("data", {})
    if not isinstance(player, dict) or not isinstance(bests, dict):
        await recommend_cmd.finish("喵... LXNet 返回的数据格式不正确")
        return

    plan = build_push_plan(
        bests,
        target_rating=target_rating,
        level_filter=level_filter,
    )
    if target_rating is not None and plan.current_total >= target_rating:
        await recommend_cmd.finish(
            f"喵~ 你当前的 B50 Rating 是 {plan.current_total}，"
            f"已经达到目标 {target_rating} 啦！"
        )
        return
    if not plan.recommendations:
        filter_text = f"「{level_filter}」范围内" if level_filter else "当前候选中"
        await recommend_cmd.finish(
            f"喵？{filter_text}暂时没有 3.5% 达成率以内的有效推分方案。"
        )
        return

    try:
        image = await generate_push_image(
            plan,
            str(player.get("name", "Unknown")),
        )
    except Exception as error:
        await recommend_cmd.finish(f"喵... 生成推分计划时出错: {error}")
        return

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    await recommend_cmd.finish(MessageSegment.image(f"base64://{encoded}"))
