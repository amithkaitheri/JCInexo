"""Age-Out Engine (pure).

Implements the age-out status computation described in design.md
("Components and Interfaces", Component 2 — Age-Out Engine). The function is
pure and deterministic: the "current date" is passed in explicitly and is never
read from the wall clock, which is what makes the date-dependent behavior
reproducible under property-based testing.

Requirements: 2.3, 2.4, 2.5.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from .types import (
    AgedOut,
    AgeOutStatus,
    AlertActive,
    NotApplicable,
    Normal,
)

# The inclusive pre-out alert window, measured in whole calendar days before the
# Age_Out_Date (Req 2.3): the alert is active from the 30th day before through
# the Age_Out_Date itself.
ALERT_WINDOW_DAYS = 30


def age_out_status(
    age_out_date: Optional[date], current_date: date
) -> AgeOutStatus:
    """Classify a Member's age-out status for a given current date.

    The four outcomes are mutually exclusive — exactly one holds for any
    ``(age_out_date, current_date)`` pair:

    * ``None`` age-out date        -> :class:`NotApplicable`            (Req 2.5)
    * ``current_date > age_out_date`` -> :class:`AgedOut` (no pre-out alert) (Req 2.4)
    * ``0 <= (age_out_date - current_date) <= 30`` ->
      :class:`AlertActive` with ``days_remaining`` set to the whole-number
      calendar-day difference, inclusive of both boundaries (Req 2.3)
    * otherwise (more than 30 days away) -> :class:`Normal`

    Args:
        age_out_date: The Member's Age_Out_Date, or ``None`` when unset.
        current_date: The current date, injected by the caller (never read from
            the wall clock inside this pure function).

    Returns:
        The single :data:`AgeOutStatus` variant that applies.
    """
    # Req 2.5: no Age_Out_Date set -> status is "not applicable".
    if age_out_date is None:
        return NotApplicable()

    # Whole-number calendar-day difference. Positive when the age-out date is in
    # the future, zero on the date itself, negative once it has passed.
    days_remaining = (age_out_date - current_date).days

    # Req 2.4: the date is already in the past -> aged out, and no pre-out alert.
    if days_remaining < 0:
        return AgedOut()

    # Req 2.3: within the inclusive [0, 30]-day window before the age-out date ->
    # an active alert reporting the whole number of days remaining.
    if days_remaining <= ALERT_WINDOW_DAYS:
        return AlertActive(days_remaining=days_remaining)

    # More than 30 days away -> nothing to flag yet.
    return Normal()


__all__ = ["age_out_status", "ALERT_WINDOW_DAYS"]
