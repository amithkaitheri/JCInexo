"""Attendance Engine (pure).

Component 3 of design.md ("Attendance Engine"). These functions derive the
attendance-related metrics the dashboard and health score depend on:

- :func:`attended_count` — how many events a member has attended (Req 3.3).
- :func:`milestone_progress` — which milestones are achieved, the next unmet
  one, and whether all are achieved (Req 3.3, 3.4).
- :func:`is_duplicate` — whether a proposed ``(member_id, event_date)`` already
  exists, supporting the duplicate-rejection rule (Req 3.2).

Every function here is pure and deterministic: it takes explicit inputs and
returns a value with no I/O, no clock reads, and no mutation of its arguments.
Duplicate *rejection* and future-date *rejection* themselves live in the
Validation component and the Repository (which relies on a DB unique
constraint); :func:`is_duplicate` is the pure predicate those layers consult.
"""

from __future__ import annotations

from datetime import date
from typing import List

from .types import AttendanceRecord, MilestoneProgress


def attended_count(records: List[AttendanceRecord]) -> int:
    """Return the number of attended events for a member (Req 3.3).

    The count is simply the number of :class:`AttendanceRecord` entries supplied
    and is therefore always a non-negative integer. Callers are expected to pass
    the records belonging to a single member; this function does not filter by
    ``member_id`` and counts exactly what it is given.
    """

    return len(records)


def milestone_progress(count: int, milestones: List[int]) -> MilestoneProgress:
    """Compute milestone progress for an attended-event ``count`` (Req 3.3, 3.4).

    A milestone ``m`` is *achieved* iff ``count >= m`` (Req 3.4). The returned
    :class:`MilestoneProgress` carries:

    - ``achieved``: the milestones met by ``count``, in ascending order.
    - ``next_unmet``: the smallest milestone strictly greater than ``count``
      (i.e. the nearest unmet target), or ``None`` when none remain.
    - ``all_achieved``: ``True`` iff every milestone is achieved (equivalently,
      ``next_unmet is None``).

    Duplicate and unordered ``milestones`` inputs are tolerated: the milestone
    set is de-duplicated and sorted so the result is stable regardless of input
    ordering. An empty ``milestones`` list yields no achieved milestones, no
    next target, and ``all_achieved = True`` (there is nothing left to meet).
    """

    ordered = sorted(set(milestones))

    achieved = [m for m in ordered if count >= m]
    unmet = [m for m in ordered if count < m]

    next_unmet = unmet[0] if unmet else None
    all_achieved = next_unmet is None

    return MilestoneProgress(
        achieved=achieved,
        next_unmet=next_unmet,
        all_achieved=all_achieved,
    )


def is_duplicate(
    records: List[AttendanceRecord],
    member_id: int,
    event_date: date,
) -> bool:
    """Return whether ``(member_id, event_date)`` already exists in ``records``.

    Attendance uniqueness is on the ``(member_id, event_date)`` pair (Req 3.2).
    This pure predicate reports whether a proposed attendance entry would
    duplicate an existing one; the Validation component and Repository use it to
    reject duplicates while leaving the existing records unchanged.
    """

    return any(
        r.member_id == member_id and r.event_date == event_date for r in records
    )


__all__ = ["attended_count", "milestone_progress", "is_duplicate"]
