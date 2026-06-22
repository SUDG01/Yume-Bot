from nonebot import on_command
from nonebot.adapters import Message
from nonebot.rule import to_me



help_list = on_command("help", rule=to_me(), priority=2, block=True)


@help_list.handle()
async def help_handle():
    help_str = '''---YumeBot帮助文档---
/help 显示指令列表
/天气 <城市名称> 获取城市天气信息
/info <歌曲名称> 查询maimai乐曲信息
/b50 <二维码> 获取舞萌best50图片
/bind <api密钥> 绑定你的落雪查分器账户
---喵喵喵---
    '''

    await help_list.finish(help_str)

