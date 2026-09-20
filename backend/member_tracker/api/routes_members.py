"""routes_members.py — member, age-out, and attendance HTTP routes (task 11.2).

This module implements the member-lifecycle slice of the Member_Tracker REST
API and wires the pure domain core + Repository into HTTP:

    POST   /api/members                     create a member            (Req 1.1, 1.3, 1.4)
    PATCH  /api/members/{id}/stage          change a member's stage    (Req 1.2, 1.4)
    PUT    /api/members/{id}/age-out-date   set a member's age-out date(Req 2.1, 2.2)
    POST   /api/members/{id}/attendance     record attendance          (Req 3.1, 3.2, 3.5)

Design realized here (design.md "HTTP layer" + the attendance request-flow
sequence): route handlers translate a request into a Repository call, then map
the returned ``Result`` (``Ok``/``Err``) to an HTTP response. The authoritative
business validation lives in the pure ``core/validation`` validators invoked by
the Repository; this layer only *translates* the structured
:class:`ValidationError` ``code`` into the right HTTP status and
:class:`ErrorResponse` envelope:

    name_missing_or_invalid          -> 422  (blank/oversized name, Req 1.3)
    invalid_stage                    -> 422  (stage outside the four values, Req 1.4)
    invalid_or_past_age_out_date     -> 422  (malformed/past age-out date, Req 2.2)
    future_dated_attendance          -> 422  (malformed/future event date, Req 3.5)
    duplicate_attendance             -> 409  (same member+date already stored, Req 3.2)
    member_not_found                 -> 404  (unknown member id)

Health-score recompute (Req 4.2, 4.3): after a **successful** attendance record
or stage change, the member's Health_Score is recomputed with the pure
``core/health_score.compute_health_score`` (using the member's stored attendance
+ the configured milestones/threshold) and persisted via
``Repository.save_health_score``. A ``StaleRetained`` result (missing/incomplete
inputs) is persisted with its stale flag + reason rather than dropped, keeping
the single-row-per-member Health_Score invariant satisfied on every change.

The scoring *weights* are not part of the persisted CONFIG (which stores only
the at-risk threshold + milestones); they are a fixed, balanced default declared
here (:data:`DEFAULT_SCORING_WEIGHTS`) — non-negative and summing to 1 so the
pure engine's precondition holds. This keeps the recompute self-contained while
still drawing milestones/threshold from the live config.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Response, status

from member_tracker.api.app import get_clock, get_repository
from member_tracker.api.schemas import (
    AgeOutDateRequest,
    AttendanceRequest,
    AttendanceResponse,
    CreateMemberRequest,
    ErrorResponse,
    MemberResponse,
    MilestoneProgressResponse,
    StageChangeRequest,
)
from member_tracker.core.attendance import attended_count, milestone_progress
from member_tracker.core.clock import Clock
from member_tracker.core.health_score import compute_health_score
from member_tracker.core.types import (
    Computed,
    Err,
    MemberView,
    ScoringConfig,
    StaleRetained,
    ValidationError,
)
from member_tracker.io.repository import MemberRecord, Repository

# ---------------------------------------------------------------------------
# Error-code -> HTTP status mapping
# ---------------------------------------------------------------------------
#
# The Repository/core surface structured ValidationError codes; this table is
# the single place that decides the HTTP status for each. Anything not listed
# falls back to 422 (a validation-shaped failure), which is the conservative
# default for a rejected write.
_STATUS_BY_CODE = {
    "name_missing_or_invalid": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "invalid_stage": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "invalid_or_past_age_out_date": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "future_dated_attendance": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "duplicate_attendance": status.HTTP_409_CONFLICT,
    "member_not_found": status.HTTP_404_NOT_FOUND,
}

# Fixed, balanced Health_Score aggregation weights. Non-negative and summing to
# 1.0, satisfying compute_health_score's precondition. The persisted CONFIG only
# carries the at-risk threshold + milestones, so the weights live here.
DEFAULT_SCORING_WEIGHTS = {
    "attendance_weight": 0.35,
    "recency_weight": 0.20,
    "stage_weight": 0.15,
    "activity_weight": 0.30,
}


router = APIRouter(prefix="/api", tags=["members"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raise_domain_error(err_result: "Err[ValidationError]") -> "HTTPException":
    """Translate a structured domain error into an :class:`HTTPException`.

    Chooses the HTTP status from :data:`_STATUS_BY_CODE` (defaulting to 422) and
    carries the :class:`ErrorResponse` envelope as the response body so clients
    receive a uniform ``{code, message}`` shape.
    """
    error = err_result.error
    http_status = _STATUS_BY_CODE.get(
        error.code, status.HTTP_422_UNPROCESSABLE_ENTITY
    )
    return HTTPException(
        status_code=http_status,
        detail=ErrorResponse(code=error.code, message=error.message).model_dump(),
    )


def _member_to_response(record: MemberRecord) -> MemberResponse:
    """Project a repository :class:`MemberRecord` onto the wire model."""
    return MemberResponse(
        id=record.id,
        name=record.name,
        stage=record.stage.value,
        age_out_date=record.age_out_date.isoformat() if record.age_out_date else None,
        stage_changed_at=record.stage_changed_at,
        created_at=record.created_at,
        mentor_name=record.mentor_name,
        area_of_interest=record.area_of_interest,
    )


def _scoring_config(repo: Repository) -> ScoringConfig:
    """Build a :class:`ScoringConfig` from live CONFIG + the fixed weights.

    Milestones and the at-risk threshold come from the persisted CONFIG store;
    the aggregation weights are the fixed :data:`DEFAULT_SCORING_WEIGHTS`.
    """
    config = repo.get_config()
    return ScoringConfig(
        attendance_weight=DEFAULT_SCORING_WEIGHTS["attendance_weight"],
        recency_weight=DEFAULT_SCORING_WEIGHTS["recency_weight"],
        stage_weight=DEFAULT_SCORING_WEIGHTS["stage_weight"],
        activity_weight=DEFAULT_SCORING_WEIGHTS["activity_weight"],
        at_risk_threshold=config.at_risk_threshold,
        milestones=list(config.milestones),
    )


def _recompute_and_persist_health_score(
    repo: Repository,
    clock: Clock,
    member: MemberRecord,
) -> Optional[int]:
    """Recompute a member's Health_Score and persist it (Req 4.2, 4.3).

    Called after a successful attendance record or stage change. Reads the
    member's stored attendance + the live scoring config, runs the pure
    :func:`compute_health_score`, and upserts the result via
    :meth:`Repository.save_health_score`. A :class:`Computed` result is stored
    as a fresh score; a :class:`StaleRetained` result is stored with its stale
    flag + reason so the previous score is preserved (Req 4.3).

    Returns the integer score now stored (the fresh score, or the retained
    previous score when stale), or ``None`` if it could not be resolved.
    """
    attendance = repo.list_attendance(member.id)

    # The MemberView carries the last persisted score as the ``previous`` value
    # used when a recomputation cannot complete (Req 4.3).
    existing = repo.get_health_score(member.id)
    previous_score = existing.score if existing is not None else None

    member_view = MemberView(
        member_id=int(member.id),
        name=member.name,
        stage=member.stage,
        age_out_date=member.age_out_date,
        health_score=previous_score,
        activity_points=repo.total_activity_points(member.id),
    )

    config = _scoring_config(repo)
    result = compute_health_score(
        member_view, attendance, clock.current_date(), config
    )
    computed_at = _as_utc(clock.current_time())

    if isinstance(result, Computed):
        repo.save_health_score(
            member.id, result.score, computed_at, stale=False, reason=None
        )
        return result.score

    if isinstance(result, StaleRetained):
        repo.save_health_score(
            member.id,
            result.previous,
            computed_at,
            stale=True,
            reason=result.reason,
        )
        return result.previous

    return None  # pragma: no cover - HealthResult is a closed union


def _as_utc(moment: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC for score persistence."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _load_member_or_404(repo: Repository, member_id: str) -> MemberRecord:
    """Return the member or raise a 404 with the standard error envelope."""
    record = repo.get_member(member_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorResponse(
                code="member_not_found",
                message=f"No member exists with id {member_id!r}.",
            ).model_dump(),
        )
    return record


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/members",
    response_model=list[MemberResponse],
)
def list_members(
    repo: Repository = Depends(get_repository),
) -> list[MemberResponse]:
    """List all members with their full records (Req 1.x).

    Returns every stored member — including the optional ``mentor_name`` and
    ``area_of_interest`` fields — ordered by ascending numeric id. This backs the
    analytics and outreach views, which need per-member interests/mentors that
    the summarized dashboard row does not carry.
    """
    return [_member_to_response(record) for record in repo.list_members()]


@router.post(
    "/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    responses={422: {"model": ErrorResponse}},
)
def create_member(
    body: CreateMemberRequest,
    repo: Repository = Depends(get_repository),
) -> MemberResponse:
    """Create a member (Req 1.1, 1.3, 1.4).

    The Repository validates name/stage/age-out-date via the pure core before
    persisting; a validation failure is mapped to 422 with the structured error
    code. On success the stored record (with its system-generated id) is
    returned with 201 Created.
    """
    result = repo.create_member(
        name=body.name,
        stage=body.stage,
        age_out_date=body.age_out_date,
        mentor_name=body.mentor_name,
        area_of_interest=body.area_of_interest,
    )
    if isinstance(result, Err):
        raise _raise_domain_error(result)
    return _member_to_response(result.value)


@router.patch(
    "/members/{member_id}/stage",
    response_model=MemberResponse,
    responses={422: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def change_stage(
    member_id: str,
    body: StageChangeRequest,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> MemberResponse:
    """Change a member's stage, then recompute the Health_Score (Req 1.2, 1.4, 4.2).

    An invalid stage maps to 422; an unknown member to 404. On success the new
    stage + UTC change timestamp are persisted, the Health_Score is recomputed
    and persisted (Req 4.2), and the updated member record is returned.
    """
    result = repo.update_member_stage(member_id, body.stage)
    if isinstance(result, Err):
        raise _raise_domain_error(result)

    # Recompute + persist the health score off the new stage (Req 4.2).
    _recompute_and_persist_health_score(repo, clock, result.value)

    return _member_to_response(result.value)


@router.put(
    "/members/{member_id}/age-out-date",
    response_model=MemberResponse,
    responses={422: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def set_age_out_date(
    member_id: str,
    body: AgeOutDateRequest,
    repo: Repository = Depends(get_repository),
) -> MemberResponse:
    """Set a member's age-out date (Req 2.1, 2.2).

    A malformed or past date maps to 422 (and the previously stored value is
    retained by the Repository); an unknown member to 404. On success the
    updated member record is returned.
    """
    result = repo.set_age_out_date(member_id, body.age_out_date)
    if isinstance(result, Err):
        raise _raise_domain_error(result)
    return _member_to_response(result.value)


@router.post(
    "/members/{member_id}/attendance",
    response_model=AttendanceResponse,
    responses={
        422: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
def record_attendance(
    member_id: str,
    body: AttendanceRequest,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> AttendanceResponse:
    """Record attendance, then recompute the Health_Score (Req 3.1, 3.2, 3.5, 4.2).

    A future-dated/malformed event date maps to 422; a duplicate
    ``(member, date)`` to 409; an unknown member to 404. On success the record
    is stored, the member's Health_Score is recomputed and persisted (Req 4.2),
    and the updated attendance count + milestone progress + score are returned.
    """
    # Distinguish an unknown member (404) from a duplicate (409): the Repository
    # relies on a FK/unique constraint and surfaces both as IntegrityError, so
    # we check membership first to give the precise status.
    member = _load_member_or_404(repo, member_id)

    result = repo.add_attendance(member_id, body.event_date)
    if isinstance(result, Err):
        raise _raise_domain_error(result)

    # Recompute + persist the health score off the new attendance (Req 4.2, 4.3).
    health_score = _recompute_and_persist_health_score(repo, clock, member)

    # Rebuild attendance-derived projections for the response (Req 3.3, 3.4).
    records = repo.list_attendance(member_id)
    count = attended_count(records)
    progress = milestone_progress(count, repo.get_config().milestones)

    return AttendanceResponse(
        member_id=member_id,
        attended_count=count,
        progress=MilestoneProgressResponse(
            achieved=list(progress.achieved),
            next_unmet=progress.next_unmet,
            all_achieved=progress.all_achieved,
        ),
        health_score=health_score,
    )


__all__ = ["router"]
