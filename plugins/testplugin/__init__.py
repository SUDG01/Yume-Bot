import httpx
from nonebot import on_command
from nonebot.rule import to_me
from nonebot.adapters import Message
from nonebot.params import CommandArg
import json

get_weather = on_command("天气", rule=to_me(), aliases={"weather"}, priority=5, block=True)  #响应器示例
weather_api = "https://restapi.amap.com/v3/weather/weatherInfo?"
key_gd="fd1e50f662bcdaedc15ecb1a09bbd0fe"


@get_weather.handle()
#此处为事件处理函数示例，如示例所示，事件处理函数必须为异步函数，且必须使用装饰器 @响应器.handle() 来注册
async def handle_func(args: Message = CommandArg()):
    if location:= args.extract_plain_text():
        response = httpx.get(f"{weather_api}key={key_gd}&city={location}").text
        data = json.loads(response)
        if data["status"] == "1":
            await get_weather.finish(f"喵~当前{location}的天气是{data['lives'][0]['weather']}，温度是{data['lives'][0]['temperature']}°C，报告时间为{data['lives'][0]['reporttime']}~")
    else:
        await get_weather.finish("正确用法：/天气 [城市名]，例如：/天气 上海")
    