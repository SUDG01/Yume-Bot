import httpx
import math
from dataclasses import dataclass
from typing import List

@dataclass
class Chart:
    type: str           #曲目类型(标准或dx)
    difficulty: float   #定数
    level: int          #难度等级(紫白分类...)
    desn: str           #铺师

    @property
    def calculate_score_sss(self) -> int:
        return math.floor(self.difficulty * 1.0000 * 21.6)
    
    @property
    def calculate_score_sssp(self) -> int:
        return math.floor(self.difficulty * 1.0050 * 22.4)
    sssscore: int = 0   #鸟的分
    ssspscore: int = 0  #鸟加的分


@dataclass
class Song:
    id: int                #歌曲id
    title: str              #歌曲名称
    genre: str             #歌曲分类
    charts: List[Chart]    #包含的谱面列表


async def music_list():
    '''
        获取落雪的maimai曲目列表,并对其进行处理
    '''
    all_songs: List[Song] = []
    
    url = "https://maimai.lxns.net/api/v0/maimai/song/list"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 200:
            resp_data = response.json()["songs"]
            for data in resp_data:
                current_song_charts = []
                if data["difficulties"]["dx"]:
                    for chart in data["difficulties"]["dx"]:
                        current_song_charts.append(Chart(
                            type="dx",
                            difficulty=chart["level_value"],
                            level=chart["difficulty"],
                            desn=chart["note_designer"],
                        ))

                if data["difficulties"]["standard"]:
                    for chart in data["difficulties"]["standard"]:
                        current_song_charts.append(Chart(
                            type="standard",
                            difficulty=chart["level_value"],
                            level=chart["difficulty"],
                            desn=chart["note_designer"],
                        ))
                song = Song(
                    id=data["id"],
                    title=data["title"],
                    genre=data["genre"],
                    charts=current_song_charts
                )
                
                all_songs.append(song)

        else:
            return
            
    return all_songs


