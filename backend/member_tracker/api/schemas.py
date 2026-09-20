"""schemas.py — Pydantic v2 request/response models for the HTTP layer.

These models are the *wire contract* of the Member_Tracker REST API. They are
deliberately thin: request models capture only what the HTTP client sends and
apply lightweight shape/format checks (types, string bounds), while the
authoritative business validation still lives in the pure ``core/validation``
validators invoked by the repository (Req 1.3, 1.4, 2.2, 3.5, 4.7). Doing the
"real" validation in the pure core keeps a single source of truth and lets the
route handlers (tasks 11.2/11.3) translate the resulting structured
:class:`ValidationError` codes into HTTP status codes.

Response models mirror the repository/snapshot/dashboard projections
(``MemberRecord``, ``ConfigView``, ``DashboardView``, ``SnapshotMeta``,
``SnapshotContents``, ``ExportFile``) so the route handlers can build them from
domain values without leaking SQLite details. The ``age_out_status`` and
``attendance_progress`` shapes on the dashboard row follow the design's
"HTTP API Surface" / "Data Models" sections.

Model coverage (task 11.1):
  * create member          → :class:`CreateMemberRequest` / :class:`MemberResponse`
  * stage change           → :class:`StageChangeRequest`
  * age-out date           → :class:`AgeOutDateRequest`
  * attendance             → :class:`AttendanceRequest` / :class:`AttendanceResponse`
  * threshold config       → :class:`ThresholdConfigRequest` / :class:`ConfigResponse`
  * dashboard view         → :class:`DashboardResponse` (+ row/status sub-models)
  * snapshot/export        → :class:`SnapshotMetaResponse`, :class:`SnapshotContentsResponse`,
                             :class:`SnapshotListResponse`, :class:`ExportResponse`
  * shared error envelope  → :class:`ErrorResponse`

The individual route handlers that *use* these models are implemented in later
tasks (11.2, 11.3); this module only defines the contract.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from member_tracker.core.types import MembershipStage

# The four allowed Membership_Stage values as a typing Literal, so request
# bodies that carry a stage reject anything outside the set at the shape layer
# (Req 1.4, 1.5). The pure ``validate_stage`` remains the authoritative check.
StageLiteral = Literal["Prospective", "Candidate", "Inducted", "Inactive"]


# ---------------------------------------------------------------------------
# Shared error envelope
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Uniform error body carrying a domain :class:`ValidationError`'s fields.

    Route handlers map a structured ``ValidationError`` (``code`` + ``message``)
    from the pure core / repository onto this envelope alongside the chosen HTTP
    status (e.g. 422 validation, 409 duplicate, 503 unavailable).
    """

    code: str = Field(..., description="Machine-readable error code.")
    message: str = Field(..., description="Human-readable error message.")


# ---------------------------------------------------------------------------
# Member creation & mutation (Req 1.1–1.5, 2.1, 2.2)
# ---------------------------------------------------------------------------


class CreateMemberRequest(BaseModel):
    """Body for ``POST /api/members`` (Req 1.1, 1.3, 1.4).

    ``name`` is bounded 1..100 at the shape layer to mirror the domain rule; the
    pure ``validate_name`` still enforces the non-whitespace requirement. ``stage``
    must be one of the four allowed values. ``age_out_date`` is optional
    (``None`` ⇒ "not applicable", Req 2.5) and accepted as an ISO ``YYYY-MM-DD``
    string, validated for well-formedness/non-past-ness by the core.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    stage: StageLiteral
    age_out_date: Optional[str] = Field(
        default=None,
        description="ISO YYYY-MM-DD; omit or null for 'not applicable'.",
    )
    mentor_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Optional mentor / sponsor name for the member.",
    )
    area_of_interest: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional area(s) of interest for the member.",
    )


class StageChangeRequest(BaseModel):
    """Body for ``PATCH /api/members/{id}/stage`` (Req 1.2, 1.4)."""

    model_config = ConfigDict(extra="forbid")

    stage: StageLiteral


class AgeOutDateRequest(BaseModel):
    """Body for ``PUT /api/members/{id}/age-out-date`` (Req 2.1, 2.2).

    ``age_out_date`` is an ISO ``YYYY-MM-DD`` string; the core validates it is a
    well-formed calendar date on or after the current date.
    """

    model_config = ConfigDict(extra="forbid")

    age_out_date: str = Field(..., description="ISO YYYY-MM-DD.")


class MemberResponse(BaseModel):
    """A stored Member, mirroring the repository's ``MemberRecord`` projection."""

    id: str
    name: str
    stage: StageLiteral
    age_out_date: Optional[str] = Field(
        default=None, description="ISO YYYY-MM-DD, or null when unset (Req 2.5)."
    )
    stage_changed_at: Optional[str] = Field(
        default=None, description="UTC ISO 8601 of last stage change (Req 1.2)."
    )
    created_at: str
    mentor_name: Optional[str] = Field(
        default=None, description="Optional mentor / sponsor name."
    )
    area_of_interest: Optional[str] = Field(
        default=None, description="Optional area(s) of interest."
    )


