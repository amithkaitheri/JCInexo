"""routes_recommendations.py — the Wellington retention routes (task 19.1, Requirement 8).

This module wires the Wellington Retention Agent
(``io/wellington_agent.generate_recommendation``) and the persisted
Retention_Recommendation store (``io/repository``) into HTTP as three endpoints:

    POST /api/members/{member_id}/recommendations       generate a recommendation
                                                         (Req 8.2, 8.4–8.8, 8.11)
    GET  /api/members/{member_id}/recommendations        view the stored recommendation
                                                         + copy-outreach data (Req 8.9)
    POST /api/members/{member_id}/recommendations/sent   mark the recommendation as sent
                                                         (Req 8.10)

Router wiring note: ``api/app.py``'s ``_register_default_routers`` already
best-effort includes ``member_tracker.api.routes_recommendations.router``
(inside a ``try/except ImportError``), so this module only needs to expose an
``APIRouter`` named ``router``. It does **not** edit ``app.py``.

Design realized here (design.md Component 9, "Wellington Retention Agent")
--------------------------------------------------------------------------
The correctness-critical guarantees — the eligibility guard (a non-at-risk
member is rejected *before any search*, Req 8.2/8.12) and the anti-fabrication
guarantee (every recommended event is bound to a real returned
``Web_Search_Result`` by the pure ``verify_recommendation``, Req 8.6) — live in
``io/wellington_agent.generate_recommendation`` and the pure core it calls. This
route's only jobs are:

1. **Build the MemberContext the agent needs — with an at_risk flag consistent
   with the dashboard.** The agent's eligibility guard reads
   ``member_context.at_risk``; to keep that decision identical to what the
   dashboard/query routes show, this route derives ``at_risk`` (and the
   ``health_score``) through the *same* pure ``core/dashboard._build_row``
   derivation those routes use (seeding the last persisted score as the
   ``previous`` value so a ``StaleRetained`` recomputation preserves it, Req 4.3,
   and classifying against the configured threshold with the injected clock's
   ``current_date``). There is no stored interests/location column, so
   ``interests`` is empty and ``location`` is ``None`` — the agent handles that.

2. **Invoke the agent with injected (optional) backends.** ``generate_recommendation``
   defaults to ``NotConfiguredSearchBackend`` / ``NotConfiguredSynthesizer`` when
   no backend is supplied; because this environment has no live search or LLM
   configured, the not-configured search backend raises and the outcome is an
   ``Error(reason="search_failed")``. This route exposes two DI seams —
   :func:`get_search_backend` and :func:`get_recommendation_synthesizer`
   (both defaulting to ``None``) — so a real adapter (or a mock, in tests) can be
   injected later without touching this handler.

3. **Persist then map the single :data:`RetentionOutcome` to a JSON response
   (Req 8.4).** Exactly one of three shapes is returned per generate request:

       {outcome: "recommendation", mode, diagnosis, events, outreach_template}
       {outcome: "no_events",      message}
       {outcome: "error",          reason, message}

   On a :class:`Recommendation` outcome the verified recommendation is persisted
   *first* via ``repo.save_recommendation`` (so a subsequent GET / mark-sent sees
   it), then serialized. Per the design's Error-Handling table an ``Error`` whose
   ``reason`` is ``"not_eligible"`` maps to HTTP ``409`` (Req 8.2/8.12); every
   other error is a normal ``200`` error outcome (Req 8.8).
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from member_tracker.api.app import get_clock, get_repository
from member_tracker.api.routes_members import DEFAULT_SCORING_WEIGHTS
from member_tracker.api.schemas import ErrorResponse
from member_tracker.core.clock import Clock
from member_tracker.core.dashboard import _build_row
from member_tracker.core.types import (
    Error,
    MemberContext,
    MemberView,
    NoEvents,
    Recommendation,
    Recommended_Event,
    Retention_Recommendation,
    ScoringConfig,
)
from member_tracker.io.repository import Repository, StoredRecommendation
from member_tracker.io.wellington_agent import generate_recommendation

router = APIRouter(prefix="/api", tags=["recommendations"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class RecommendedEventResponse(BaseModel):
    """A single recommended event, mirroring a verified :class:`Recommended_Event`.

    Carries the event's ``title``, ``event_date`` (serialized as an ISO
    ``YYYY-MM-DD`` string), source ``url``, and the fit ``reason`` — each event
    is bound to a real returned ``Web_Search_Result`` (Req 8.6).
    """

    title: str
    event_date: str
    url: str
    reason: str


class RecommendationResponse(BaseModel):
    """The single outcome of a generate request (Req 8.4).

    Exactly one shape per request:

    * ``outcome == "recommendation"`` — ``mode`` (``"llm"`` | ``"template"``,
      Req 8.11) + ``diagnosis`` + ``events`` (1..3 verified) + ``outreach_template``;
      ``message`` / ``reason`` are ``None``.
    * ``outcome == "no_events"`` — a ``message`` and zero ``events`` (Req 8.7).
    * ``outcome == "error"`` — a ``reason`` + ``message`` and zero ``events``
      (Req 8.2 ineligible, or 8.8 search/LLM failure/timeout).
    """

    outcome: str
    mode: Optional[str] = None
    diagnosis: Optional[str] = None
    events: List[RecommendedEventResponse] = Field(default_factory=list)
    outreach_template: Optional[str] = None
    message: Optional[str] = None
    reason: Optional[str] = None


class StoredRecommendationResponse(BaseModel):
    """A stored recommendation viewed via ``GET`` (Req 8.9, 8.10).

    Mirrors :class:`RecommendationResponse`'s recommendation shape and adds the
    persistence-only fields the UI needs: ``created_at`` (UTC ISO 8601) and the
    sent status (``sent`` / ``sent_at``) so the member record can surface whether
    the recommendation has been sent (Req 8.10).
    """

    outcome: str = "recommendation"
    mode: Optional[str] = None
    diagnosis: Optional[str] = None
    events: List[RecommendedEventResponse] = Field(default_factory=list)
    outreach_template: Optional[str] = None
    created_at: Optional[str] = None
    sent: bool = False
    sent_at: Optional[str] = None


class MarkSentResponse(BaseModel):
    """Result of marking a recommendation as sent (Req 8.10)."""

    member_id: str
    sent: bool = True
    sent_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Backend DI seams
# ---------------------------------------------------------------------------


def get_search_backend() -> Optional[object]:
    """Return the Web Search Tool backend to inject into ``generate_recommendation``.

    Defaults to ``None`` so the agent uses its ``NotConfiguredSearchBackend``
    default. In this environment no live search is configured, so the
    not-configured backend raises and the outcome is
    ``Error(reason="search_failed")``. A real search adapter (or a mock, in
    tests) can be supplied by overriding this dependency via
    ``app.dependency_overrides[get_search_backend]``.
    """
    return None


def get_recommendation_synthesizer() -> Optional[object]:
    """Return the LLM synthesizer to inject into ``generate_recommendation``.

    Returns a :class:`GeminiRecommendationSynthesizer` so at-risk retention
    advice is drafted by Gemini (Wellington-the-Wise persona) when a
    ``GEMINI_API_KEY`` is configured. The synthesizer itself raises
    ``LLMSynthesisNotConfigured`` if no key is set, so the agent transparently
    falls back to the deterministic template assembler (Req 8.11). A mock can
    still be supplied in tests via
    ``app.dependency_overrides[get_recommendation_synthesizer]``.
    """
    try:
        from member_tracker.io.gemini_synthesizer import (
            GeminiRecommendationSynthesizer,
        )

        return GeminiRecommendationSynthesizer()
    except Exception:  # pragma: no cover - import guard
        return None


# ---------------------------------------------------------------------------
# Member-context projection (shared with the dashboard derivation)
# ---------------------------------------------------------------------------


def _scoring_config(repo: Repository) -> ScoringConfig:
    """Build a :class:`ScoringConfig` from live CONFIG + the fixed weights.

    Identical to the dashboard/members/query derivation: milestones and the
    at-risk threshold come from the persisted CONFIG store; the aggregation
    weights are the shared :data:`DEFAULT_SCORING_WEIGHTS`, so the ``at_risk``
    flag this route feeds the agent matches what the dashboard shows.
    """
    config = repo.get_config()
    return ScoringConfig(
        attendance_weight=DEFAULT_SCORING_WEIGHTS["attendance_weight"],
        recency_weight=DEFAULT_SCORING_WEIGHTS["recency_weight"],
        stage_weight=DEFAULT_SCORING_WEIGHTS["stage_weight"],
        activity_weight=DEFAULT_SCORING_WEIGHTS.get("activity_weight", 0.0),
        at_risk_threshold=config.at_risk_threshold,
        milestones=list(config.milestones),
    )


def _load_member_context(
    repo: Repository, clock: Clock, member_id: str
) -> Optional[MemberContext]:
    """Read the live member + attendance and project to a :class:`MemberContext`.

    Returns ``None`` when the member does not exist (the caller maps that to a
    404). Otherwise derives ``at_risk`` / ``health_score`` through the *same*
    pure ``core/dashboard._build_row`` the dashboard route uses (seeding the last
    persisted score as ``previous`` so a ``StaleRetained`` recomputation
    preserves it, Req 4.3, and classifying against the configured threshold with
    the injected clock's ``current_date``). ``interests`` is empty and
    ``location`` is ``None`` because there is no stored column for either — the
    agent handles that.
    """
    member = repo.get_member(member_id)
    if member is None:
        return None

    config = _scoring_config(repo)
    current_date = clock.current_date()

    attendance = repo.list_attendance(member_id)
    existing = repo.get_health_score(member_id)
    previous_score = existing.score if existing is not None else None

    member_view = MemberView(
        member_id=int(member.id),
        name=member.name,
        stage=member.stage,
        age_out_date=member.age_out_date,
        health_score=previous_score,
    )

    row = _build_row(member_view, attendance, config, current_date)

    return MemberContext(
        member_id=int(member.id),
        name=member.name,
        stage=member.stage,
        interests=[],
        health_score=(row.health_score if row.health_score is not None else 0),
        at_risk=row.at_risk,
        location=None,
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _events_to_response(
    events: List[Recommended_Event],
) -> List[RecommendedEventResponse]:
    """Serialize verified :class:`Recommended_Event` entries to the wire model.

    ``event_date`` is rendered as an ISO ``YYYY-MM-DD`` string; order is
    preserved.
    """
    return [
        RecommendedEventResponse(
            title=event.title,
            event_date=event.event_date.isoformat(),
            url=event.url,
            reason=event.reason,
        )
        for event in events
    ]


def _recommendation_to_response(
    recommendation: Retention_Recommendation,
) -> RecommendationResponse:
    """Serialize a verified :class:`Retention_Recommendation` (Req 8.5, 8.11)."""
    return RecommendationResponse(
        outcome="recommendation",
        mode=recommendation.mode,
        diagnosis=recommendation.diagnosis,
        events=_events_to_response(recommendation.events),
        outreach_template=recommendation.outreach_template,
    )


def _stored_to_response(
    stored: StoredRecommendation,
) -> StoredRecommendationResponse:
    """Serialize a persisted :class:`StoredRecommendation` for the GET route."""
    rec = stored.recommendation
    return StoredRecommendationResponse(
        outcome="recommendation",
        mode=rec.mode,
        diagnosis=rec.diagnosis,
        events=_events_to_response(rec.events),
        outreach_template=rec.outreach_template,
        created_at=stored.created_at,
        sent=stored.sent,
        sent_at=stored.sent_at,
    )


def _not_found(code: str, message: str) -> HTTPException:
    """Build a 404 with the shared :class:`ErrorResponse` envelope."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=ErrorResponse(code=code, message=message).model_dump(),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/members/{member_id}/recommendations",
    response_model=RecommendationResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": RecommendationResponse}},
)
def generate_member_recommendation(
    member_id: str,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
    search_backend: Optional[object] = Depends(get_search_backend),
    synthesizer: Optional[object] = Depends(get_recommendation_synthesizer),
) -> RecommendationResponse:
    """Generate a retention recommendation for a member (Req 8.2, 8.4–8.8, 8.11).

    Builds the member's :class:`MemberContext` (deriving ``at_risk`` /
    ``health_score`` through the same pure dashboard derivation the dashboard
    shows) and calls the Wellington Retention Agent with the injected clock's
    ``current_date`` and the (optionally overridden) search/synthesizer backends.
    The single :data:`RetentionOutcome` is mapped to the JSON response (Req 8.4):

    * :class:`Recommendation` -> persist via ``repo.save_recommendation`` (so a
      later GET / mark-sent sees it), then return ``{outcome: "recommendation",
      mode, diagnosis, events, outreach_template}``. HTTP 200.
    * :class:`NoEvents` -> ``{outcome: "no_events", message}``. HTTP 200 (Req 8.7).
    * :class:`Error` -> ``{outcome: "error", reason, message}``; HTTP ``409`` when
      ``reason == "not_eligible"`` (Req 8.2/8.12), otherwise HTTP ``200`` (Req 8.8).

    An unknown member id maps to ``404`` before any agent work.
    """
    context = _load_member_context(repo, clock, member_id)
    if context is None:
        raise _not_found(
            "member_not_found", f"No member exists with id {member_id!r}."
        )

    outcome = generate_recommendation(
        context,
        request_date=clock.current_date(),
        search_backend=search_backend,
        synthesizer=synthesizer,
    )

    if isinstance(outcome, Recommendation):
        # Persist FIRST so a subsequent GET / mark-sent observes the fresh
        # recommendation (Req 8.9), then serialize (Req 8.5, 8.11).
        repo.save_recommendation(
            member_id,
            outcome.recommendation,
            created_at=clock.current_time(),
            mode=outcome.recommendation.mode,
        )
        return _recommendation_to_response(outcome.recommendation)

    if isinstance(outcome, NoEvents):
        return RecommendationResponse(
            outcome="no_events",
            message=(
                "No upcoming local events were found for this member in the "
                "next 30 days."
            ),
        )

    if isinstance(outcome, Error):
        response = RecommendationResponse(
            outcome="error",
            reason=outcome.reason,
            message=outcome.message,
        )
        if outcome.reason == "not_eligible":
            # Per the design Error-Handling table: an ineligible (non-at-risk)
            # member is a 409 with the error outcome body (Req 8.2, 8.12).
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=response.model_dump(),
            )
        # Any other error (search/LLM failure or deadline breach) is a normal
        # 200 error outcome (Req 8.8).
        return response

    # Defensive: RetentionOutcome is a closed union of the three cases above.
    raise TypeError(f"unexpected RetentionOutcome variant: {outcome!r}")  # pragma: no cover


