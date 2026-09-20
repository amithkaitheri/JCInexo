"""routes_gamification.py — "Wellington's Trail" gamification routes (Req 7).

Wires the pure ``core/gamification`` engine + the Repository into HTTP for the
Gamified Onboarding feature:

    GET  /api/members/{id}/gamification   read the member's mascot state + trail
    POST /api/members/{id}/gamification/sync
                                          award any newly-qualified badges and
                                          return the refreshed trail (drives the
                                          celebration overlay)

Design realized here:

- **Badges are derived from live attendance.** The route reads the member's
  stored attendance count and the configured milestones, builds the default
  badge catalog, and uses the pure ``newly_unlocked_badges`` to decide which
  badges to persist. The GET is a pure read; the POST /sync is the idempotent
  write invoked after an attendance record so a milestone unlock celebrates
  exactly once (Req 7, "Milestone & Badge Unlocks").

- **Mascot state comes from the same health/age-out inputs the dashboard uses.**
  ``derive_mascot_state`` maps Health_Score (vs. the at-risk threshold) and the
  age-out status to HAPPY / ALERT / CELEBRATING (spec 2.1). A ``CELEBRATING``
  state is returned from /sync only when a badge was actually just unlocked.

- **No API key.** Gamification is a member-facing read/engagement surface, so it
  is unguarded like the dashboard GET. An unknown member maps to 404 with the
  standard error envelope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from member_tracker.api.app import get_clock, get_repository
from member_tracker.api.schemas import (
    EarnedBadgeResponse,
    ErrorResponse,
    GamificationResponse,
    PointsTierResponse,
    TrailNodeResponse,
)
from member_tracker.core.age_out import age_out_status
from member_tracker.core.attendance import attended_count
from member_tracker.core import activity_points
from member_tracker.core.clock import Clock
from member_tracker.core.gamification import (
    EarnedBadge,
    MascotState,
    build_trail,
    default_badge_catalog,
    derive_mascot_state,
    mascot_message,
    newly_unlocked_badges,
)
from member_tracker.io.repository import MemberRecord, Repository

router = APIRouter(prefix="/api", tags=["gamification"])


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


def _build_response(
    repo: Repository,
    clock: Clock,
    member: MemberRecord,
    *,
    just_unlocked_ids: list[str],
) -> GamificationResponse:
    """Assemble the full gamification payload for a member.

    Reads the live attendance count + configured milestones + health/age-out
    context, builds the trail from the persisted earned badges, and derives the
    mascot state. ``just_unlocked_ids`` marks badges unlocked by the current
    /sync call so the mascot celebrates and the overlay fires exactly once.
    """
    config = repo.get_config()
    catalog = default_badge_catalog(list(config.milestones))

    count = attended_count(repo.list_attendance(member.id))

    # Earned badges (persisted) -> pure EarnedBadge value objects for the trail.
    earned_records = repo.list_earned_badges(member.id)
    earned = [
        EarnedBadge(
            badge_id=r.badge_id,
            badge_name=r.badge_name,
            unlocked_at=r.unlocked_at,
        )
        for r in earned_records
    ]

    trail = build_trail(
        stage=member.stage,
        attended_event_count=count,
        catalog=catalog,
        earned_badges=earned,
    )

    # Health + age-out context for the mascot state (spec 2.1).
    health = repo.get_health_score(member.id)
    health_score = health.score if health is not None else None
    at_risk = (
        health_score is not None and health_score <= config.at_risk_threshold
    )
    ao_status = age_out_status(member.age_out_date, clock.current_date())

    state = derive_mascot_state(
        health_score=health_score,
        at_risk_threshold=config.at_risk_threshold,
        age_out_status=ao_status,
        just_celebrated=bool(just_unlocked_ids),
        stage=member.stage,
    )

    newly = [r for r in earned_records if r.badge_id in set(just_unlocked_ids)]

    # Reason-aware ALERT message: distinguish an age-out (renewal) alert from a
    # low-engagement alert so an engaged member nearing age-out isn't mislabelled.
    from member_tracker.core.types import AlertActive as _AlertActive
    from member_tracker.core.types import MembershipStage

    _age_out_alert = isinstance(ao_status, _AlertActive)
    _days_remaining = ao_status.days_remaining if _age_out_alert else None
    _inactive = member.stage is MembershipStage.INACTIVE

    # Points-based engagement trail: one node per tier, unlocked by total points.
    total_points = repo.total_activity_points(member.id)
    tier_now = activity_points.current_tier(total_points)
    tier_next = activity_points.next_tier(total_points)
    points_nodes = [
        PointsTierResponse(
            key=t.key,
            label=t.label,
            icon=t.icon,
            threshold=t.threshold,
            unlocked=total_points >= t.threshold,
            current=(t.key == tier_now.key),
        )
        for t in activity_points.tiers()
    ]
    points_to_next = (tier_next.threshold - total_points) if tier_next else None

    return GamificationResponse(
        member_id=member.id,
        name=member.name,
        stage=member.stage.value,
        mascot_state=state.value,
        mascot_message=mascot_message(
            state,
            member.name,
            age_out_alert=_age_out_alert,
            days_remaining=_days_remaining,
            inactive=_inactive,
        ),
        health_score=health_score,
        at_risk=at_risk,
        attended_event_count=count,
        next_milestone=trail.next_milestone,
        nodes=[
            TrailNodeResponse(
                badge_id=n.badge_id,
                label=n.label,
                icon=n.icon,
                threshold=n.threshold,
                unlocked=n.unlocked,
                current=n.current,
            )
            for n in trail.nodes
        ],
        earned_badges=[
            EarnedBadgeResponse(
                badge_id=b.badge_id,
                badge_name=b.badge_name,
                unlocked_at=b.unlocked_at,
            )
            for b in trail.earned_badges
        ],
        newly_unlocked=[
            EarnedBadgeResponse(
                badge_id=r.badge_id,
                badge_name=r.badge_name,
                unlocked_at=r.unlocked_at,
            )
            for r in newly
        ],
        total_points=total_points,
        points_tier_label=tier_now.label,
        points_nodes=points_nodes,
        points_to_next=points_to_next,
        next_tier_label=tier_next.label if tier_next else None,
    )


@router.get(
    "/members/{member_id}/gamification",
    response_model=GamificationResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_gamification(
    member_id: str,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> GamificationResponse:
    """Return the member's mascot state, trail, and earned badges (Req 7).

    A pure read: it computes qualifying badges from attendance but does not
    persist or celebrate. Unknown member -> 404.
    """
    member = _load_member_or_404(repo, member_id)
    return _build_response(repo, clock, member, just_unlocked_ids=[])


@router.post(
    "/members/{member_id}/gamification/sync",
    response_model=GamificationResponse,
    responses={404: {"model": ErrorResponse}},
)
def sync_gamification(
    member_id: str,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> GamificationResponse:
    """Award any newly-qualified badges and return the refreshed trail (Req 7).

    Idempotent: computes the badges the member now qualifies for that are not
    yet earned (``newly_unlocked_badges``), persists them via
    ``Repository.award_badge`` (which itself no-ops on an already-earned badge),
    and returns the refreshed profile. ``newly_unlocked`` in the response drives
    the celebration overlay and sets the mascot to CELEBRATING. Called after an
    attendance record so a milestone celebrates exactly once. Unknown member ->
    404.
    """
    member = _load_member_or_404(repo, member_id)

    config = repo.get_config()
    catalog = default_badge_catalog(list(config.milestones))
    count = attended_count(repo.list_attendance(member.id))

    already = [r.badge_id for r in repo.list_earned_badges(member.id)]
    to_award = newly_unlocked_badges(count, catalog, already)

    now = clock.current_time()
    unlocked_ids: list[str] = []
    for badge in to_award:
        awarded = repo.award_badge(member.id, badge.badge_id, badge.name, now)
        if awarded is not None:
            unlocked_ids.append(awarded.badge_id)

    return _build_response(repo, clock, member, just_unlocked_ids=unlocked_ids)


# ---------------------------------------------------------------------------
# ImpactQuest leaderboard
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BaseModel  # local import to avoid polluting the top


class LeaderboardEntryResponse(_BaseModel):
    rank: int
    member_id: str
    name: str
    stage: str
    total_points: int
    tier_label: str
    rank_name: str
    rank_icon: str


class LeaderboardResponse(_BaseModel):
    entries: list[LeaderboardEntryResponse]
    total_members: int
    as_of: str


_RANK_MAP = {
    "rookie":      ("Owlet",        "🐣"),
    "contributor": ("Scout",        "🦉"),
    "active":      ("Glider",       "🌲"),
    "leader":      ("Ranger",       "🏔️"),
    "champion":    ("Wise Owl",     "👑"),
}


@router.get(
    "/leaderboard",
    response_model=LeaderboardResponse,
    summary="ImpactQuest global points leaderboard",
)
def get_leaderboard(
    limit: int = 50,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> LeaderboardResponse:
    """Return members ranked by total engagement points, highest first.

    Computed live from MEMBER_ACTIVITY — trivia points written by the game are
    reflected immediately on the next call. ``limit`` caps rows (default 50).
    """
    from member_tracker.core import activity_points as _ap

    rows = repo.get_leaderboard(limit=min(int(limit), 200))
    entries = []
    for row in rows:
        pts = row["total_points"]
        tier = _ap.current_tier(pts)
        rank_name, rank_icon = _RANK_MAP.get(tier.key, ("Owlet", "🐣"))
        entries.append(
            LeaderboardEntryResponse(
                rank=row["rank"],
                member_id=row["member_id"],
                name=row["name"],
                stage=row["stage"],
                total_points=pts,
                tier_label=tier.label,
                rank_name=rank_name,
                rank_icon=rank_icon,
            )
        )

    return LeaderboardResponse(
        entries=entries,
        total_members=len(entries),
        as_of=clock.current_time().isoformat(),
    )


__all__ = ["router"]
