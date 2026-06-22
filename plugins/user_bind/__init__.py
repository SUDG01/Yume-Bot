import httpx
from nonebot import on_command, get_driver
from nonebot.rule import to_me
from nonebot.adapters import Message
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Event

from src.tools.database import init_db, bind_user, unbind_user, get_api_key

# ── 启动时初始化数据库 ──────────────────────
driver = get_driver()


@driver.on_startup
async def _init_bindings_db():
    await init_db()
    print("User bindings database initialized.")


# ── 命令注册 ────────────────────────────────
LXNET_PLAYER_URL = "https://maimai.lxns.net/api/v0/user/maimai/player"

bind_cmd = on_command("bind", aliases={"绑定"},rule=to_me(), priority=4, block=True)


@bind_cmd.handle()
async def handle_bind(event: Event, args: Message = CommandArg()):
    user_input = args.extract_plain_text().strip()
    qq_id = event.get_user_id()

    # 无参数 → 显示状态或帮助
    if not user_input:
        existing_key = await get_api_key(qq_id)
        if existing_key:
            masked = (
                existing_key[:4] + "****" + existing_key[-4:]
                if len(existing_key) > 8
                else "****"
            )
            await bind_cmd.finish(
                f"喵~ 你已绑定的 API Key: {masked}\n"
                f"使用 /bind clear 解绑，/bind <新密钥> 更换"
            )
        else:
            await bind_cmd.finish(
                "使用方法:\n"
                "/bind <lxnet_api_key>  绑定你的 LXNet API Key\n"
                "/bind clear            清除绑定"
            )
        return

    # clear → 解绑
    if user_input.lower() == "clear":
        deleted = await unbind_user(qq_id)
        if deleted:
            await bind_cmd.finish("喵~ 已清除你的 API Key 绑定")
        else:
            await bind_cmd.finish("喵？你还没有绑定过 API Key 呢")
        return

    # 验证并绑定
    api_key = user_input
    async with httpx.AsyncClient(trust_env=False) as client:
        try:
            resp = await client.get(
                LXNET_PLAYER_URL,
                headers={"X-User-Token": api_key},
                timeout=10.0,
            )
        except httpx.RequestError:
            await bind_cmd.finish("喵... 网络请求失败，请稍后再试")
            return

    if resp.status_code == 200:
        await bind_user(qq_id, api_key)
        await bind_cmd.finish("喵~ API Key 验证成功，已绑定！")
    elif resp.status_code == 401:
        await bind_cmd.finish("喵？这个 API Key 无效哦，请检查后重试")
    elif resp.status_code == 403:
        await bind_cmd.finish("喵？这个 API Key 没有权限访问，请检查后重试")
    else:
        await bind_cmd.finish(
            f"喵... LXNet 返回了意外的状态码 ({resp.status_code})，请稍后再试"
        )