@router.get(
    "/members/{member_id}/recommendations",
    response_model=StoredRecommendationResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_member_recommendation(
    member_id: str,
    repo: Repository = Depends(get_repository),
) -> StoredRecommendationResponse:
    """Return a member's stored retention recommendation (Req 8.9, 8.10).

    Reads the persisted recommendation via ``repo.get_recommendation``. A member
    with no stored recommendation maps to ``404``. On success returns the
    recommendation (diagnosis, its 1–3 verified events, outreach template, and
    ``mode``) together with ``created_at`` and the sent status (``sent`` /
    ``sent_at``) so the UI can render it alongside the copy-outreach and
    mark-as-sent actions (Req 8.9, 8.10).
    """
    stored = repo.get_recommendation(member_id)
    if stored is None:
        raise _not_found(
            "recommendation_not_found",
            f"No retention recommendation exists for member {member_id!r}.",
        )
    return _stored_to_response(stored)


@router.post(
    "/members/{member_id}/recommendations/sent",
    response_model=MarkSentResponse,
    responses={404: {"model": ErrorResponse}},
)
def mark_member_recommendation_sent(
    member_id: str,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> MarkSentResponse:
    """Mark a member's stored recommendation as sent (Req 8.10).

    Delegates to ``repo.mark_recommendation_sent`` with the injected clock's
    ``current_time`` as the UTC ``sent_at``. A member with no stored
    recommendation surfaces a structured ``recommendation_not_found`` error which
    maps to ``404`` (state left unchanged). On success returns the member id and
    the recorded sent flag + timestamp.
    """
    from member_tracker.core.types import Err

    result = repo.mark_recommendation_sent(member_id, sent_at=clock.current_time())
    if isinstance(result, Err):
        error = result.error
        raise _not_found(error.code, error.message)

    stored = result.value
    return MarkSentResponse(
        member_id=member_id,
        sent=stored.sent,
        sent_at=stored.sent_at,
    )


__all__ = ["router"]