# ---------------------------------------------------------------------------
# Attendance (Req 3.1, 3.2, 3.3, 3.4, 3.5)
# ---------------------------------------------------------------------------


class AttendanceRequest(BaseModel):
    """Body for ``POST /api/members/{id}/attendance`` (Req 3.1, 3.5).

    ``event_date`` is an ISO ``YYYY-MM-DD`` string; the core validates it is
    well-formed and not future-dated.
    """

    model_config = ConfigDict(extra="forbid")

    event_date: str = Field(..., description="ISO YYYY-MM-DD; must not be future.")


class MilestoneProgressResponse(BaseModel):
    """Attendance progress toward the configured milestones (Req 3.3, 3.4).

    Mirrors the pure ``MilestoneProgress`` value object.
    """

    achieved: List[int] = Field(default_factory=list)
    next_unmet: Optional[int] = None
    all_achieved: bool = False


class AttendanceResponse(BaseModel):
    """Result of recording attendance: updated count + milestone progress.

    Route handlers (task 11.2) recompute and persist the health score after a
    successful record and may surface the resulting score here.
    """

    member_id: str
    attended_count: int
    progress: MilestoneProgressResponse
    health_score: Optional[int] = None


# ---------------------------------------------------------------------------
# Threshold configuration (Req 4.6, 4.7)
# ---------------------------------------------------------------------------


class ThresholdConfigRequest(BaseModel):
    """Body for ``PUT /api/config/at-risk-threshold`` (Req 4.6, 4.7).

    ``at_risk_threshold`` must be an integer in ``[0, 100]``; the shape layer
    bounds it and the pure ``validate_threshold`` is the authoritative check
    (rejecting non-integers / out-of-range and retaining the prior value).
    """

    model_config = ConfigDict(extra="forbid")

    at_risk_threshold: int = Field(..., ge=0, le=100)


class ConfigResponse(BaseModel):
    """Current configuration, mirroring the repository's ``ConfigView``."""

    at_risk_threshold: int
    milestones: List[int] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dashboard view (Req 4.5, 6.1–6.5)
# ---------------------------------------------------------------------------


class AgeOutStatusResponse(BaseModel):
    """Serialized age-out status for a dashboard row (Req 2.3, 2.4, 2.5).

    ``kind`` names the variant (``not_applicable`` | ``alert_active`` |
    ``aged_out`` | ``normal``); ``days_remaining`` is populated only for
    ``alert_active`` (the whole-day count until the age-out date, Req 2.3).
    """

    kind: Literal["not_applicable", "alert_active", "aged_out", "normal"]
    days_remaining: Optional[int] = None


class DashboardRowResponse(BaseModel):
    """One dashboard row per member (Req 6.1), mirroring ``DashboardRow``.

    ``intervention_eligible`` surfaces whether a retention intervention can be
    generated for this member (Req 8.1). It is derived directly from the at-risk
    classification — an At_Risk_Member is intervention-eligible — so the UI can
    show the intervention affordance on exactly the at-risk rows.
    """

    member_id: str
    name: str
    stage: StageLiteral
    age_out_status: AgeOutStatusResponse
    attendance_progress: MilestoneProgressResponse
    health_score: Optional[int] = None
    at_risk: bool = False
    score_stale: bool = False
    intervention_eligible: bool = False


class DashboardResponse(BaseModel):
    """A single page of the dashboard, mirroring the pure ``DashboardView``.

    At-risk rows are grouped contiguously (Req 4.5). ``no_members`` (Req 6.3) /
    ``no_match`` (Req 6.5) are the empty-state flags; pagination metadata
    (Req 6.2) accompanies the rows.
    """

    rows: List[DashboardRowResponse] = Field(default_factory=list)
    page: int = 1
    page_size: int = 50
    total_members: int = 0
    total_pages: int = 1
    filter_stage: Optional[StageLiteral] = None
    no_members: bool = False
    no_match: bool = False


