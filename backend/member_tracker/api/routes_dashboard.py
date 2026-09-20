"""routes_dashboard.py — dashboard, config, handover, and export routes (task 11.3).

This module implements the read/administrative slice of the Member_Tracker REST
API and wires the pure Dashboard Assembler + Repository + SnapshotService into
HTTP:

    GET  /api/dashboard?stage=&page=       assembled dashboard view   (Req 4.5, 6.1-6.5, 6.7)
    PUT  /api/config/at-risk-threshold     configure at-risk threshold(Req 4.6, 4.7)
    POST /api/handover/snapshots           create a handover snapshot (Req 5.1, 5.2, 5.3)
    GET  /api/handover/snapshots           list retained snapshots    (Req 5.3)
    GET  /api/handover/snapshots/{id}      view a snapshot's contents (Req 5.5, 5.6)
    GET  /api/export                       export all data            (Req 5.7, 5.8)

Design realized here (design.md "HTTP layer" + "Dashboard Assembler"):

- **Dashboard is a read endpoint (no API key).** It reads the live member +
  attendance state through the :class:`Repository`, builds a live
  :class:`ScoringConfig` (milestones + at-risk threshold from ``repo.get_config``,
  plus the fixed :data:`DEFAULT_SCORING_WEIGHTS` convention established in
  ``routes_members``), and calls the pure ``core/dashboard.build_dashboard`` with
  the injected clock's ``current_date`` (Req 6.1-6.5). At-risk rows are grouped
  contiguously (Req 4.5) and pages hold at most 50 rows (Req 6.2).

- **Retrieval failure never presents stale/partial data as current (Req 6.7).**
  If reading state or assembling the view raises, the handler returns a
  ``503 Service Unavailable`` with a structured error envelope rather than a
  partial/empty dashboard that could be mistaken for the current view.

- **Stage filter parsing.** ``?stage=`` is parsed into the
  :class:`MembershipStage` enum; an unrecognized value maps to ``422`` (Req 6.4).
  ``?page=`` defaults to 1.

- **Administrative endpoints are API-key guarded.** Threshold configuration,
  snapshot creation/listing/viewing, and export all attach
  ``Depends(require_api_key)`` so they return ``401`` without a valid key. The
  dashboard GET is intentionally unguarded (a read view).

- **Domain error -> HTTP mapping.** The pure/IO layers surface structured
  :class:`ValidationError` codes; this layer only translates them:

      threshold_out_of_range   -> 422  (invalid threshold; prior value retained, Req 4.7)
      snapshot_failed          -> 500  (all-or-nothing creation failure, Req 5.2)
      snapshot_timeout         -> 500  (60s budget exceeded, Req 5.2)
      snapshot_unavailable     -> 404/409 (missing -> 404; integrity/tamper -> 409, Req 5.6)
      export_failed            -> 500  (all-or-nothing export failure, Req 5.8)
      export_timeout           -> 500  (60s budget exceeded, Req 5.8)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from member_tracker.api.app import (
    get_clock,
    get_repository,
    get_snapshot_service,
    require_api_key,
)
from member_tracker.api.routes_members import DEFAULT_SCORING_WEIGHTS
from member_tracker.api.schemas import (
    AgeOutStatusResponse,
    ConfigResponse,
    DashboardResponse,
    DashboardRowResponse,
    ErrorResponse,
    ExportResponse,
    MilestoneProgressResponse,
    SnapshotContentsResponse,
    SnapshotListResponse,
    SnapshotMetaResponse,
    ThresholdConfigRequest,
)
from member_tracker.core.clock import Clock
from member_tracker.core.dashboard import build_dashboard
from member_tracker.core.types import (
    AgedOut,
    AlertActive,
    DashboardRow,
    Err,
    MemberView,
    MembershipStage,
    NotApplicable,
    Normal,
    ScoringConfig,
)
from member_tracker.io.repository import Repository
from member_tracker.io.snapshot_service import (
    SnapshotContents,
    SnapshotMeta,
    SnapshotService,
)

# ---------------------------------------------------------------------------
# Snapshot/export error-code -> HTTP status mapping
# ---------------------------------------------------------------------------
#
# The SnapshotService surfaces structured ValidationError codes; this table is
# the single place that decides the HTTP status for each administrative
# snapshot/export failure. snapshot_unavailable is resolved contextually (a
# missing snapshot -> 404, an integrity/tamper failure -> 409) in the handler.
_SNAPSHOT_STATUS_BY_CODE = {
    "snapshot_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "snapshot_timeout": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "export_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "export_timeout": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


router = APIRouter(prefix="/api", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scoring_config(repo: Repository) -> ScoringConfig:
    """Build a :class:`ScoringConfig` from live CONFIG + the fixed weights.

    Milestones and the at-risk threshold come from the persisted CONFIG store
    (``repo.get_config``); the aggregation weights are the fixed
    :data:`DEFAULT_SCORING_WEIGHTS` shared with ``routes_members`` so the
    dashboard's health-score recomputation matches the persisted convention.
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


