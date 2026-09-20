"""Validation component (pure).

Responsible for all input validation, returning either a normalized value
(:class:`~member_tracker.core.types.Ok`) or a structured
:class:`~member_tracker.core.types.ValidationError`
(:class:`~member_tracker.core.types.Err`) carrying a machine-readable ``code``
and a human-readable ``message``.

Every function here is pure: it takes explicit inputs (including the resolved
``current_date`` for date validators, following the Clock convention in
``core/clock.py`` — the wall clock is *never* read here) and returns a value
with no side effects. Signatures follow design.md, "Components and Interfaces",
Section 1 (Validation Component).

    validate_name(name)                          -> Result[str, ValidationError]
    validate_stage(value)                        -> Result[MembershipStage, ValidationError]
    validate_age_out_date(date, current_date)    -> Result[date, ValidationError]
    validate_threshold(value)                    -> Result[int, ValidationError]
    validate_event_date(date, current_date)      -> Result[date, ValidationError]
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Union

from .types import (
    Err,
    MembershipStage,
    Ok,
    Result,
    ValidationError,
    err,
    ok,
)

# A date input may arrive as a native ``date`` or as an ISO-8601 (``YYYY-MM-DD``)
# string from the HTTP layer. "Well-formed calendar date" means it parses to a
# real calendar date (rejecting e.g. ``2023-02-30`` or non-date junk).
DateInput = Union[date, str]

# Name length bounds (Req 1.3): non-empty after trim, at most 100 characters.
_NAME_MIN_LEN = 1
_NAME_MAX_LEN = 100


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_calendar_date(value: DateInput) -> Union[date, None]:
    """Coerce ``value`` to a real calendar :class:`date`, or ``None`` if it is
    not a well-formed calendar date.

    Accepts a native ``date`` (but not a ``datetime``, whose time component is
    not a plain calendar date) or an ISO-8601 ``YYYY-MM-DD`` string. ``datetime``
    is treated as not well-formed for a calendar-date field to avoid silently
    discarding a time component.
    """
    # ``datetime`` is a subclass of ``date``; exclude it explicitly so a
    # timestamp is not silently accepted as a bare calendar date.
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value.strip())
        except (ValueError, TypeError):
            return None
        return parsed
    return None


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def validate_name(name: Any) -> Result[str, ValidationError]:
    """Validate a member name (Req 1.3).

    Accepts iff ``name`` is a string that is non-empty after trimming leading
    and trailing whitespace and whose trimmed length is within ``[1, 100]``.
    On success returns the trimmed name.

    Rejection code: ``name_missing_or_invalid``.
    """
    if not isinstance(name, str):
        return err(
            ValidationError(
                code="name_missing_or_invalid",
                message="Name is required and must be text.",
            )
        )

    trimmed = name.strip()
    if len(trimmed) < _NAME_MIN_LEN:
        return err(
            ValidationError(
                code="name_missing_or_invalid",
                message="Name must not be blank.",
            )
        )
    if len(trimmed) > _NAME_MAX_LEN:
        return err(
            ValidationError(
                code="name_missing_or_invalid",
                message=f"Name must be at most {_NAME_MAX_LEN} characters.",
            )
        )

    return ok(trimmed)


def validate_stage(value: Any) -> Result[MembershipStage, ValidationError]:
    """Validate a membership stage (Req 1.4, 1.5).

    Accepts iff ``value`` matches one of the four allowed stage strings
    (``Prospective``, ``Candidate``, ``Inducted``, ``Inactive``) or is already a
    :class:`MembershipStage`. On success returns the corresponding
    :class:`MembershipStage`.

    Rejection code: ``invalid_stage``.
    """
    if isinstance(value, MembershipStage):
        return ok(value)

    if isinstance(value, str):
        try:
            return ok(MembershipStage(value))
        except ValueError:
            pass

    allowed = ", ".join(MembershipStage.allowed_values())
    return err(
        ValidationError(
            code="invalid_stage",
            message=f"Stage must be one of: {allowed}.",
        )
    )


def validate_age_out_date(
    date_value: DateInput, current_date: date
) -> Result[date, ValidationError]:
    """Validate an age-out date (Req 2.1, 2.2).

    Accepts iff ``date_value`` is a well-formed calendar date on or after
    ``current_date``. On success returns the normalized :class:`date`.

    Rejection code: ``invalid_or_past_age_out_date``.
    """
    parsed = _coerce_calendar_date(date_value)
    if parsed is None:
        return err(
            ValidationError(
                code="invalid_or_past_age_out_date",
                message="Age-out date must be a valid calendar date.",
            )
        )
    if parsed < current_date:
        return err(
            ValidationError(
                code="invalid_or_past_age_out_date",
                message="Age-out date must not be in the past.",
            )
        )
    return ok(parsed)


def validate_threshold(value: Any) -> Result[int, ValidationError]:
    """Validate an at-risk threshold (Req 4.6, 4.7).

    Accepts iff ``value`` is an integer within ``[0, 100]``. Booleans are
    rejected even though ``bool`` is a subclass of ``int``. On success returns
    the :class:`int`.

    Rejection code: ``threshold_out_of_range``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return err(
            ValidationError(
                code="threshold_out_of_range",
                message="Threshold must be an integer between 0 and 100.",
            )
        )
    if value < 0 or value > 100:
        return err(
            ValidationError(
                code="threshold_out_of_range",
                message="Threshold must be between 0 and 100.",
            )
        )
    return ok(value)


def validate_event_date(
    date_value: DateInput, current_date: date
) -> Result[date, ValidationError]:
    """Validate an attendance event date (Req 3.1, 3.5).

    Accepts iff ``date_value`` is a well-formed calendar date on or before
    ``current_date`` (i.e. not future-dated). On success returns the normalized
    :class:`date`.

    Rejection code: ``future_dated_attendance``.
    """
    parsed = _coerce_calendar_date(date_value)
    if parsed is None:
        return err(
            ValidationError(
                code="future_dated_attendance",
                message="Event date must be a valid calendar date.",
            )
        )
    if parsed > current_date:
        return err(
            ValidationError(
                code="future_dated_attendance",
                message="Event date must not be in the future.",
            )
        )
    return ok(parsed)


__all__ = [
    "DateInput",
    "validate_name",
    "validate_stage",
    "validate_age_out_date",
    "validate_threshold",
    "validate_event_date",
]
