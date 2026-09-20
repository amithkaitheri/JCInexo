"""routes_activities.py — engagement points ledger + catalogue.

Endpoints:
    GET  /api/activities/catalog          — the recognised activities + points
    GET  /api/members/{id}/activities     — a member's logged activities + total
    POST /api/members/{id}/activities     — log an activity (recomputes health)

Logging an activity recomputes and persists the member's Health_Score so the
points immediately move the score (points are a weighted signal in the score —
see core/activity_points.py + core/health_score.py).
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from member_tracker.api.app import get_clock, get_repository, require_api_key
from member_tracker.api.routes_members import _recompute_and_persist_health_score
from member_tracker.core import activity_points
from member_tracker.core.clock import Clock
from member_tracker.io.repository import Repository

router = APIRouter(prefix="/api", tags=["activities"])


class ActivityTypeModel(BaseModel):
    key: str
    label: str
    category: str
    points: int


class ActivityModel(BaseModel):
    id: int
    activity_key: str
    activity_label: str
    category: str
    points: int
    occurred_at: str
    note: Optional[str] = None


class MemberActivitiesResponse(BaseModel):
    member_id: str
    total_points: int
    activities: List[ActivityModel]


class LogActivityRequest(BaseModel):
    activity_key: str = Field(..., min_length=1, max_length=80)
    note: Optional[str] = Field(default=None, max_length=300)


@router.get("/activities/catalog", response_model=List[ActivityTypeModel])
def activity_catalog() -> List[ActivityTypeModel]:
    """Return the full engagement points catalogue."""
    return [
        ActivityTypeModel(key=a.key, label=a.label, category=a.category, points=a.points)
        for a in activity_points.catalog()
    ]


@router.get("/members/{member_id}/activities", response_model=MemberActivitiesResponse)
def list_member_activities(
    member_id: str,
    repo: Repository = Depends(get_repository),
) -> MemberActivitiesResponse:
    """Return a member's logged activities and their point total."""
    records = repo.list_activities(member_id)
    return MemberActivitiesResponse(
        member_id=member_id,
        total_points=repo.total_activity_points(member_id),
        activities=[
            ActivityModel(
                id=r.id,
                activity_key=r.activity_key,
                activity_label=r.activity_label,
                category=r.category,
                points=r.points,
                occurred_at=r.occurred_at,
                note=r.note,
            )
            for r in records
        ],
    )


@router.post(
    "/members/{member_id}/activities",
    response_model=MemberActivitiesResponse,
    dependencies=[Depends(require_api_key)],
)
def log_member_activity(
    member_id: str,
    body: LogActivityRequest,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> MemberActivitiesResponse:
    """Log an engagement activity for a member and recompute their health score."""
    member = repo.get_member(member_id)
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "member_not_found", "message": "Member not found."},
        )
    if not activity_points.is_valid_key(body.activity_key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "unknown_activity",
                "message": f"Unknown activity '{body.activity_key}'.",
            },
        )

    activity = activity_points.get_activity(body.activity_key)
    repo.log_activity(
        member_id=member_id,
        activity_key=activity.key,
        activity_label=activity.label,
        category=activity.category,
        points=activity.points,
        occurred_at=clock.current_time(),
        note=body.note,
    )
    # Recompute health so the new points immediately affect the score.
    _recompute_and_persist_health_score(repo, clock, member)

    records = repo.list_activities(member_id)
    return MemberActivitiesResponse(
        member_id=member_id,
        total_points=repo.total_activity_points(member_id),
        activities=[
            ActivityModel(
                id=r.id,
                activity_key=r.activity_key,
                activity_label=r.activity_label,
                category=r.category,
                points=r.points,
                occurred_at=r.occurred_at,
                note=r.note,
            )
            for r in records
        ],
    )


__all__ = ["router"]
