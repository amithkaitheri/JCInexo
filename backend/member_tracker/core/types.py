"""Shared value objects and enums for the pure domain core.

These types are the vocabulary the rest of the domain core is written in. They
are deliberately small, immutable dataclasses / enums with no behavior and no
I/O, so they can be freely constructed by property-based tests. Field shapes
follow design.md ("Components and Interfaces", "Enumerations and Value Objects"
and "Field Notes").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Generic, List, Optional, TypeVar, Union


# ---------------------------------------------------------------------------
# Membership stage (Req 1.5, 2.5)
# ---------------------------------------------------------------------------


class MembershipStage(str, Enum):
    """The current phase of a Member's journey.

    Restricted to exactly these four values at the domain layer (Req 1.5); a DB
    ``CHECK`` constraint mirrors this in the persistence layer.
    """

    PROSPECTIVE = "Prospective"
    CANDIDATE = "Candidate"
    INDUCTED = "Inducted"
    INACTIVE = "Inactive"

    @classmethod
    def allowed_values(cls) -> List[str]:
        """The four allowed stage strings, in journey order."""
        return [s.value for s in cls]


# ---------------------------------------------------------------------------
# Result / error plumbing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationError:
    """A structured validation failure with a machine-readable ``code`` and a
    human-readable ``message`` (design.md, Validation Component)."""

    code: str
    message: str


T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True)
class Ok(Generic[T]):
    """Success arm of a :data:`Result`, carrying a value."""

    value: T

    @property
    def is_ok(self) -> bool:
        return True

    @property
    def is_err(self) -> bool:
        return False


@dataclass(frozen=True)
class Err(Generic[E]):
    """Failure arm of a :data:`Result`, carrying an error."""

    error: E

    @property
    def is_ok(self) -> bool:
        return False

    @property
    def is_err(self) -> bool:
        return True


# A Result is either Ok{value} or Err{error}. Pure validators/computations
# return one of these rather than raising, so callers must handle both arms.
Result = Union[Ok[T], Err[E]]


def ok(value: T) -> Ok[T]:
    """Construct a success result."""
    return Ok(value)


def err(error: E) -> Err[E]:
    """Construct a failure result."""
    return Err(error)


# ---------------------------------------------------------------------------
# Age-out status (Req 2.3, 2.4, 2.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NotApplicable:
    """No Age_Out_Date is set for the member (Req 2.5)."""


@dataclass(frozen=True)
class AlertActive:
    """Current date is within 30 days before the Age_Out_Date, inclusive of both
    the 30th day before and the date itself (Req 2.3)."""

    days_remaining: int


@dataclass(frozen=True)
class AgedOut:
    """The Age_Out_Date is earlier than the current date (Req 2.4)."""


@dataclass(frozen=True)
class Normal:
    """An Age_Out_Date is set but is more than 30 days away."""


# Exactly one of these holds for a given (age_out_date, current_date) pair.
AgeOutStatus = Union[NotApplicable, AlertActive, AgedOut, Normal]


# ---------------------------------------------------------------------------
# Attendance (Req 3.x)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttendanceRecord:
    """A dated entry recording that a Member attended a specific event.

    Uniqueness is on ``(member_id, event_date)`` (Req 3.2).
    """

    member_id: int
    event_date: date


@dataclass(frozen=True)
class MilestoneProgress:
    """Progress toward the defined attendance milestones (Req 3.3, 3.4).

    A milestone ``m`` is achieved iff ``count >= m``. ``next_unmet`` is the
    smallest unmet milestone, or ``None`` when ``all_achieved`` is ``True``.
    """

    achieved: List[int]
    next_unmet: Optional[int]
    all_achieved: bool


# ---------------------------------------------------------------------------
# Health score (Req 4.x)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Computed:
    """A freshly computed Health_Score, an integer in ``[0, 100]`` (Req 4.1)."""

    score: int


@dataclass(frozen=True)
class StaleRetained:
    """Inputs were missing/incomplete, so the previous score is retained and a
    reason is recorded (Req 4.3)."""

    previous: int
    reason: str


# A HealthResult is either a freshly Computed score or a StaleRetained one.
HealthResult = Union[Computed, StaleRetained]


@dataclass(frozen=True)
class ScoringConfig:
    """Configuration for the pure health-score computation and at-risk gate.

    Weights are non-negative and sum to 1 so ``raw`` stays within ``[0, 1]``
    (design.md, "Health Score Computation Approach"). ``milestones`` are the
    attendance thresholds; ``at_risk_threshold`` is the integer in ``[0, 100]``
    at or below which a member is classified At_Risk (Req 4.6).
    """

    attendance_weight: float
    recency_weight: float
    stage_weight: float
    at_risk_threshold: int
    milestones: List[int]
    # Weight of the engagement-points signal (see core/activity_points.py). The
    # four weights (attendance, recency, stage, activity) sum to 1. Defaulted to
    # 0.0 so any ScoringConfig constructed positionally without it stays valid
    # (backward compatible with the original three-weight model).
    activity_weight: float = 0.0


# ---------------------------------------------------------------------------
# Member view (input to pure computations)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberView:
    """A read-only projection of a stored Member used by pure computations.

    ``age_out_date`` is ``None`` when unset (⇒ "not applicable", Req 2.5).
    ``health_score`` is the last persisted score, used as the ``previous`` value
    when a recomputation cannot complete (Req 4.3).
    """

    member_id: int
    name: str
    stage: MembershipStage
    age_out_date: Optional[date] = None
    health_score: Optional[int] = None
    # Total engagement points accumulated by the member (0 when none). Feeds the
    # activity signal in the health score.
    activity_points: int = 0


# ---------------------------------------------------------------------------
# Dashboard (Req 6.x)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DashboardRow:
    """One row per member on the dashboard (Req 6.1)."""

    member_id: int
    name: str
    stage: MembershipStage
    age_out_status: AgeOutStatus
    attendance_progress: MilestoneProgress
    health_score: Optional[int]
    at_risk: bool
    score_stale: bool


@dataclass(frozen=True)
class DashboardView:
    """A single page of the assembled dashboard.

    At-risk rows are grouped into one contiguous section (Req 4.5). ``no_members``
    signals the empty datastore (Req 6.3); ``no_match`` signals a filter that
    matched zero rows (Req 6.5). Pages hold at most 50 rows (Req 6.2).
    """

    rows: List[DashboardRow] = field(default_factory=list)
    page: int = 1
    page_size: int = 50
    total_members: int = 0
    total_pages: int = 1
    filter_stage: Optional[MembershipStage] = None
    no_members: bool = False
    no_match: bool = False


# ---------------------------------------------------------------------------
# AI-powered member retention — "Wellington the Wise" (Req 8.x)
# ---------------------------------------------------------------------------
#
# Component 9 of design.md ("Wellington Retention Agent, Web Search Tool, and
# Recommendation Verifier"). These value objects are the vocabulary shared by
# the pure ``verify_recommendation`` / ``build_templated_recommendation`` core
# (core/recommendation.py) and the I/O adapters that surround it (the Web Search
# Tool and the Wellington Retention Agent). They are small, immutable
# dataclasses with no behavior and no I/O so property-based tests can freely
# construct them.
#
# Trust boundary: ``verify_recommendation`` is the anti-fabrication guarantee.
# It receives the ``Web_Search_Result`` set explicitly and can only bless a
# :class:`Recommended_Event` whose ``(title, event_date, url)`` matches one of
# those returned results — it constructs no events (Req 8.6).


@dataclass(frozen=True)
class Web_Search_Result:
    """A single event hit returned by the Web Search Tool adapter (Req 8.3).

    This is the *only* source of real event facts: the verifier will bless a
    recommended event only if it matches one of these on ``(title, event_date,
    url)``. ``snippet`` is optional supporting text and is never used as an
    identity key.
    """

    title: str
    event_date: date
    url: str
    snippet: Optional[str] = None


@dataclass(frozen=True)
class Recommended_Event:
    """An event Wellington proposes to a member, with a fit ``reason`` (Req 8.5).

    Identity for verification is the ``(title, event_date, url)`` triple, which
    must match some :class:`Web_Search_Result` (Req 8.6). ``reason`` is
    synthesized prose explaining why the event suits the member and carries no
    event-fact fabrication risk.
    """

    title: str
    event_date: date
    url: str
    reason: str


@dataclass(frozen=True)
class Retention_Recommendation:
    """A verified retention recommendation with exactly three parts (Req 8.5).

    * ``diagnosis`` — non-empty prose explaining why the member is at risk.
    * ``events`` — 1..3 verified :class:`Recommended_Event` entries, each bound
      to a distinct real in-window :class:`Web_Search_Result`.
    * ``outreach_template`` — non-empty draft outreach message.

    ``mode`` records how the recommendation was produced: ``"llm"`` for the LLM
    synthesis path or ``"template"`` for the deterministic fallback assembler
    (Req 8.11). It defaults to ``"llm"`` so the synthesis path and persistence
    layer need no change when the template fallback (task 16.4) is added.
    """

    diagnosis: str
    events: List[Recommended_Event]
    outreach_template: str
    mode: str = "llm"


@dataclass(frozen=True)
class MemberContext:
    """Safe, stored member context handed to Wellington (Req 8.2, 8.3, 8.11).

    Used by the agent to drive the search query and by the template fallback
    assembler to parameterize its fixed diagnosis/outreach text. ``interests``
    and ``location`` steer the event search; ``at_risk`` gates eligibility — a
    non-at-risk member is rejected before any search (Req 8.2).
    """

    member_id: int
    name: str
    stage: MembershipStage
    interests: List[str]
    health_score: int
    at_risk: bool
    location: Optional[str] = None


@dataclass(frozen=True)
class VerificationError:
    """A structured failure from :func:`verify_recommendation`.

    Mirrors :class:`ValidationError` with a machine-readable ``code`` and a
    human-readable ``message`` (design.md Component 9). Raised when no valid
    event survives pruning or a required part is missing/empty (Req 8.5, 8.6).
    """

    code: str
    message: str


# ---------------------------------------------------------------------------
# Retention outcome (Req 8.4, 8.7, 8.8) — the single sum-typed result the
# Wellington Retention Agent returns. Exactly one variant holds per request.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Recommendation:
    """Outcome: search returned >=1 event and synthesis+verification succeeded
    (Req 8.4, 8.5). Carries the verified :class:`Retention_Recommendation`."""

    recommendation: Retention_Recommendation


@dataclass(frozen=True)
class NoEvents:
    """Outcome: the search returned zero results (Req 8.7). Carries no
    recommendation and no events."""


@dataclass(frozen=True)
class Error:
    """Outcome: eligibility failure, search/LLM failure, or deadline breach
    (Req 8.8). Carries no partial recommendation and no events.

    ``reason`` is a machine-readable tag (e.g. ``"not_eligible"``); ``message``
    is human-readable.
    """

    message: str
    reason: str


# A RetentionOutcome is exactly one of these three mutually exclusive variants
# (Req 8.4). NoEvents and Error carry zero events and no Retention_Recommendation.
RetentionOutcome = Union[Recommendation, NoEvents, Error]


__all__ = [
    "MembershipStage",
    "ValidationError",
    "Ok",
    "Err",
    "Result",
    "ok",
    "err",
    "NotApplicable",
    "AlertActive",
    "AgedOut",
    "Normal",
    "AgeOutStatus",
    "AttendanceRecord",
    "MilestoneProgress",
    "Computed",
    "StaleRetained",
    "HealthResult",
    "ScoringConfig",
    "MemberView",
    "DashboardRow",
    "DashboardView",
    # Retention (Req 8.x) — "Wellington the Wise"
    "Web_Search_Result",
    "Recommended_Event",
    "Retention_Recommendation",
    "MemberContext",
    "VerificationError",
    "Recommendation",
    "NoEvents",
    "Error",
    "RetentionOutcome",
]
