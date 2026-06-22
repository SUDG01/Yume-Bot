from nonebot import on_command
from nonebot.rule import to_me
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg
from src.tools.data_cache import song_cache, alias_cache

get_song_info = on_command("info", rule=to_me(), aliases={"maiinfo"}, priority=5, block=True)

@get_song_info.handle()
async def song_info(arg: Message = CommandArg()):
    user_input = arg.extract_plain_text().strip()

    if(user_input.isdigit() and song_cache[int(user_input)]):
        song = song_cache[int(user_input)]
    elif(user_input in alias_cache):
        song_id = alias_cache[user_input]
        song = song_cache[song_id]
    else:
        await get_song_info.finish("喵？小Yume找不到这首歌... 看看歌名有没有打错？")
        return
    
    level_names = {0:"绿",1:"黄",2:"红",3:"紫",4:"白"}
    chart_str = ""

    for chart in song.charts:
        diff_name = level_names[chart.level]
        chart_str += f"{diff_name}谱定数:{chart.difficulty} [{chart.type}]"
        if diff_name == "白":
            chart_str += f" sss得分:{chart.calculate_score_sss}，sss+得分:{chart.calculate_score_sssp}"
        elif diff_name == "紫":
            chart_str += f" sss得分:{chart.calculate_score_sss}，sss+得分:{chart.calculate_score_sssp}"
        chart_str += "\n"
    
    img_str = f"https://assets2.lxns.net/maimai/jacket/{song.id}.png"
    song_info_str = f'''
曲名:{song.title}
分类:{song.genre}
{chart_str}
—— Info From LXNet ——'''
    
    
    await get_song_info.finish(MessageSegment.image(img_str)+song_info_str)