"""Song and chart search helpers over the in-memory LXNet catalog."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from src.tools.data_cache import alias_cache, song_cache
from src.tools.mai_music import Chart, Song


@dataclass(frozen=True)
class SongMatch:
    song: Song
    score: float
    matched_text: str
    matched_by: str


@dataclass(frozen=True)
class ChartMatch:
    song: Song
    chart: Chart


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _text_match_score(query: str, candidate: str, *, alias: bool) -> float:
    if not candidate:
        return 0.0
    penalty = 0.02 if alias else 0.0
    if query == candidate:
        return 1.0 - penalty
    if candidate.startswith(query):
        return 0.94 - penalty + min(len(query) / len(candidate), 1.0) * 0.03
    if query in candidate:
        return 0.84 - penalty + min(len(query) / len(candidate), 1.0) * 0.06
    if len(query) < 2:
        return 0.0
    ratio = SequenceMatcher(None, query, candidate).ratio()
    return ratio * (0.80 - penalty) if ratio >= 0.55 else 0.0


def search_songs(query: str, *, limit: int = 10) -> list[SongMatch]:
    query_key = normalize_text(query)
    if not query_key:
        return []

    matches: dict[int, SongMatch] = {}
    if query.strip().isdigit():
        song = song_cache.get(int(query.strip()))
        if song is not None:
            return [SongMatch(song, 1.1, str(song.id), "id")]

    def consider(song: Song, candidate: str, matched_by: str, alias: bool) -> None:
        candidate_key = normalize_text(candidate)
        score = _text_match_score(query_key, candidate_key, alias=alias)
        if score <= 0:
            return
        current = matches.get(song.id)
        if current is None or score > current.score:
            matches[song.id] = SongMatch(song, score, candidate, matched_by)

    for song in song_cache.values():
        consider(song, song.title, "title", False)
    for alias_name, song_id in alias_cache.items():
        song = song_cache.get(song_id)
        if song is not None:
            consider(song, alias_name, "alias", True)

    ranked = sorted(
        matches.values(),
        key=lambda match: (-match.score, len(match.song.title), match.song.id),
    )
    return ranked[:limit]


def resolve_song(query: str) -> tuple[Song | None, list[SongMatch]]:
    matches = search_songs(query, limit=10)
    if not matches:
        return None, []
    if matches[0].score >= 0.98:
        return matches[0].song, matches
    if len(matches) == 1 and matches[0].score >= 0.65:
        return matches[0].song, matches
    if matches[0].score >= 0.88 and matches[0].score - matches[1].score >= 0.12:
        return matches[0].song, matches
    return None, matches


def chart_level_text(chart: Chart) -> str:
    return chart.display_level or f"{chart.difficulty:g}"


def search_charts(
    *,
    constant_min: float | None = None,
    constant_max: float | None = None,
    chart_type: str | None = None,
    level_index: int | None = None,
    display_level: str | None = None,
    genre_query: str | None = None,
) -> list[ChartMatch]:
    genre_key = normalize_text(genre_query or "")
    display_level_key = unicodedata.normalize(
        "NFKC", display_level or ""
    ).strip()
    results: list[ChartMatch] = []

    for song in song_cache.values():
        if song.disabled:
            continue
        if genre_key and genre_key not in normalize_text(song.genre):
            continue
        for chart in song.charts:
            if chart_type and chart.type != chart_type:
                continue
            if level_index is not None and chart.level != level_index:
                continue
            if (
                display_level_key
                and unicodedata.normalize("NFKC", chart_level_text(chart)).strip()
                != display_level_key
            ):
                continue
            if constant_min is not None and chart.difficulty < constant_min - 0.0001:
                continue
            if constant_max is not None and chart.difficulty > constant_max + 0.0001:
                continue
            results.append(ChartMatch(song, chart))

    return sorted(
        results,
        key=lambda match: (
            match.chart.difficulty,
            match.song.title.casefold(),
            match.chart.type,
            match.chart.level,
        ),
    )
