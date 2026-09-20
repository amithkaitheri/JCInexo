"""activity_points.py — the JCI engagement points catalog (pure).

Defines the point value of each recognised member activity and a normalization
target used to fold total points into the Health_Score as a fourth signal
(alongside attendance depth, recency, and stage progression).

Design: this is a pure, IO-free module. The catalogue mirrors the JCI chapter's
engagement rubric (attend an event +10, lead a project +50, hold a national
leadership position +100, attend World Congress +150, …). Each activity has a
stable ``key`` (used in the DB + API), a human ``label``, a ``category`` (for
grouping in the UI), and its ``points``.

The Health_Score's activity signal is ``clamp(total_points / POINTS_TARGET, 0,
1)`` — i.e. a member who accumulates :data:`POINTS_TARGET` points or more maxes
out the activity contribution. The target is deliberately reachable within a
season of solid engagement so the signal is meaningful without being trivial.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ActivityType:
    """A recognised engagement activity worth a fixed number of points."""

    key: str
    label: str
    category: str
    points: int


# The points at which the activity signal saturates to 1.0 in the health score.
# ~300 points ≈ a very engaged member (e.g. lead a project + hold a board seat +
# attend a couple of conferences), so the contribution is earned, not free.
POINTS_TARGET: int = 300


# The core engagement catalogue (the primary rubric the president cares about).
# Additional social/recruitment/project/international rubrics can be appended
# later; keys must stay unique and stable.
_CATALOG: List[ActivityType] = [
    # -- Events & meetings -------------------------------------------------
    ActivityType("attend_local_event", "Attend a local JCI event", "Events", 10),
    ActivityType("volunteer_event", "Volunteer at a JCI event", "Events", 20),
    ActivityType("help_plan_event", "Help plan/organize an event", "Events", 30),
    ActivityType("lead_event", "Lead an event/project", "Events", 50),
    ActivityType("attend_general_assembly", "Attend a General Assembly / Meeting", "Events", 10),
    ActivityType("mc_host_event", "MC/Host a JCI event", "Events", 30),
    ActivityType("speaker_panelist", "Participate as a speaker/panelist", "Events", 30),
    # -- Training & certification -----------------------------------------
    ActivityType("attend_local_training", "Attend a local training", "Training", 15),
    ActivityType("attend_intl_training", "Attend an international/virtual training", "Training", 20),
    ActivityType("complete_certification", "Complete a JCI training/certification", "Training", 30),
    ActivityType("attend_lots", "Attend LOTS training", "Training", 25),
    # -- Committees & leadership ------------------------------------------
    ActivityType("join_local_committee", "Join a local committee", "Leadership", 25),
    ActivityType("join_national_committee", "Join a national committee", "Leadership", 50),
    ActivityType("local_board_position", "Hold a local executive/board position", "Leadership", 75),
    ActivityType("national_leadership", "Hold a national leadership position", "Leadership", 100),
    # -- Conferences & conventions ----------------------------------------
    ActivityType("attend_regional_conf", "Attend a Regional Conference", "Conferences", 50),
    ActivityType("attend_area_conf", "Attend an Area Conference", "Conferences", 75),
    ActivityType("attend_national_convention", "Attend a National Convention", "Conferences", 100),
    ActivityType("attend_world_congress", "Attend World Congress", "Conferences", 150),
]

# Fast lookup by key.
_BY_KEY: Dict[str, ActivityType] = {a.key: a for a in _CATALOG}


def catalog() -> List[ActivityType]:
    """Return the full activity catalogue (stable order)."""
    return list(_CATALOG)


def get_activity(key: str) -> ActivityType:
    """Return the :class:`ActivityType` for ``key`` or raise ``KeyError``."""
    return _BY_KEY[key]


def is_valid_key(key: str) -> bool:
    """Whether ``key`` names a recognised activity."""
    return key in _BY_KEY


def points_for(key: str) -> int:
    """Points awarded for activity ``key`` (0 if unknown)."""
    a = _BY_KEY.get(key)
    return a.points if a is not None else 0


def activity_signal(total_points: int) -> float:
    """Normalize accumulated points into ``[0, 1]`` for the health score.

    Linearly scales ``total_points`` against :data:`POINTS_TARGET`, capped at
    1.0. Negative totals (should not occur) clamp to 0.0.
    """
    if total_points <= 0:
        return 0.0
    signal = total_points / POINTS_TARGET
    return 1.0 if signal > 1.0 else signal


# ---------------------------------------------------------------------------
# Engagement tiers (points-based Wellington's Trail)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngagementTier:
    """A tier on the points-based trail, unlocked at ``threshold`` points."""

    key: str
    label: str
    icon: str
    threshold: int


# Ordered ascending by threshold. The trail renders one node per tier; a member
# unlocks a tier once their total engagement points reach its threshold.
_TIERS: List["EngagementTier"] = [
    EngagementTier("rookie", "Rookie", "🐾", 0),
    EngagementTier("contributor", "Contributor", "🌱", 25),
    EngagementTier("active", "Active Member", "🔥", 75),
    EngagementTier("leader", "Chapter Leader", "🏆", 150),
    EngagementTier("champion", "Chapter Champion", "👑", 300),
]


def tiers() -> List["EngagementTier"]:
    """Return the ordered engagement tiers (ascending by point threshold)."""
    return list(_TIERS)


def current_tier(total_points: int) -> "EngagementTier":
    """Return the highest tier whose threshold the member's points have reached."""
    reached = [t for t in _TIERS if total_points >= t.threshold]
    return reached[-1] if reached else _TIERS[0]


def next_tier(total_points: int) -> Optional["EngagementTier"]:
    """Return the next unreached tier, or ``None`` when at the top tier."""
    for t in _TIERS:
        if total_points < t.threshold:
            return t
    return None


__all__ = [
    "ActivityType",
    "POINTS_TARGET",
    "catalog",
    "get_activity",
    "is_valid_key",
    "points_for",
    "activity_signal",
    "EngagementTier",
    "tiers",
    "current_tier",
    "next_tier",
]
