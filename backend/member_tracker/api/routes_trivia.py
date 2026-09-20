"""routes_trivia.py — ImpactQuest Trail Trivia mini-game endpoints.

Endpoints:
    GET  /api/trivia/daily          — today's 3 questions (no answers exposed)
    POST /api/trivia/daily/submit   — submit answers → score, points, badges

Design:
- Both endpoints require a valid member JWT (require_member_token).
- The same 3 questions are served to every member on a given date (the date
  string is the RNG seed), creating a shared "daily puzzle" feel.
- On submit the server re-derives the same question set to prevent answer
  fishing (client cannot GET the questions with answers, then POST).
- Points are written to MEMBER_ACTIVITY (activity_key="trail_trivia_daily")
  using the existing log_activity path, so they flow immediately into the
  health score, leaderboard, and Wellington's Trail tier display.
- A SQLite UNIQUE constraint on (member_id, activity_key, DATE(occurred_at))
  enforces the one-play-per-day rule at the DB level. The submit route catches
  the resulting IntegrityError and returns 409 "already_played".
- After awarding points the route syncs badges (newly_unlocked_badges) and
  computes a rank_up flag (tier before vs. after), giving the frontend
  everything it needs to show the celebration screen in one round-trip.

Points schedule:
    Per correct answer      15 pts
    Perfect round bonus     +10 pts  (all 3 correct)
    Streak continuation     +5 pts   (streak > 1 day)
    Participation (0/3)      5 pts   (consolation, encourages daily habit)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from member_tracker.api.app import get_clock, get_repository
from member_tracker.api.routes_member_auth import _rank, require_member_token
from member_tracker.core import activity_points
from member_tracker.core.attendance import attended_count
from member_tracker.core.clock import Clock
from member_tracker.core.gamification import (
    default_badge_catalog,
    newly_unlocked_badges,
)
from member_tracker.io.repository import Repository

router = APIRouter(prefix="/api/trivia", tags=["trivia"])

# Points constants
_PTS_CORRECT = 15
_PTS_PERFECT_BONUS = 10
_PTS_STREAK_BONUS = 5
_PTS_PARTICIPATION = 5

# The activity key used when logging trivia points to MEMBER_ACTIVITY.
_TRIVIA_ACTIVITY_KEY = "trail_trivia_daily"
_TRIVIA_ACTIVITY_LABEL = "Trail Trivia (daily)"
_TRIVIA_CATEGORY = "Mini-Game"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TriviaQuestionOut(BaseModel):
    id: int
    question: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str


class DailyTriviaResponse(BaseModel):
    date: str
    questions: list[TriviaQuestionOut]
    already_played: bool
    points_earned: Optional[int] = None  # filled when already_played=True


class AnswerSubmission(BaseModel):
    # Maps question id (as string) to chosen answer letter: 'a'|'b'|'c'|'d'
    answers: dict[str, str]


class BadgeOut(BaseModel):
    badge_id: str
    badge_name: str
    unlocked_at: str


class TriviaResultResponse(BaseModel):
    correct_count: int
    total_questions: int
    points_earned: int
    streak: int
    longest_streak: int
    rank_up: bool
    old_rank: str
    new_rank: str
    new_total_points: int
    new_tier_label: str
    points_to_next: Optional[int]
    next_tier_label: Optional[str]
    newly_unlocked_badges: list[BadgeOut]
    correct_answers: dict[str, str]   # revealed after submit: {str(q_id): letter}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_iso(clock: Clock) -> str:
    return clock.current_date().isoformat()


def _already_played_today(repo: Repository, member_id: str, today: str) -> Optional[int]:
    """Return the points earned today if the member already played, else None."""
    conn = repo._connect()
    try:
        row = conn.execute(
            """
            SELECT SUM(points) AS pts
            FROM MEMBER_ACTIVITY
            WHERE member_id = ?
              AND activity_key = ?
              AND DATE(occurred_at) = ?
            """,
            (member_id, _TRIVIA_ACTIVITY_KEY, today),
        ).fetchone()
    finally:
        conn.close()
    total = row["pts"] if row and row["pts"] is not None else 0
    # Any activity row for today → already played.
    conn2 = repo._connect()
    try:
        exists = conn2.execute(
            """
            SELECT 1 FROM MEMBER_ACTIVITY
            WHERE member_id = ?
              AND activity_key = ?
              AND DATE(occurred_at) = ?
            LIMIT 1
            """,
            (member_id, _TRIVIA_ACTIVITY_KEY, today),
        ).fetchone()
    finally:
        conn2.close()
    return int(total) if exists else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/daily",
    response_model=DailyTriviaResponse,
    summary="Today's 3 Trail Trivia questions (no answers)",
)
def get_daily_trivia(
    member_id: str = Depends(require_member_token),
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> DailyTriviaResponse:
    """Return today's 3 questions.

    Questions are drawn deterministically by date so every member gets the same
    daily puzzle. The ``correct`` field is never included in the response.
    If the member has already played today, ``already_played`` is True and
    ``questions`` is empty (no point showing questions they can't re-answer).
    """
    today = _today_iso(clock)
    played_pts = _already_played_today(repo, member_id, today)

    if played_pts is not None:
        return DailyTriviaResponse(
            date=today,
            questions=[],
            already_played=True,
            points_earned=played_pts,
        )

    raw = repo.get_daily_trivia_questions(today)
    if not raw:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "no_questions",
                "message": "No trivia questions are available yet. Ask your LP to seed the question bank.",
            },
        )

    questions = [
        TriviaQuestionOut(
            id=q["id"],
            question=q["question"],
            option_a=q["option_a"],
            option_b=q["option_b"],
            option_c=q["option_c"],
            option_d=q["option_d"],
        )
        for q in raw
    ]

    return DailyTriviaResponse(date=today, questions=questions, already_played=False)


@router.post(
    "/daily/submit",
    response_model=TriviaResultResponse,
    summary="Submit today's answers and earn points",
)
def submit_daily_trivia(
    body: AnswerSubmission,
    member_id: str = Depends(require_member_token),
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> TriviaResultResponse:
    """Score the member's answers, award points, and return the result.

    The server re-derives today's questions (same date seed as GET /daily) so
    the ``correct`` answers are authoritative here, not from the client.
    Returns 409 if the member already played today.
    """
    today = _today_iso(clock)

    # Block double-play before doing any scoring work.
    if _already_played_today(repo, member_id, today) is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "already_played", "message": "You've already played today's trivia. Come back tomorrow!"},
        )

    # Re-derive questions server-side (prevents answer fishing).
    raw = repo.get_daily_trivia_questions(today)
    if not raw:
        raise HTTPException(
            status_code=503,
            detail={"code": "no_questions", "message": "No trivia questions available."},
        )

    # Score answers.
    correct_answers: dict[str, str] = {str(q["id"]): q["_correct"] for q in raw}
    correct_count = sum(
        1
        for q in raw
        if body.answers.get(str(q["id"]), "").lower() == q["_correct"]
    )
    is_perfect = correct_count == len(raw)

    # Compute points.
    if correct_count == 0:
        points = _PTS_PARTICIPATION
    else:
        points = correct_count * _PTS_CORRECT
        if is_perfect:
            points += _PTS_PERFECT_BONUS

    # Streak update first so we know if bonus applies.
    streak_data = repo.update_streak(member_id, today)
    if streak_data["current_streak"] > 1:
        points += _PTS_STREAK_BONUS

    # Snapshot tier BEFORE awarding points (for rank-up detection).
    points_before = repo.total_activity_points(member_id)
    old_tier = activity_points.current_tier(points_before)
    old_rank, _ = _rank(points_before)

    # Write to MEMBER_ACTIVITY.
    now = clock.current_time()
    repo.log_activity(
        member_id=member_id,
        activity_key=_TRIVIA_ACTIVITY_KEY,
        activity_label=_TRIVIA_ACTIVITY_LABEL,
        category=_TRIVIA_CATEGORY,
        points=points,
        occurred_at=now,
        note=f"{correct_count}/{len(raw)} correct",
    )

    # Recompute health score so points flow into health immediately.
    try:
        from member_tracker.api.routes_members import _recompute_and_persist_health_score
        member = repo.get_member(member_id)
        if member:
            _recompute_and_persist_health_score(repo, clock, member)
    except Exception:
        pass  # non-critical; health score will recompute on next dashboard load

    # New totals and tier.
    new_total = repo.total_activity_points(member_id)
    new_tier = activity_points.current_tier(new_total)
    new_rank, _ = _rank(new_total)
    tier_next = activity_points.next_tier(new_total)
    rank_up = old_tier.key != new_tier.key

    # Badge sync: award any newly qualified badges.
    config = repo.get_config()
    catalog = default_badge_catalog(list(config.milestones))
    attendance_count = attended_count(repo.list_attendance(member_id))
    already_earned_ids = [b.badge_id for b in repo.list_earned_badges(member_id)]
    new_badges = newly_unlocked_badges(attendance_count, catalog, already_earned_ids)
    awarded_badges = []
    for badge in new_badges:
        result = repo.award_badge(
            member_id, badge.badge_id, badge.name,
            datetime.now(timezone.utc),
        )
        if result is not None:
            awarded_badges.append(
                BadgeOut(
                    badge_id=result.badge_id,
                    badge_name=result.badge_name,
                    unlocked_at=result.unlocked_at,
                )
            )

    return TriviaResultResponse(
        correct_count=correct_count,
        total_questions=len(raw),
        points_earned=points,
        streak=streak_data["current_streak"],
        longest_streak=streak_data["longest_streak"],
        rank_up=rank_up,
        old_rank=old_rank,
        new_rank=new_rank,
        new_total_points=new_total,
        new_tier_label=new_tier.label,
        points_to_next=(tier_next.threshold - new_total) if tier_next else None,
        next_tier_label=tier_next.label if tier_next else None,
        newly_unlocked_badges=awarded_badges,
        correct_answers=correct_answers,
    )


__all__ = ["router"]
