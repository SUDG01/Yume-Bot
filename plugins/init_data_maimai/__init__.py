from nonebot import get_driver
from src.tools.mai_music import music_list
from src.tools.get_music_alias import alias_dict
from src.tools.data_cache import song_cache, alias_cache

driver = get_driver()

@driver.on_startup
async def init_data_maimai():
    print("小Yume正在处理歌曲信息喵")

    songs_list = await music_list()
    if songs_list is not None:
        for song in songs_list:
            song_cache[song.id] = song
    
    aliases = await alias_dict()
    if aliases is not None:
        alias_cache.update(aliases)

    print(f"加载了{len(song_cache)}首歌，以及{len(alias_cache)}个别名")
