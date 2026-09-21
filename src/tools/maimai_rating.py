"""Shared maimai DX Rating calculations and chart lookup helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping

from src.tools.data_cache import song_cache


ACHIEVEMENT_MILESTONES = (
    50.0,
    60.0,
    70.0,
    75.0,
    80.0,
    90.0,
    94.0,
    97.0,
    98.0,
    99.0,
    99.5,
    100.0,
    100.5,
)


def rating_coefficient(achievement: float) -> float:
    if achievement < 50.0:
        return 7.0
    if achievement < 60.0:
        return 8.0
    if achievement < 70.0:
        return 9.6
    if achievement < 75.0:
        return 11.2
    if achievement < 80.0:
        return 12.0
    if achievement < 90.0:
        return 13.6
    if achievement < 94.0:
        return 15.2
    if achievement < 97.0:
        return 16.8
    if achievement < 98.0:
        return 20.0
    if achievement < 99.0:
        return 20.3
    if achievement < 99.5:
        return 20.8
    if achievement < 100.0:
        return 21.1
    if achievement < 100.5:
        return 21.6
    return 22.4


def calculate_dx_rating(constant: float, achievement: float) -> int:
    capped_achievement = min(max(achievement, 0.0), 100.5)
    return math.floor(
        constant
        * (capped_achievement / 100.0)
        * rating_coefficient(achievement)
    )


def chart_constant(entry: Mapping[str, object]) -> float | None:
    song_id = entry.get("id")
    level_index = entry.get("level_index")
    chart_type = entry.get("type")
    if not isinstance(song_id, int) or not isinstance(level_index, int):
        return None

    song = song_cache.get(song_id)
    if song is None:
        return None

    for chart in song.charts:
        if chart.level == level_index and chart.type == chart_type:
            return chart.difficulty
    for chart in song.charts:
        if chart.level == level_index:
            return chart.difficulty
    return None
