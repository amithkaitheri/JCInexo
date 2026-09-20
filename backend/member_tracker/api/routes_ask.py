"""routes_ask.py — enhanced "Ask about members" analytics endpoint (Req 7+).

The original ``POST /api/query`` interprets a natural-language question into
member-filter *criteria* and returns the matching members. This module adds a
richer companion, ``POST /api/ask``, that answers analytical / aggregate
questions about the whole dashboard dataset — counts, averages, extremes,
stage/health distributions, age-out risk, and gamification/badge totals — and
falls back to the existing member-filter interpreter for filter-style
questions.

It is fully deterministic (no LLM): a small keyword/intent matcher inspects the
question and computes the answer from the same live dashboard projection the
member list is built from, so every number it reports is consistent with what
the dashboard shows and is never fabricated.

Response shape (:class:`AskResponse`):

    {
      "answer": "12 of 29 members are at risk (41%).",   # headline sentence
      "kind": "aggregate" | "members" | "clarification",
      "stats": [{ "label": "At risk", "value": "12" }, ...],
      "members": [ QueryMemberResponse, ... ]             # supporting rows
    }
"""

from __future__ import annotations

import re
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from member_tracker.api.app import get_clock, get_repository
from member_tracker.api.routes_members import DEFAULT_SCORING_WEIGHTS
from member_tracker.api.routes_query import (
    QueryMemberResponse,
    _load_criteria_member_rows,
)
from member_tracker.core.clock import Clock
from member_tracker.core.criteria import CriteriaMemberRow
from member_tracker.core.gamification import default_badge_catalog
from member_tracker.core import activity_points
from member_tracker.io import gemini_client
from member_tracker.io.query_interpreter import ResultSet, interpret_query
from member_tracker.io.repository import Repository

