from httpx import get
import os

json_music = get(
    "https://maimai.lxns.net/api/v0/maimai/song/list",
    trust_env=False,
    timeout=30
).json()["songs"]

for i in json_music:
    song_id = i["id"]
    songs_cover_url = f"https://assets2.lxns.net/maimai/jacket/{song_id}.png"

    filepath = f"{song_id}.png"
    if os.path.exists(filepath):
        print(f"跳过 {song_id}.png (已存在)")
        continue

    try:
        cover = get(songs_cover_url, trust_env=False, timeout=30)
        cover.raise_for_status()
        with open(filepath, mode="wb") as f:
            f.write(cover.content)
        print(f"下载 {song_id}.png ✅")
    except Exception as e:
        print(f"失败 {song_id}.png: {e}")
