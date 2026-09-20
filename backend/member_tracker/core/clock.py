"""Injected Clock provider.

Design decision (see design.md, "Layering and Rationale"): the current date is
*never* read from the wall clock inside domain logic; it is passed in via a
``Clock`` provider. This is what makes date-dependent properties (age-out
windows, future-dated attendance) reproducible under property-based tests — a
test simply injects a :class:`FixedClock` at a chosen instant.

No function in ``member_tracker.core`` should ever call ``date.today()`` or
``datetime.now()`` directly; it should accept a ``current_date`` argument (which
the I/O layer obtains from the injected ``Clock``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Provides the notion of "now" to the I/O layer.

    Domain-core functions receive the resolved ``current_date`` explicitly; the
    ``Clock`` is what the I/O layer uses to resolve it. Implementations must be
    consistent within a single logical operation (``current_date()`` should be
    the date component of ``current_time()``).
    """

    def current_date(self) -> date:
        """Return the current calendar date."""
        ...

    def current_time(self) -> datetime:
        """Return the current timestamp. Should be timezone-aware (UTC)."""
        ...


class SystemClock:
    """Real clock backed by the wall clock, used in production (I/O layer only)."""

    def current_date(self) -> date:
        return datetime.now(timezone.utc).date()

    def current_time(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FixedClock:
    """Deterministic clock pinned to a fixed instant, for tests.

    ``current_date()`` is derived from ``moment`` so the date and time stay
    consistent.
    """

    moment: datetime

    def current_date(self) -> date:
        return self.moment.date()

    def current_time(self) -> datetime:
        return self.moment


__all__ = ["Clock", "SystemClock", "FixedClock"]
