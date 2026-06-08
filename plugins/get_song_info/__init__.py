from nonebot import on_command
from nonebot.rule import to_me
from nonebot.adapters import Message
from nonebot.params import CommandArg
import httpx
import math
import json

base_url_lx = "https://maimai.lxns.net/api/v0/maimai/song/"

get_song_info = on_command("info", rule=to_me(), aliases={"maiinfo"}, priority=5, block=True)

def calculate_score_sss(diff:int) -> int:
    return math.floor(diff*1.0000*21.6)

def calculate_score_sssplus(diff:int) -> int:
    return math.floor(diff*1.0050*22.4)

@get_song_info.handle()
async def handle_first_receive(arg: Message = CommandArg()):
    if song_id := arg.extract_plain_text():
        response_raw = httpx.get(f"{base_url_lx}{song_id}").text
        response = json.loads(response_raw)
        if response["difficulties"]["dx"] != []:
            highest_difficulty = response["difficulties"]["dx"][-1]["level_value"]
        else:
            highest_difficulty = response["difficulties"]["standard"][-1]["level_value"]
        # highest_difficulty = response["difficulties"][0]["level_value"]
        rank_sss_score = calculate_score_sss(highest_difficulty)
        rank_sssplus_score = calculate_score_sssplus(highest_difficulty)

        await get_song_info.finish(f"曲名：{response['title']}\n分类：{response['genre']}\n难度：{highest_difficulty}\nsss分数：{rank_sss_score}\nsss+分数：{rank_sssplus_score}")
    else:
        await get_song_info.finish("正确用法：/info [歌曲ID]，例如：/info 12345")