# ---------------------------------------------------------------------------
# Snapshot & export (Req 5.1–5.8)
# ---------------------------------------------------------------------------


class SnapshotMetaResponse(BaseModel):
    """Metadata for one retained Handover_Snapshot (Req 5.3), mirrors ``SnapshotMeta``."""

    id: str
    created_at: str = Field(..., description="ISO 8601 with UTC offset (Req 5.3).")
    member_count: int
    checksum: str


class SnapshotListResponse(BaseModel):
    """Response for ``GET /api/handover/snapshots`` — retained snapshots, newest first."""

    snapshots: List[SnapshotMetaResponse] = Field(default_factory=list)


class SnapshotContentsResponse(BaseModel):
    """Full payload of a snapshot (Req 5.5), mirroring ``SnapshotContents``.

    ``members`` and ``attendance`` are the JSON-decoded payload lists exactly as
    captured at creation time.
    """

    id: str
    created_at: str
    member_count: int
    members: List[dict] = Field(default_factory=list)
    attendance: List[dict] = Field(default_factory=list)


class ExportResponse(BaseModel):
    """Result of ``GET /api/export`` (Req 5.7), mirroring ``ExportFile``."""

    path: str
    member_count: int
    attendance_count: int


# ---------------------------------------------------------------------------
# Gamification — "Wellington's Trail" (Req 7)
# ---------------------------------------------------------------------------


class EarnedBadgeResponse(BaseModel):
    """A digital badge a member has earned (Extended Member Record Schema 2.2)."""

    badge_id: str
    badge_name: str
    unlocked_at: str


class TrailNodeResponse(BaseModel):
    """One node on Wellington's Trail (Visual Progress Trail)."""

    badge_id: str
    label: str
    icon: str
    threshold: int
    unlocked: bool
    current: bool


class PointsTierResponse(BaseModel):
    """One node on the points-based engagement trail.

    ``threshold`` is the point total at which the tier unlocks. ``unlocked`` is
    ``True`` once the member's total engagement points reach it; ``current``
    marks the member's present tier.
    """

    key: str
    label: str
    icon: str
    threshold: int
    unlocked: bool
    current: bool


class GamificationResponse(BaseModel):
    """Response for ``GET /api/members/{id}/gamification`` (Req 7).

    Bundles everything the mascot + trail UI needs for a member: the derived
    mascot ``state`` (HAPPY | ALERT | CELEBRATING) and its contextual
    ``message``, the member's ``attended_event_count``, ``stage``, and
    ``next_milestone``, the ordered trail ``nodes``, and the ``earned_badges``.
    ``newly_unlocked`` carries badges unlocked by the most recent sync (drives
    the celebration overlay); it is empty on a plain read.

    The points-based engagement trail is carried by ``total_points`` +
    ``points_nodes`` (one node per engagement tier), ``points_tier_label`` (the
    member's current tier), and ``points_to_next`` / ``next_tier_label`` (points
    remaining to the next tier, ``None`` at the top tier).
    """

    member_id: str
    name: str
    stage: str
    mascot_state: Literal["HAPPY", "ALERT", "CELEBRATING"]
    mascot_message: str
    health_score: Optional[int] = None
    at_risk: bool = False
    attended_event_count: int = 0
    next_milestone: Optional[int] = None
    nodes: List[TrailNodeResponse] = Field(default_factory=list)
    earned_badges: List[EarnedBadgeResponse] = Field(default_factory=list)
    newly_unlocked: List[EarnedBadgeResponse] = Field(default_factory=list)
    # Points-based engagement trail.
    total_points: int = 0
    points_tier_label: str = ""
    points_nodes: List[PointsTierResponse] = Field(default_factory=list)
    points_to_next: Optional[int] = None
    next_tier_label: Optional[str] = None


__all__ = [
    "StageLiteral",
    "ErrorResponse",
    "CreateMemberRequest",
    "StageChangeRequest",
    "AgeOutDateRequest",
    "MemberResponse",
    "AttendanceRequest",
    "MilestoneProgressResponse",
    "AttendanceResponse",
    "ThresholdConfigRequest",
    "ConfigResponse",
    "AgeOutStatusResponse",
    "DashboardRowResponse",
    "DashboardResponse",
    "SnapshotMetaResponse",
    "SnapshotListResponse",
    "SnapshotContentsResponse",
    "ExportResponse",
    "EarnedBadgeResponse",
    "TrailNodeResponse",
    "PointsTierResponse",
    "GamificationResponse",
]
