"""Health Score Engine (pure).

Implements the deterministic Health_Score computation described in design.md
("Health Score Computation Approach"). The Health_Score is an integer in
``[0, 100]`` where higher means lower dropout risk (Req 4.1). It is a weighted,
bounded aggregate of three signals — each normalized to ``[0, 1]`` — derived
purely from stored member and attendance data:

1. **Attendance depth** — attended-event count relative to the highest milestone
   target, capped at 1.0. More attendance ⇒ higher score.
2. **Attendance recency** — how recently the member last attended, relative to a
   configured recency window, decaying linearly to 0. More recent ⇒ higher score.
3. **Stage progression** — an ordinal signal from ``Membership_Stage``:
   ``Inactive`` lowest, then ``Prospective``, ``Candidate``, ``Inducted`` highest.

Aggregation (design.md)::

    raw = attendance_weight * attendance_depth
        + recency_weight    * attendance_recency
        + stage_weight      * stage_progression
    score = clamp(round(raw * 100), 0, 100)

Weights come from :class:`ScoringConfig`, are non-negative and sum to 1, which
guarantees ``raw ∈ [0, 1]`` and therefore ``score ∈ [0, 100]`` before rounding;
the explicit ``clamp`` is a defensive invariant that keeps the output in range
even under configuration edge cases (Req 4.1).

If a required input is missing/incomplete such that a score cannot be computed,
the engine returns :class:`StaleRetained` — the previous score is retained, a
stale indicator is surfaced, and the reason is recorded (Req 4.3).

This module is pure: it performs no I/O and never reads the wall clock. The
``current_date`` is passed in (resolved by the I/O layer from the injected
``Clock``), which is what makes recency-dependent scoring reproducible under
property-based tests.
"""

from __future__ import annotations

from datetime import date
from typing import List

from . import activity_points as _activity_points_module
from .types import (
    AttendanceRecord,
    Computed,
    HealthResult,
    MemberView,
    MembershipStage,
    ScoringConfig,
    StaleRetained,
)


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# The recency window, in whole days, over which the attendance-recency signal
# decays linearly from 1.0 (attended today) to 0.0 (last attended >= this many
# days ago). Kept as a module constant so the signal is deterministic and the
# function stays pure; ``ScoringConfig`` carries only the aggregation weights,
# milestones, and the at-risk threshold.
RECENCY_WINDOW_DAYS: int = 90

# Ordinal ranking of the four membership stages, lowest risk-reducing signal to
# highest, per design.md: Inactive < Prospective < Candidate < Inducted.
_STAGE_ORDINAL = {
    MembershipStage.INACTIVE: 0,
    MembershipStage.PROSPECTIVE: 1,
    MembershipStage.CANDIDATE: 2,
    MembershipStage.INDUCTED: 3,
}
# Highest ordinal, used to normalize the stage signal into [0, 1].
_STAGE_MAX_ORDINAL = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(value: int, low: int, high: int) -> int:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def _attendance_depth(count: int, milestones: List[int]) -> float:
    """Normalize attendance count against the highest milestone target.

    Returns a value in ``[0, 1]``: ``count / max(milestones)`` capped at 1.0.
    When no positive milestone target exists, any attendance is treated as full
    depth (1.0) and no attendance as 0.0, keeping the signal within ``[0, 1]``.
    """
    positive = [m for m in milestones if m > 0]
    if not positive:
        return 1.0 if count > 0 else 0.0
    target = max(positive)
    depth = count / target
    if depth < 0.0:
        return 0.0
    if depth > 1.0:
        return 1.0
    return depth


def _attendance_recency(
    attendance: List[AttendanceRecord], current_date: date
) -> float:
    """Normalize how recently the member last attended into ``[0, 1]``.

    Linearly decays from 1.0 (last event on ``current_date``) to 0.0 once the
    most recent event is ``RECENCY_WINDOW_DAYS`` or more days in the past. With
    no attendance the signal is 0.0. Events dated in the future relative to
    ``current_date`` are clamped to a same-day (fully recent) contribution so
    the signal never exceeds 1.0.
    """
    if not attendance:
        return 0.0
    latest = max(record.event_date for record in attendance)
    days_since = (current_date - latest).days
    if days_since <= 0:
        return 1.0
    if days_since >= RECENCY_WINDOW_DAYS:
        return 0.0
    return 1.0 - (days_since / RECENCY_WINDOW_DAYS)


def _stage_progression(stage: MembershipStage) -> float:
    """Normalize the membership stage ordinal into ``[0, 1]``."""
    return _STAGE_ORDINAL[stage] / _STAGE_MAX_ORDINAL


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_health_score(
    member: MemberView,
    attendance: List[AttendanceRecord],
    current_date: date,
    config: ScoringConfig,
) -> HealthResult:
    """Compute a Member's Health_Score as a weighted aggregate of normalized signals.

    Returns :class:`Computed` with an integer ``score`` in ``[0, 100]`` (Req 4.1)
    on success. When a required input is missing or incomplete such that a score
    cannot be computed, returns :class:`StaleRetained` carrying the member's
    previous score and a non-empty reason (Req 4.3).

    Purity: no I/O; ``current_date`` is supplied by the caller (Req 4.6 uses the
    configured threshold only in :func:`classify_at_risk`).
    """
    # --- Incomplete-input guard (Req 4.3) ---------------------------------
    # A score cannot be computed when the stage is missing/unknown or when the
    # scoring configuration is malformed (missing weights, negative weights, or
    # weights that do not sum to 1). In those cases retain the previous score,
    # mark it stale, and record why the recomputation did not complete.
    previous = member.health_score if member.health_score is not None else 0

    reason = _incompleteness_reason(member, config)
    if reason is not None:
        return StaleRetained(previous=previous, reason=reason)

    # --- Signals, each normalized to [0, 1] -------------------------------
    count = len(attendance)
    depth = _attendance_depth(count, config.milestones)
    recency = _attendance_recency(attendance, current_date)
    stage = _stage_progression(member.stage)
    activity = _activity_points_module.activity_signal(member.activity_points)

    # --- Weighted aggregate, then explicit clamp (defensive) --------------
    raw = (
        config.attendance_weight * depth
        + config.recency_weight * recency
        + config.stage_weight * stage
        + config.activity_weight * activity
    )
    score = _clamp(round(raw * 100), 0, 100)
    return Computed(score=score)


def _incompleteness_reason(member: MemberView, config: ScoringConfig):
    """Return a non-empty reason string when inputs are incomplete, else ``None``.

    Encapsulates the Req 4.3 "missing or incomplete such that a score cannot be
    computed" decision so :func:`compute_health_score` stays readable.
    """
    if member.stage is None:
        return "member stage is missing"
    if member.stage not in _STAGE_ORDINAL:
        return f"unrecognized membership stage: {member.stage!r}"

    weights = (
        config.attendance_weight,
        config.recency_weight,
        config.stage_weight,
        config.activity_weight,
    )
    if any(w is None for w in weights):
        return "scoring configuration is missing one or more weights"
    if any(w < 0 for w in weights):
        return "scoring configuration has a negative weight"
    # Weights must sum to 1 (design.md); allow a small floating-point tolerance.
    if abs(sum(weights) - 1.0) > 1e-9:
        return "scoring configuration weights do not sum to 1"
    return None


def classify_at_risk(score: int, threshold: int) -> bool:
    """Classify a Member as At_Risk iff ``score <= threshold`` (Req 4.4, 4.6)."""
    return score <= threshold


__all__ = [
    "RECENCY_WINDOW_DAYS",
    "compute_health_score",
    "classify_at_risk",
]
