from nonebot import on_command
from nonebot.adapters.onebot.v11 import Event, MessageSegment

from src.tools.data_cache import song_cache
from src.tools.qqhash import hash as hash_q

jrwm = on_command("今日舞萌", aliases={"jrwm"}, priority=5, block=True)

wm_str = "拼机,推分,越级,下埋,夜勤,练底力,练手法,打旧框,干饭,抓绝赞,收歌".split(",")

@jrwm.handle()
async def generate_today_wm(event: Event):
    qq_id = int(event.get_user_id())
    h = hash_q(qq_id)

    ren_pin = h % 100

    wm_value = []
    for i in range(11):
        wm_value.append(h & 3)
        h >>= 2

    _str = f"今日人品值：{ren_pin}\n"

    for i in range(11):
        if wm_value[i] == 3:
            _str += f"宜 {wm_str[i]}\n"
        elif wm_value[i] == 0:
            _str += f"忌 {wm_str[i]}\n"

    _str += "小Yume提醒您：打机时不要大力拍打或滑动哦\n今日推荐歌曲："

    # song_cache 是 dict，先转列表再按索引取
    songs = list(song_cache.values())
    music = songs[h % len(songs)]

    diff_parts = [str(c.difficulty) for c in music.charts]
    str_song_info = f"\nID.{music.id} - {music.title}\n定数: {' / '.join(diff_parts)}"

    await jrwm.finish(
        MessageSegment.text(_str)
        + MessageSegment.image(
            f"https://assets2.lxns.net/maimai/jacket/{music.id}.png"
        )
        + MessageSegment.text(str_song_info)
    )
