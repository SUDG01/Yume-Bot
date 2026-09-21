"""Build realistic maimai B50 improvement plans from LXNet best lists."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.tools.maimai_rating import (
    ACHIEVEMENT_MILESTONES,
    calculate_dx_rating,
    chart_constant,
)


ScoreKey = tuple[int, str, int]
CandidateKey = tuple[str, ScoreKey]
MAX_ACHIEVEMENT_JUMP = 3.5


@dataclass(frozen=True)
class PushRecommendation:
    entry: Mapping[str, object]
    category: str
    in_b50: bool
    constant: float
    current_achievement: float
    target_achievement: float
    current_rating: int
    target_rating: int
    gain: int

    @property
    def category_label(self) -> str:
        return "B35" if self.category == "standard" else "B15"

    @property
    def effort(self) -> float:
        return self.target_achievement - self.current_achievement


@dataclass(frozen=True)
class PushPlan:
    recommendations: tuple[PushRecommendation, ...]
    current_total: int
    projected_total: int
    target_rating: int | None
    level_filter: str | None

    @property
    def projected_gain(self) -> int:
        return self.projected_total - self.current_total


@dataclass(frozen=True)
class _CandidateOption:
    entry: Mapping[str, object]
    category: str
    key: ScoreKey
    in_b50: bool
    constant: float
    current_achievement: float
    target_achievement: float
    current_rating: int
    target_rating: int

    @property
    def effort(self) -> float:
        return self.target_achievement - self.current_achievement


def _score_key(entry: Mapping[str, object]) -> ScoreKey | None:
    song_id = entry.get("id")
    chart_type = entry.get("type")
    level_index = entry.get("level_index")
    if (
        not isinstance(song_id, int)
        or not isinstance(chart_type, str)
        or not isinstance(level_index, int)
    ):
        return None
    return song_id, chart_type, level_index


def _score_rating(entry: Mapping[str, object]) -> int:
    try:
        return math.floor(float(entry.get("dx_rating", 0)))
    except (TypeError, ValueError):
        return 0


def _score_achievement(entry: Mapping[str, object]) -> float:
    try:
        return float(entry.get("achievements", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _score_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [entry for entry in value if isinstance(entry, Mapping)]


def _normalize_level(value: str) -> str:
    return value.strip().replace("＋", "+").upper()


def _matches_level(
    entry: Mapping[str, object],
    constant: float,
    level_filter: str | None,
) -> bool:
    if not level_filter:
        return True

    expected = _normalize_level(level_filter)
    actual = _normalize_level(str(entry.get("level", "")))
    if actual and actual == expected:
        return True

    try:
        if "." in expected:
            return math.isclose(constant, float(expected), abs_tol=0.01)
        if expected.endswith("+"):
            base = int(expected[:-1])
            return base + 0.7 <= constant < base + 1.0
        base = int(expected)
        return base <= constant < base + 0.7
    except ValueError:
        return False


def _trim_state(state: Mapping[ScoreKey, int], limit: int) -> dict[ScoreKey, int]:
    ranked = sorted(state.items(), key=lambda item: item[1], reverse=True)
    return dict(ranked[:limit])


def _apply_option(
    state: Mapping[ScoreKey, int],
    option: _CandidateOption,
    limit: int,
) -> tuple[dict[ScoreKey, int], int]:
    updated = dict(state)
    updated[option.key] = max(updated.get(option.key, 0), option.target_rating)
    updated = _trim_state(updated, limit)
    gain = sum(updated.values()) - sum(state.values())
    return updated, gain


def _build_options(
    bests: Mapping[str, object],
    level_filter: str | None,
) -> tuple[
    dict[str, dict[ScoreKey, int]],
    dict[CandidateKey, list[_CandidateOption]],
]:
    category_specs = (
        ("standard", "standard_selections", 35),
        ("dx", "dx_selections", 15),
    )
    states: dict[str, dict[ScoreKey, int]] = {}
    options: dict[CandidateKey, list[_CandidateOption]] = {}

    for category, selection_key, limit in category_specs:
        current_entries = _score_list(bests.get(category))
        selection_entries = _score_list(bests.get(selection_key))

        current_state: dict[ScoreKey, int] = {}
        current_keys: set[ScoreKey] = set()
        for entry in current_entries:
            key = _score_key(entry)
            if key is None:
                continue
            current_keys.add(key)
            current_state[key] = _score_rating(entry)
        states[category] = _trim_state(current_state, limit)

        seen: set[ScoreKey] = set()
        for entry in (*current_entries, *selection_entries):
            key = _score_key(entry)
            if key is None or key in seen:
                continue
            seen.add(key)

            constant = chart_constant(entry)
            if constant is None or not _matches_level(entry, constant, level_filter):
                continue

            current_achievement = _score_achievement(entry)
            current_rating = _score_rating(entry)
            candidate_options: list[_CandidateOption] = []
            for target_achievement in ACHIEVEMENT_MILESTONES:
                effort = target_achievement - current_achievement
                if effort <= 0.00005 or effort > MAX_ACHIEVEMENT_JUMP:
                    continue
                target_rating = calculate_dx_rating(constant, target_achievement)
                if target_rating <= current_rating:
                    continue
                candidate_options.append(
                    _CandidateOption(
                        entry=dict(entry),
                        category=category,
                        key=key,
                        in_b50=key in current_keys,
                        constant=constant,
                        current_achievement=current_achievement,
                        target_achievement=target_achievement,
                        current_rating=current_rating,
                        target_rating=target_rating,
                    )
                )
            if candidate_options:
                options[(category, key)] = candidate_options

    return states, options


def build_push_plan(
    bests: Mapping[str, object],
    *,
    target_rating: int | None = None,
    level_filter: str | None = None,
    max_items: int = 10,
) -> PushPlan:
    """Select milestones by marginal gain, updating each B35/B15 cutoff in turn."""
    states, options = _build_options(bests, level_filter)
    limits = {"standard": 35, "dx": 15}
    current_total = sum(sum(category.values()) for category in states.values())
    selected: set[CandidateKey] = set()
    recommendations: list[PushRecommendation] = []
    projected_total = current_total

    while len(recommendations) < max_items:
        best_choice: tuple[
            tuple[float, int, float, int],
            CandidateKey,
            _CandidateOption,
            dict[ScoreKey, int],
            int,
        ] | None = None

        for candidate_key, candidate_options in options.items():
            if candidate_key in selected:
                continue
            category, _ = candidate_key
            state = states.get(category, {})
            for option in candidate_options:
                updated_state, gain = _apply_option(
                    state,
                    option,
                    limits[category],
                )
                if gain <= 0:
                    continue
                efficiency = gain / max(option.effort, 0.05)
                priority = (
                    efficiency,
                    gain,
                    -option.effort,
                    option.target_rating,
                )
                if best_choice is None or priority > best_choice[0]:
                    best_choice = (
                        priority,
                        candidate_key,
                        option,
                        updated_state,
                        gain,
                    )

        if best_choice is None:
            break

        _, candidate_key, option, updated_state, gain = best_choice
        states[option.category] = updated_state
        selected.add(candidate_key)
        projected_total += gain
        recommendations.append(
            PushRecommendation(
                entry=option.entry,
                category=option.category,
                in_b50=option.in_b50,
                constant=option.constant,
                current_achievement=option.current_achievement,
                target_achievement=option.target_achievement,
                current_rating=option.current_rating,
                target_rating=option.target_rating,
                gain=gain,
            )
        )

        if target_rating is not None and projected_total >= target_rating:
            break

    return PushPlan(
        recommendations=tuple(recommendations),
        current_total=current_total,
        projected_total=projected_total,
        target_rating=target_rating,
        level_filter=level_filter,
    )
