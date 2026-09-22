"""Normalize LXNet trend, activity, and recent-score payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


@dataclass(frozen=True)
class TrendPoint:
    day: date
    total: int
    standard: int
    dx: int


@dataclass(frozen=True)
class RecentScore:
    entry: Mapping[str, object]
    occurred_at: datetime | None


@dataclass(frozen=True)
class TrendSummary:
    days: int
    start_day: date
    end_day: date
    points: tuple[TrendPoint, ...]
    activity: Mapping[date, int]
    recents: tuple[RecentScore, ...]

    @property
    def current_total(self) -> int:
        return self.points[-1].total if self.points else 0

    @property
    def current_standard(self) -> int:
        return self.points[-1].standard if self.points else 0

    @property
    def current_dx(self) -> int:
        return self.points[-1].dx if self.points else 0

    @property
    def rating_gain(self) -> int:
        if len(self.points) < 2:
            return 0
        return self.points[-1].total - self.points[0].total

    @property
    def peak_total(self) -> int:
        return max((point.total for point in self.points), default=0)

    @property
    def active_days(self) -> int:
        return sum(1 for count in self.activity.values() if count > 0)

    @property
    def update_count(self) -> int:
        return sum(self.activity.values())


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_day(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_trend_points(payload: object) -> list[TrendPoint]:
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        return []
    by_day: dict[date, TrendPoint] = {}
    for entry in payload:
        if not isinstance(entry, Mapping):
            continue
        day = _parse_day(entry.get("date"))
        if day is None:
            continue
        by_day[day] = TrendPoint(
            day=day,
            total=_as_int(entry.get("total")),
            standard=_as_int(entry.get("standard")),
            dx=_as_int(entry.get("dx")),
        )
    return sorted(by_day.values(), key=lambda point: point.day)


def _window_points(
    points: Sequence[TrendPoint],
    start_day: date,
    end_day: date,
    fallback_total: int,
    fallback_standard: int,
    fallback_dx: int,
) -> list[TrendPoint]:
    prior = [point for point in points if point.day < start_day]
    visible = [point for point in points if start_day <= point.day <= end_day]
    if prior and (not visible or visible[0].day > start_day):
        previous = prior[-1]
        visible.insert(
            0,
            TrendPoint(
                day=start_day,
                total=previous.total,
                standard=previous.standard,
                dx=previous.dx,
            ),
        )
    if not visible and fallback_total:
        visible.append(
            TrendPoint(end_day, fallback_total, fallback_standard, fallback_dx)
        )
    elif visible and fallback_total:
        latest = visible[-1]
        current = TrendPoint(
            day=end_day,
            total=fallback_total,
            standard=fallback_standard or latest.standard,
            dx=fallback_dx or latest.dx,
        )
        if current != latest:
            if latest.day == end_day:
                visible[-1] = current
            else:
                visible.append(current)
    if len(visible) == 1 and visible[0].day != start_day:
        point = visible[0]
        visible.insert(
            0,
            TrendPoint(start_day, point.total, point.standard, point.dx),
        )
    return visible


def _parse_activity(
    payload: object,
    start_day: date,
    end_day: date,
) -> dict[date, int]:
    if not isinstance(payload, Mapping):
        return {}
    activity: dict[date, int] = {}
    for raw_day, raw_count in payload.items():
        day = _parse_day(raw_day)
        if day is None or not start_day <= day <= end_day:
            continue
        activity[day] = max(_as_int(raw_count), 0)
    return activity


def _parse_recents(
    payload: object,
    start_day: date,
    end_day: date,
) -> list[RecentScore]:
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        return []
    scores: list[RecentScore] = []
    for entry in payload:
        if not isinstance(entry, Mapping):
            continue
        occurred_at = _parse_datetime(
            entry.get("play_time") or entry.get("upload_time")
        )
        if occurred_at is not None:
            occurred_day = occurred_at.date()
            if not start_day <= occurred_day <= end_day:
                continue
        scores.append(RecentScore(dict(entry), occurred_at))
    scores.sort(
        key=lambda score: score.occurred_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return scores[:5]


def build_trend_summary(
    trend_payload: object,
    heatmap_payload: object,
    recents_payload: object,
    *,
    days: int,
    fallback_total: int = 0,
    fallback_standard: int = 0,
    fallback_dx: int = 0,
    today: date | None = None,
) -> TrendSummary:
    end_day = today or date.today()
    start_day = end_day - timedelta(days=days - 1)
    all_points = _parse_trend_points(trend_payload)
    points = _window_points(
        all_points,
        start_day,
        end_day,
        fallback_total,
        fallback_standard,
        fallback_dx,
    )
    activity = _parse_activity(heatmap_payload, start_day, end_day)
    recents = _parse_recents(recents_payload, start_day, end_day)
    return TrendSummary(
        days=days,
        start_day=start_day,
        end_day=end_day,
        points=tuple(points),
        activity=activity,
        recents=tuple(recents),
    )
