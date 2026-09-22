import math
from dataclasses import dataclass

import httpx


@dataclass
class Chart:
    type: str
    difficulty: float
    level: int
    desn: str
    display_level: str = ""
    version: int | None = None

    @property
    def calculate_score_sss(self) -> int:
        return math.floor(self.difficulty * 1.0000 * 21.6)

    @property
    def calculate_score_sssp(self) -> int:
        return math.floor(self.difficulty * 1.0050 * 22.4)


@dataclass
class Song:
    id: int
    title: str
    genre: str
    charts: list[Chart]
    artist: str = ""
    bpm: int | None = None
    version: int | None = None
    disabled: bool = False


def _parse_chart(chart_type: str, data: dict) -> Chart | None:
    try:
        return Chart(
            type=chart_type,
            difficulty=float(data["level_value"]),
            level=int(data["difficulty"]),
            desn=str(data.get("note_designer") or ""),
            display_level=str(data.get("level") or ""),
            version=(
                int(data["version"])
                if isinstance(data.get("version"), (int, float))
                else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def music_list() -> list[Song]:
    """Fetch and normalize the current LXNet maimai song catalog."""
    url = "https://maimai.lxns.net/api/v0/maimai/song/list"
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
            response = await client.get(url)
    except httpx.RequestError:
        return []
    if response.status_code != 200:
        return []

    try:
        raw_songs = response.json()["songs"]
    except (KeyError, TypeError, ValueError):
        return []

    songs: list[Song] = []
    for data in raw_songs:
        if not isinstance(data, dict):
            continue
        difficulties = data.get("difficulties")
        if not isinstance(difficulties, dict):
            continue

        charts: list[Chart] = []
        for chart_type in ("dx", "standard"):
            raw_charts = difficulties.get(chart_type) or []
            for raw_chart in raw_charts:
                if not isinstance(raw_chart, dict):
                    continue
                chart = _parse_chart(chart_type, raw_chart)
                if chart is not None:
                    charts.append(chart)

        try:
            song_id = int(data["id"])
            title = str(data["title"])
        except (KeyError, TypeError, ValueError):
            continue

        bpm_value = data.get("bpm")
        version_value = data.get("version")
        songs.append(
            Song(
                id=song_id,
                title=title,
                genre=str(data.get("genre") or "未知分类"),
                charts=charts,
                artist=str(data.get("artist") or ""),
                bpm=(
                    int(bpm_value)
                    if isinstance(bpm_value, (int, float))
                    else None
                ),
                version=(
                    int(version_value)
                    if isinstance(version_value, (int, float))
                    else None
                ),
                disabled=bool(data.get("disabled", False)),
            )
        )
    return songs
