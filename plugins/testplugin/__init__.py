import httpx
from nonebot import on_command
from nonebot.rule import to_me
from nonebot.adapters import Message
from nonebot.params import CommandArg

get_weather = on_command("天气", rule=to_me(), aliases={"weather"}, priority=5, block=True)
weather_api = "https://restapi.amap.com/v3/weather/weatherInfo?"
key_gd = "fd1e50f662bcdaedc15ecb1a09bbd0fe"


@get_weather.handle()
async def handle_weather(args: Message = CommandArg()):
    location = args.extract_plain_text().strip()
    if not location:
        await get_weather.finish("使用方法：/天气 <城市名>\n例如：/天气 上海")
        return

    try:
        resp = httpx.get(
            f"{weather_api}key={key_gd}&city={location}",
            trust_env=False,
            timeout=10.0,
        )
        data = resp.json()
    except Exception:
        await get_weather.finish("喵... 天气服务暂时不可用，请稍后再试")
        return

    if data.get("status") == "1" and data.get("lives"):
        live = data["lives"][0]
        await get_weather.finish(
            f"喵~ {location} 当前天气：{live['weather']}\n"
            f"温度：{live['temperature']}°C\n"
            f"报告时间：{live['reporttime']}"
        )
    else:
        await get_weather.finish(f"喵？找不到「{location}」的天气信息，检查一下城市名？")