def _parse_stage_filter(stage: Optional[str]) -> Optional[MembershipStage]:
    """Parse the ``?stage=`` query value into a :class:`MembershipStage`.

    ``None`` / empty (no filter) yields ``None``. An unrecognized stage value
    raises ``422`` with the standard error envelope (Req 6.4) — the filter must
    reference one of the four allowed stages.
    """
    if stage is None or stage == "":
        return None
    try:
        return MembershipStage(stage)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorResponse(
                code="invalid_stage",
                message=(
                    f"Unknown stage filter {stage!r}; must be one of "
                    f"{MembershipStage.allowed_values()}."
                ),
            ).model_dump(),
        )


def _age_out_status_to_response(status_value: object) -> AgeOutStatusResponse:
    """Serialize an :class:`AgeOutStatus` union member into the wire model.

    ``kind`` names the variant; ``days_remaining`` is populated only for the
    ``alert_active`` variant (the whole-day count until the age-out date, Req 2.3).
    """
    if isinstance(status_value, AlertActive):
        return AgeOutStatusResponse(
            kind="alert_active", days_remaining=status_value.days_remaining
        )
    if isinstance(status_value, AgedOut):
        return AgeOutStatusResponse(kind="aged_out", days_remaining=None)
    if isinstance(status_value, Normal):
        return AgeOutStatusResponse(kind="normal", days_remaining=None)
    if isinstance(status_value, NotApplicable):
        return AgeOutStatusResponse(kind="not_applicable", days_remaining=None)
    # Defensive: AgeOutStatus is a closed union.
    raise TypeError(f"unexpected AgeOutStatus variant: {status_value!r}")  # pragma: no cover


def _row_to_response(row: DashboardRow) -> DashboardRowResponse:
    """Project a pure :class:`DashboardRow` onto the wire model.

    ``intervention_eligible`` is derived from the at-risk classification: an
    At_Risk_Member is eligible for a retention intervention (Req 8.1), so the UI
    surfaces the intervention affordance on exactly the at-risk rows.
    """
    return DashboardRowResponse(
        member_id=str(row.member_id),
        name=row.name,
        stage=row.stage.value,
        age_out_status=_age_out_status_to_response(row.age_out_status),
        attendance_progress=MilestoneProgressResponse(
            achieved=list(row.attendance_progress.achieved),
            next_unmet=row.attendance_progress.next_unmet,
            all_achieved=row.attendance_progress.all_achieved,
        ),
        health_score=row.health_score,
        at_risk=row.at_risk,
        score_stale=row.score_stale,
        intervention_eligible=row.at_risk,
    )


def _snapshot_meta_to_response(meta: SnapshotMeta) -> SnapshotMetaResponse:
    """Project a :class:`SnapshotMeta` onto the wire model."""
    return SnapshotMetaResponse(
        id=meta.id,
        created_at=meta.created_at,
        member_count=meta.member_count,
        checksum=meta.checksum,
    )