router = APIRouter(prefix="/api", tags=["ask"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ChatTurn(BaseModel):
    """One prior message in the conversation for follow-up context."""

    role: str  # "user" | "model" (also accepts "assistant"/"wellington")
    text: str


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=1000)
    # Prior conversation turns (oldest first) so Gemini can resolve follow-ups
    # like "what about candidates?". Bounded to keep the prompt small.
    history: List[ChatTurn] = Field(default_factory=list)


class StatItem(BaseModel):
    label: str
    value: str


class AskResponse(BaseModel):
    """A natural-language answer plus supporting stats and member rows."""

    answer: str
    kind: str = "aggregate"  # aggregate | members | clarification | chat
    stats: List[StatItem] = Field(default_factory=list)
    members: List[QueryMemberResponse] = Field(default_factory=list)
    # Provenance: which brain produced the answer.
    source: str = "deterministic"  # "gemini" | "deterministic"


# ---------------------------------------------------------------------------
# Serialization helper (mirrors routes_query._member_to_response)
# ---------------------------------------------------------------------------


def _member_to_response(member: CriteriaMemberRow) -> QueryMemberResponse:
    return QueryMemberResponse(
        name=member.name,
        stage=member.stage,
        age_out_status=member.age_out_status,
        attendance_count=member.attendance_count,
        health_score=member.health_score,
        at_risk=member.at_risk,
    )


# ---------------------------------------------------------------------------
# Aggregate helpers
# ---------------------------------------------------------------------------

_STAGES = ["Prospective", "Candidate", "Inducted", "Inactive"]


def _pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def _avg_health(rows: List[CriteriaMemberRow]) -> Optional[float]:
    scored = [r.health_score for r in rows if r.health_score is not None]
    return sum(scored) / len(scored) if scored else None


def _summary_stats(rows: List[CriteriaMemberRow]) -> List[StatItem]:
    """A compact overview always attached to an aggregate answer."""
    total = len(rows)
    at_risk = sum(1 for r in rows if r.at_risk)
    avg = _avg_health(rows)
    total_events = sum(r.attendance_count for r in rows)
    stats = [
        StatItem(label="Members", value=str(total)),
        StatItem(label="At risk", value=str(at_risk)),
        StatItem(
            label="Avg health", value=f"{avg:.0f}" if avg is not None else "—"
        ),
        StatItem(label="Total events", value=str(total_events)),
    ]
    return stats


# ---------------------------------------------------------------------------
# Intent handlers — each returns an AskResponse or None if it does not apply.
# ---------------------------------------------------------------------------


def _answer_aggregate(
    q: str, rows: List[CriteriaMemberRow], repo: Repository
) -> Optional[AskResponse]:
    """Match common analytical questions and compute a deterministic answer."""
    total = len(rows)
    if total == 0:
        return AskResponse(
            answer="There are no members yet. Add members to start tracking.",
            kind="aggregate",
            stats=[],
            members=[],
        )

    ql = q.lower()

    def has(*words: str) -> bool:
        return any(w in ql for w in words)

    # --- Greetings / small talk / help ------------------------------------
    stripped = ql.strip(" !.?")
    if stripped in ("hi", "hello", "hey", "yo", "hiya", "howdy") or has(
        "good morning", "good afternoon", "good evening"
    ):
        return AskResponse(
            answer=(
                f"👋 Hoot hoot! I'm Wellington the Wise. Ask me anything about "
                f"your {total} members — who's at risk, health scores, stage "
                f"breakdowns, attendance, or badges."
            ),
            kind="chat",
        )
    if has("thank", "thanks", "cheers", "appreciate"):
        return AskResponse(
            answer="🦉 Any time! Ask me another question whenever you like.",
            kind="chat",
        )
    if has("what can you do", "help", "who are you", "what can i ask", "capabilities"):
        return AskResponse(
            answer=(
                "I can answer questions about your chapter — for example: "
                "\"how many members are at risk?\", \"average health score\", "
                "\"stage breakdown\", \"who has the most events?\", \"how many "
                "badges earned?\", \"who is aging out soon?\", or filters like "
                "\"prospective members at risk\"."
            ),
            kind="chat",
        )
    if has("average", "avg", "mean") and has("health", "score"):
        avg = _avg_health(rows)
        healthiest = max(
            (r for r in rows if r.health_score is not None),
            key=lambda r: r.health_score,
            default=None,
        )
        answer = (
            f"The average member health score is {avg:.0f} out of 100."
            if avg is not None
            else "No members have a health score yet."
        )
        stats = _summary_stats(rows)
        if healthiest:
            stats.append(
                StatItem(
                    label="Healthiest",
                    value=f"{healthiest.name} ({healthiest.health_score})",
                )
            )
        return AskResponse(answer=answer, stats=stats)

    # --- At-risk count -----------------------------------------------------
    # If a specific stage is also named (e.g. "prospective members at risk"),
    # skip the plain at-risk aggregate and let the member-filter engine handle
    # the compound criteria precisely.
    stage_named = any(s.lower() in ql for s in _STAGES)
    if not stage_named and (
        has("at risk", "at-risk", "atrisk")
        or (has("risk") and has("how many", "count", "number"))
    ):
        at_risk_rows = [r for r in rows if r.at_risk]
        answer = (
            f"{len(at_risk_rows)} of {total} members are at risk "
            f"({_pct(len(at_risk_rows), total)})."
        )
        return AskResponse(
            answer=answer,
            kind="members",
            stats=_summary_stats(rows),
            members=[_member_to_response(r) for r in at_risk_rows],
        )

    # --- Age-out / expiring soon -------------------------------------------
    if has("age out", "age-out", "ageout", "expiring", "expire", "aging out"):
        alert_rows = [r for r in rows if r.age_out_status == "alert_active"]
        aged = [r for r in rows if r.age_out_status == "aged_out"]
        answer = (
            f"{len(alert_rows)} member(s) are within 30 days of aging out"
            + (f", and {len(aged)} have already aged out." if aged else ".")
        )
        return AskResponse(
            answer=answer,
            kind="members",
            stats=[
                StatItem(label="Aging out soon", value=str(len(alert_rows))),
                StatItem(label="Aged out", value=str(len(aged))),
                StatItem(label="Members", value=str(total)),
            ],
            members=[_member_to_response(r) for r in alert_rows + aged],
        )

    # --- Stage breakdown / distribution ------------------------------------
    if has("breakdown", "distribution", "how many in each", "by stage") or (
        has("stage") and has("how many", "count", "each")
    ):
        counts = {s: sum(1 for r in rows if r.stage == s) for s in _STAGES}
        parts = ", ".join(f"{counts[s]} {s}" for s in _STAGES)
        return AskResponse(
            answer=f"Stage breakdown of {total} members: {parts}.",
            stats=[StatItem(label=s, value=str(counts[s])) for s in _STAGES],
        )

    # --- Per-stage count ("how many inducted members") --------------------
    for stage in _STAGES:
        if stage.lower() in ql and has("how many", "count", "number", "are there"):
            matched = [r for r in rows if r.stage == stage]
            return AskResponse(
                answer=f"{len(matched)} of {total} members are {stage} "
                f"({_pct(len(matched), total)}).",
                kind="members",
                stats=_summary_stats(rows),
                members=[_member_to_response(r) for r in matched],
            )

    # --- Most / top attendance --------------------------------------------
    if has("most", "top", "highest") and has(
        "event", "attend", "attendance", "active"
    ):
        top = sorted(rows, key=lambda r: r.attendance_count, reverse=True)[:5]
        leader = top[0]
        return AskResponse(
            answer=(
                f"{leader.name} has attended the most events "
                f"({leader.attendance_count})."
            ),
            kind="members",
            stats=[
                StatItem(label="Top attendee", value=leader.name),
                StatItem(label="Events", value=str(leader.attendance_count)),
            ],
            members=[_member_to_response(r) for r in top],
        )

    # --- Badges / gamification --------------------------------------------
    if has("badge", "trail", "milestone", "unlock", "gamif"):
        milestones = list(repo.get_config().milestones)
        catalog = default_badge_catalog(milestones)
        total_badges = 0
        top_member = None
        top_count = -1
        for record in repo.list_members():
            earned = repo.list_earned_badges(record.id)
            total_badges += len(earned)
            if len(earned) > top_count:
                top_count = len(earned)
                top_member = record.name
        return AskResponse(
            answer=(
                f"{total_badges} badges earned across {total} members. "
                f"There are {len(catalog)} badges on Wellington's Trail "
                f"(milestones at {', '.join(map(str, milestones))} events)."
            ),
            stats=[
                StatItem(label="Badges earned", value=str(total_badges)),
                StatItem(label="Badges available", value=str(len(catalog))),
                StatItem(
                    label="Trail leader",
                    value=f"{top_member} ({max(top_count, 0)})"
                    if top_member
                    else "—",
                ),
            ],
        )

    # --- Total members / general count ------------------------------------
    if has("how many members", "total members", "member count") or (
        has("how many") and has("member") and not any(s.lower() in ql for s in _STAGES)
    ):
        return AskResponse(
            answer=f"The chapter has {total} members.",
            stats=_summary_stats(rows),
        )

    # --- Total events ------------------------------------------------------
    if has("how many events", "total events", "total attendance"):
        total_events = sum(r.attendance_count for r in rows)
        return AskResponse(
            answer=f"{total_events} event attendances recorded across {total} members.",
            stats=_summary_stats(rows),
        )

    # --- Generic "overview" / "summary" ------------------------------------
    if has("overview", "summary", "status", "how are we", "health of the chapter"):
        at_risk = sum(1 for r in rows if r.at_risk)
        avg = _avg_health(rows)
        return AskResponse(
            answer=(
                f"Chapter overview: {total} members, {at_risk} at risk, "
                f"average health {avg:.0f}."
                if avg is not None
                else f"Chapter overview: {total} members, {at_risk} at risk."
            ),
            stats=_summary_stats(rows),
        )

    return None


# ---------------------------------------------------------------------------
# Grounding context for Gemini (factual snapshot the model may use)
# ---------------------------------------------------------------------------


def _build_grounding_context(
    rows: List[CriteriaMemberRow], repo: Repository
) -> str:
    """Build a compact, factual snapshot of the chapter for the LLM prompt.

    Includes computed aggregate stats plus a per-member table (name, stage,
    events, health, at-risk, age-out) and badge totals. The LLM is instructed to
    answer only from this text, so it can present the real data conversationally
    without fabricating anything.
    """
    total = len(rows)
    at_risk = sum(1 for r in rows if r.at_risk)
    avg = _avg_health(rows)
    total_events = sum(r.attendance_count for r in rows)
    stage_counts = {s: sum(1 for r in rows if r.stage == s) for s in _STAGES}
    alert = sum(1 for r in rows if r.age_out_status == "alert_active")
    aged = sum(1 for r in rows if r.age_out_status == "aged_out")

    # Badge totals.
    milestones = list(repo.get_config().milestones)
    catalog = default_badge_catalog(milestones)
    total_badges = sum(
        len(repo.list_earned_badges(m.id)) for m in repo.list_members()
    )

    # Engagement points per member (name -> total), for points-aware answers.
    members = repo.list_members()
    points_by_name = {m.name: repo.total_activity_points(m.id) for m in members}
    total_points = sum(points_by_name.values())
    avg_points = (total_points / len(members)) if members else 0
    top_points = sorted(points_by_name.items(), key=lambda kv: kv[1], reverse=True)[:5]

    lines: List[str] = []
    lines.append(f"Total members: {total}")
    lines.append(f"At-risk members: {at_risk}")
    lines.append(
        f"Average health score: {avg:.0f}/100" if avg is not None else
        "Average health score: n/a"
    )
    lines.append(f"Total event attendances: {total_events}")
    lines.append(
        "Stage counts: "
        + ", ".join(f"{stage_counts[s]} {s}" for s in _STAGES)
    )
    lines.append(f"Aging out within 30 days: {alert}; already aged out: {aged}")
    lines.append(
        f"Badges: {total_badges} earned; {len(catalog)} available "
        f"(milestones at {', '.join(map(str, milestones))} events)"
    )

    # --- Engagement points system explainer + stats ------------------------
    lines.append("")
    lines.append(
        "ENGAGEMENT POINTS SYSTEM: members earn points for JCI activities. "
        "These points are one of four signals in the Health_Score (weighted "
        "30%, alongside attendance depth, recency, and membership stage). "
        f"Chapter totals: {total_points} points across all members, "
        f"averaging {avg_points:.0f} per member."
    )
    lines.append(
        "Point values per activity: "
        + "; ".join(f"{a.label} = +{a.points}" for a in activity_points.catalog())
    )
    lines.append(
        "Engagement tiers (by total points): "
        + "; ".join(f"{t.label} at {t.threshold}+" for t in activity_points.tiers())
    )
    if top_points:
        lines.append(
            "Top point earners: "
            + ", ".join(f"{name} ({pts})" for name, pts in top_points)
        )

    lines.append("")
    lines.append(
        "MEMBERS (name | stage | events | points | health | at_risk | age_out):"
    )
    # Cap the table so the prompt stays small for large chapters.
    for r in rows[:60]:
        health = r.health_score if r.health_score is not None else "n/a"
        pts = points_by_name.get(r.name, 0)
        lines.append(
            f"- {r.name} | {r.stage} | {r.attendance_count} | {pts} | {health} | "
            f"{'yes' if r.at_risk else 'no'} | {r.age_out_status}"
        )
    if total > 60:
        lines.append(f"...and {total - 60} more members.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/ask", response_model=AskResponse)
def ask(
    body: AskRequest,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> AskResponse:
    """Answer a question about the chapter, conversationally.

    Strategy:
      1. Compute the deterministic answer (stats + relevant members) from the
         live dashboard projection — this is always the source of truth for the
         numbers and the member tables.
      2. If a Gemini API key is configured, ask Gemini to phrase a natural,
         conversational reply grounded in a factual snapshot of the data (and
         the prior conversation for follow-ups). Attach the deterministic
         stats/members so the UI still renders chips and tables. ``source`` =
         "gemini".
      3. If Gemini is not configured or fails/times out, return the
         deterministic answer directly. ``source`` = "deterministic".
    """
    rows = _load_criteria_member_rows(repo, clock)

    # --- Deterministic answer (stats + members), always computed. ----------
    deterministic = _answer_aggregate(body.query, rows, repo)
    if deterministic is None:
        outcome = interpret_query(
            body.query, rows, current_date=clock.current_date(), translator=None
        )
        if isinstance(outcome, ResultSet):
            matched = list(outcome.members)
            deterministic = AskResponse(
                answer=(
                    f"Found {len(matched)} member(s) matching your question."
                    if matched
                    else "No members match that description."
                ),
                kind="members",
                stats=_summary_stats(rows),
                members=[_member_to_response(m) for m in matched],
            )
        else:
            deterministic = AskResponse(
                answer=(
                    "I couldn't interpret that. Try: \"how many members are at "
                    "risk?\", \"average health score\", \"stage breakdown\", "
                    "\"who has the most events?\", \"how many badges earned?\", "
                    "or \"prospective members at risk\"."
                ),
                kind="clarification",
                stats=[],
                members=[],
            )
    deterministic.source = "deterministic"

    # --- Try Gemini for a conversational phrasing (grounded). --------------
    if gemini_client.is_configured():
        try:
            context = _build_grounding_context(rows, repo)
            history = [(t.role, t.text) for t in body.history][-10:]
            reply = gemini_client.generate_reply(body.query, context, history)
            # Keep the deterministic stats/members for the UI; swap in the
            # conversational answer text and tag the source.
            return AskResponse(
                answer=reply,
                kind="chat",
                stats=deterministic.stats,
                members=deterministic.members,
                source="gemini",
            )
        except gemini_client.GeminiError:
            # Graceful degradation — fall through to the deterministic answer.
            pass

    return deterministic


__all__ = ["router"]
