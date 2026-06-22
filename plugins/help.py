from nonebot import on_command
from nonebot.rule import to_me

help_cmd = on_command("help", rule=to_me(), priority=1, block=True)


@help_cmd.handle()
async def help_handle():
    help_str = """—— YumeBot 帮助 ——

🎵 Maimai 相关
/info <歌名>     查询歌曲信息（支持别名、ID）
/b50             获取你的 Best 50 成绩图
/今日舞萌         每日人品值 + 宜忌 + 推歌

🔑 账号
/bind <密钥>     绑定 LXNet API Key
/bind clear      清除绑定
/bind            查看当前绑定状态

🌤 其他
/天气 <城市>     查询城市天气
/help            显示本帮助

—— 喵喵喵 ——"""
    await help_cmd.finish(help_str)