def _snapshot_contents_to_response(
    contents: SnapshotContents,
) -> SnapshotContentsResponse:
    """Project a :class:`SnapshotContents` onto the wire model."""
    return SnapshotContentsResponse(
        id=contents.id,
        created_at=contents.created_at,
        member_count=contents.member_count,
        members=list(contents.members),
        attendance=list(contents.attendance),
    )


def _raise_snapshot_error(err_result: "Err") -> HTTPException:
    """Translate a SnapshotService structured error into an HTTPException.

    ``snapshot_unavailable`` is resolved contextually: a missing snapshot maps to
    ``404`` while an integrity/tamper failure maps to ``409`` (Req 5.6). All other
    codes use :data:`_SNAPSHOT_STATUS_BY_CODE` (defaulting to ``500``).
    """
    error = err_result.error
    if error.code == "snapshot_unavailable":
        # A missing snapshot is a 404; a corrupt/tampered payload is a 409.
        http_status = (
            status.HTTP_404_NOT_FOUND
            if "not found" in error.message
            else status.HTTP_409_CONFLICT
        )
    else:
        http_status = _SNAPSHOT_STATUS_BY_CODE.get(
            error.code, status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    return HTTPException(
        status_code=http_status,
        detail=ErrorResponse(code=error.code, message=error.message).model_dump(),
    )


# ---------------------------------------------------------------------------
# Dashboard (read; no API key) — Req 4.5, 6.1-6.5, 6.7
# ---------------------------------------------------------------------------


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    responses={
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_dashboard(
    stage: Optional[str] = Query(
        default=None, description="Optional MembershipStage filter."
    ),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> DashboardResponse:
    """Assemble and return a single page of the dashboard (Req 4.5, 6.1-6.5, 6.7).

    Reads the live member + attendance state, builds one row per member with the
    pure ``build_dashboard`` (at-risk grouped contiguously, paginated at 50),
    applies the optional ``?stage=`` filter, and returns the ``no_members`` /
    ``no_match`` empty states. An invalid stage filter maps to ``422``.

    On any failure reading state or assembling the view, returns ``503`` with a
    structured error rather than presenting a partial/stale view as current
    (Req 6.7).
    """
    # Parse the filter first: an invalid stage is a 422, distinct from a
    # retrieval failure (503).
    filter_stage = _parse_stage_filter(stage)

    try:
        members = repo.list_members()

        # Build MemberViews carrying the last persisted score as ``previous`` so
        # a StaleRetained recomputation preserves it (Req 4.3), and gather each
        # member's attendance keyed by member_id for the pure assembler.
        member_views = []
        attendance_by_member = {}
        for record in members:
            existing = repo.get_health_score(record.id)
            previous_score = existing.score if existing is not None else None
            member_views.append(
                MemberView(
                    member_id=int(record.id),
                    name=record.name,
                    stage=record.stage,
                    age_out_date=record.age_out_date,
                    health_score=previous_score,
                    activity_points=repo.total_activity_points(record.id),
                )
            )
            attendance_by_member[int(record.id)] = repo.list_attendance(record.id)

        config = _scoring_config(repo)
        view = build_dashboard(
            members=member_views,
            attendance_by_member=attendance_by_member,
            config=config,
            current_date=clock.current_date(),
            filter_stage=filter_stage,
            page=page,
            page_size=50,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - Req 6.7: never present stale data as current
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorResponse(
                code="dashboard_unavailable",
                message=(
                    "The dashboard could not be retrieved and no current view "
                    f"is available: {exc}"
                ),
            ).model_dump(),
        )

    return DashboardResponse(
        rows=[_row_to_response(row) for row in view.rows],
        page=view.page,
        page_size=view.page_size,
        total_members=view.total_members,
        total_pages=view.total_pages,
        filter_stage=view.filter_stage.value if view.filter_stage else None,
        no_members=view.no_members,
        no_match=view.no_match,
    )


# ---------------------------------------------------------------------------
# Threshold configuration (admin) — Req 4.6, 4.7
# ---------------------------------------------------------------------------


@router.put(
    "/config/at-risk-threshold",
    response_model=ConfigResponse,
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
def set_at_risk_threshold(
    body: ThresholdConfigRequest,
    repo: Repository = Depends(get_repository),
) -> ConfigResponse:
    """Configure the at-risk threshold (Req 4.6, 4.7).

    Delegates to ``Repository.set_at_risk_threshold``, which validates the value
    is an integer in ``[0, 100]`` before writing. An out-of-range value maps to
    ``422`` and the prior threshold is retained (Req 4.7). On success the updated
    configuration is returned.
    """
    result = repo.set_at_risk_threshold(body.at_risk_threshold)
    if isinstance(result, Err):
        error = result.error
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorResponse(code=error.code, message=error.message).model_dump(),
        )
    config = result.value
    return ConfigResponse(
        at_risk_threshold=config.at_risk_threshold,
        milestones=list(config.milestones),
    )


# ---------------------------------------------------------------------------
# Handover snapshots (admin) — Req 5.1, 5.2, 5.3, 5.5, 5.6
# ---------------------------------------------------------------------------


@router.post(
    "/handover/snapshots",
    response_model=SnapshotMetaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def create_snapshot(
    snapshots: SnapshotService = Depends(get_snapshot_service),
) -> SnapshotMetaResponse:
    """Create an all-or-nothing handover snapshot (Req 5.1, 5.2, 5.3).

    Delegates to ``SnapshotService.create_snapshot``. A creation failure or 60s
    timeout maps to ``500`` (the partial snapshot is discarded and existing data
    is untouched, Req 5.2). On success the snapshot metadata is returned with
    ``201 Created``.
    """
    result = snapshots.create_snapshot()
    if isinstance(result, Err):
        raise _raise_snapshot_error(result)
    return _snapshot_meta_to_response(result.value)


@router.get(
    "/handover/snapshots",
    response_model=SnapshotListResponse,
    dependencies=[Depends(require_api_key)],
    responses={401: {"model": ErrorResponse}},
)
def list_snapshots(
    snapshots: SnapshotService = Depends(get_snapshot_service),
) -> SnapshotListResponse:
    """List retained handover snapshots, newest first (Req 5.3)."""
    metas = snapshots.list_snapshots()
    return SnapshotListResponse(
        snapshots=[_snapshot_meta_to_response(meta) for meta in metas]
    )


@router.get(
    "/handover/snapshots/{snapshot_id}",
    response_model=SnapshotContentsResponse,
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def view_snapshot(
    snapshot_id: str,
    snapshots: SnapshotService = Depends(get_snapshot_service),
) -> SnapshotContentsResponse:
    """View a snapshot's full contents, verifying integrity (Req 5.5, 5.6).

    Delegates to ``SnapshotService.load_snapshot``, which recomputes and verifies
    the integrity checksum. A missing snapshot maps to ``404``; a corrupt/tampered
    payload maps to ``409`` — in both cases via the structured
    ``snapshot_unavailable`` error (Req 5.6). Other snapshots are unaffected.
    """
    result = snapshots.load_snapshot(snapshot_id)
    if isinstance(result, Err):
        raise _raise_snapshot_error(result)
    return _snapshot_contents_to_response(result.value)


# ---------------------------------------------------------------------------
# Export (admin) — Req 5.7, 5.8
# ---------------------------------------------------------------------------


@router.get(
    "/export",
    response_model=ExportResponse,
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def export_all(
    snapshots: SnapshotService = Depends(get_snapshot_service),
) -> ExportResponse:
    """Export every Member + Attendance_Record to a complete file (Req 5.7, 5.8).

    Delegates to ``SnapshotService.export_all``, which writes a temporary
    artifact and exposes it only once fully written. A failure or 60s timeout
    maps to ``500`` and leaves no partial file behind (Req 5.8). On success the
    completed file path + counts are returned.
    """
    result = snapshots.export_all()
    if isinstance(result, Err):
        raise _raise_snapshot_error(result)
    export = result.value
    return ExportResponse(
        path=export.path,
        member_count=export.member_count,
        attendance_count=export.attendance_count,
    )


__all__ = ["router"]
