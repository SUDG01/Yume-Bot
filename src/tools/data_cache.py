from typing import Dict, List
from src.tools.mai_music import Song


#曲目以及别名缓存
song_cache: Dict[int, Song] = {}
alias_cache: Dict[str, int] = {}