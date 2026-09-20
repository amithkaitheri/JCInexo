"""routes_query.py — the natural-language query route (task 15.5, Requirement 7).

This module wires the Query Interpreter (``io/query_interpreter.interpret_query``)
into HTTP as a single endpoint:

    POST /api/query    interpret a natural-language query and return its outcome
                       (Req 7.1, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8)

Router wiring note: ``api/app.py``'s ``create_app`` already best-effort includes
``member_tracker.api.routes_query.router`` (inside a ``try/except ImportError``),
so this module only needs to expose an ``APIRouter`` named ``router``. It does
**not** edit ``app.py``.

Design realized here
--------------------
The correctness-critical guarantee — that a query result is always a *subset* of
the real stored members and never fabricates records or fields — lives in the
pure ``evaluate_criteria`` (``core/criteria.py``) that ``interpret_query`` calls.
This route's only jobs are:

1. **Build the member projection the interpreter needs.** ``interpret_query``
   consumes ``List[CriteriaMemberRow]`` — the same six-field projection the
   dashboard derives per member. This route reads the live members + attendance
   from the :class:`Repository` and projects each member into a
   :class:`CriteriaMemberRow` using the *same* derivation the dashboard route
   uses (``core/dashboard._build_row`` composed with
   ``core/criteria.criteria_row_from_dashboard_row``), including the shared
   :data:`~member_tracker.api.routes_members.DEFAULT_SCORING_WEIGHTS`. This keeps
   the query projection consistent with what the dashboard shows: the persisted
   health score is seeded as the ``previous`` value (so a ``StaleRetained``
   recomputation preserves it, Req 4.3), the age-out status is derived with the
   injected clock's ``current_date`` and collapsed to its canonical token, and
   ``at_risk`` uses the configured threshold.

2. **Invoke the interpreter with no translator.** ``interpret_query`` defaults to
   :class:`NotConfiguredTranslator` when no ``translator`` is supplied; because
   this environment has no LLM configured, that routes queries through the
   deterministic fallback parser (task 15.10). The route exposes a
   :func:`get_query_translator` DI seam (defaulting to ``None``) so a real LLM
   adapter — or a mock, in tests — can be injected later without touching this
   handler.

3. **Map the single :data:`QueryOutcome` to a JSON response.** Exactly one of
   three shapes is returned (Req 7.7):

       {outcome: "result",        interpreted_criteria, members, source}
       {outcome: "clarification", message, members: []}
       {outcome: "timeout",       message, members: []}

   The ``interpreted_criteria`` are serialized as the list of conditions
   (``field`` / ``comparison`` / ``value``) plus the ``combinator`` so the UI can
   display exactly which attributes were applied (Req 7.3). ``source``
   (``"llm"`` | ``"deterministic_fallback"``) is surfaced on the result outcome
   (task 15.1/15.10 put it on ``ResultSet``; task 15.13 surfaces it in the UI).

Validation (Req 7.6): the request ``query`` is bounded ``1..1000`` characters at
the schema layer, so a query longer than 1000 chars is rejected with ``422``
before the interpreter runs. An *empty/whitespace* query is **not** a 422: it is
1 character or more only if the client sends whitespace, and ``interpret_query``
short-circuits any empty/whitespace input to a ``clarification`` outcome without
calling the interpreter backend (Req 7.6). The ``min_length=1`` bound only
rejects a genuinely empty string (``""``), which carries no query at all.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from member_tracker.api.app import get_clock, get_repository
from member_tracker.api.routes_members import DEFAULT_SCORING_WEIGHTS
from member_tracker.core.clock import Clock
from member_tracker.core.criteria import (
    CriteriaMemberRow,
    Interpreted_Criteria,
    criteria_row_from_dashboard_row,
)
from member_tracker.core.dashboard import _build_row
from member_tracker.core.attendance import attended_count
from member_tracker.core.types import MemberView, ScoringConfig
from member_tracker.io.query_interpreter import (
    Clarification,
    ResultSet,
    Timeout,
    interpret_query,
)
from member_tracker.io.repository import Repository

router = APIRouter(prefix="/api", tags=["query"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    """Body for ``POST /api/query`` (Req 7.1, 7.6).

    ``query`` is the raw natural-language question. It is bounded ``1..1000``
    characters at the shape layer: a query longer than 1000 characters is
    rejected with ``422`` before interpretation. A genuinely empty string is
    rejected too (``min_length=1``); a whitespace-only query passes the shape
    check and is short-circuited to a ``clarification`` outcome by
    ``interpret_query`` (Req 7.6).
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=1000)


class ConditionResponse(BaseModel):
    """One serialized criteria condition (Req 7.3).

    Surfaces the ``field``, ``comparison``, and ``value`` the interpreter applied
    so the UI can show exactly which member attributes were matched.
    """

    field: str
    comparison: str
    value: object = None


class InterpretedCriteriaResponse(BaseModel):
    """The serialized ``Interpreted_Criteria`` applied to produce a result (Req 7.3)."""

    conditions: List[ConditionResponse] = Field(default_factory=list)
    combinator: str = "and"


class QueryMemberResponse(BaseModel):
    """A single matched member, mirroring the :class:`CriteriaMemberRow` projection.

    Carries exactly the six schema-bounded fields the query language reads — the
    same projection the dashboard derives — so the members surfaced are a subset
    of the stored members with only stored field values (Req 7.1, 7.4).
    """

    name: str
    stage: str
    age_out_status: str
    attendance_count: int
    health_score: Optional[int] = None
    at_risk: bool = False


class QueryResponse(BaseModel):
    """The single outcome of a natural-language query (Req 7.7).

    Exactly one shape per query:

    * ``outcome == "result"`` — ``interpreted_criteria`` + ``members`` (possibly
      empty, Req 7.4) + ``source`` (``"llm"`` | ``"deterministic_fallback"``);
      ``message`` is ``None``.
    * ``outcome == "clarification"`` — a ``message`` and zero ``members``
      (Req 7.5, 7.6); ``interpreted_criteria`` / ``source`` are ``None``.
    * ``outcome == "timeout"`` — a ``message`` and zero ``members`` (Req 7.8);
      ``interpreted_criteria`` / ``source`` are ``None``.
    """

    outcome: str
    members: List[QueryMemberResponse] = Field(default_factory=list)
    interpreted_criteria: Optional[InterpretedCriteriaResponse] = None
    message: Optional[str] = None
    source: Optional[str] = None


# ---------------------------------------------------------------------------
# Translator DI seam
# ---------------------------------------------------------------------------


def get_query_translator() -> Optional[object]:
    """Return the NL->criteria translator to inject into ``interpret_query``.

    Defaults to ``None`` so ``interpret_query`` uses its
    :class:`NotConfiguredTranslator` default. In this environment no LLM is
    configured, so queries route through the deterministic fallback parser and
    successful results are tagged ``source = "deterministic_fallback"``. A real
    LLM adapter (or a mock, in tests) can be supplied by overriding this
    dependency via ``app.dependency_overrides[get_query_translator]``.
    """
    return None


# ---------------------------------------------------------------------------
# Member-row projection (shared with the dashboard derivation)
# ---------------------------------------------------------------------------


def _scoring_config(repo: Repository) -> ScoringConfig:
    """Build a :class:`ScoringConfig` from live CONFIG + the fixed weights.

    Identical to the dashboard/members derivation: milestones and the at-risk
    threshold come from the persisted CONFIG store; the aggregation weights are
    the shared :data:`DEFAULT_SCORING_WEIGHTS`, so the query projection's health
    score / at-risk classification matches what the dashboard shows.
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


def _load_criteria_member_rows(
    repo: Repository, clock: Clock
) -> List[CriteriaMemberRow]:
    """Read the live members + attendance and project to :class:`CriteriaMemberRow`.

    Uses the *same* per-member derivation as the dashboard route: build a
    :class:`MemberView` seeding the last persisted score as ``previous`` (so a
    ``StaleRetained`` recomputation preserves it, Req 4.3), assemble the
    :class:`DashboardRow` with the pure ``_build_row`` (age-out status, attendance
    progress, health score, at-risk — all against the injected ``current_date``
    and the shared scoring config), then collapse it to the six-field
    :class:`CriteriaMemberRow` the interpreter consumes via
    ``criteria_row_from_dashboard_row`` (carrying the explicit attendance count).

    Building the rows through the shared dashboard derivation keeps the query
    projection consistent with what the dashboard shows and preserves the
    subset / non-fabrication guarantee end to end: ``interpret_query`` selects
    only from these real rows.
    """
    config = _scoring_config(repo)
    current_date = clock.current_date()

    rows: List[CriteriaMemberRow] = []
    for record in repo.list_members():
        attendance = repo.list_attendance(record.id)

        existing = repo.get_health_score(record.id)
        previous_score = existing.score if existing is not None else None

        member_view = MemberView(
            member_id=int(record.id),
            name=record.name,
            stage=record.stage,
            age_out_date=record.age_out_date,
            health_score=previous_score,
        )

        dashboard_row = _build_row(member_view, attendance, config, current_date)
        rows.append(
            criteria_row_from_dashboard_row(
                dashboard_row, attended_count(attendance)
            )
        )

    return rows


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _criteria_to_response(
    criteria: Interpreted_Criteria,
) -> InterpretedCriteriaResponse:
    """Serialize :class:`Interpreted_Criteria` into its wire model (Req 7.3)."""
    return InterpretedCriteriaResponse(
        conditions=[
            ConditionResponse(
                field=condition.field,
                comparison=condition.comparison,
                value=condition.value,
            )
            for condition in criteria.conditions
        ],
        combinator=criteria.combinator,
    )


def _member_to_response(member: CriteriaMemberRow) -> QueryMemberResponse:
    """Project a :class:`CriteriaMemberRow` onto the wire model."""
    return QueryMemberResponse(
        name=member.name,
        stage=member.stage,
        age_out_status=member.age_out_status,
        attendance_count=member.attendance_count,
        health_score=member.health_score,
        at_risk=member.at_risk,
    )


# ---------------------------------------------------------------------------
# Route (Req 7.1, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8)
# ---------------------------------------------------------------------------


@router.post("/query", response_model=QueryResponse)
def run_query(
    body: QueryRequest,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
    translator: Optional[object] = Depends(get_query_translator),
) -> QueryResponse:
    """Interpret a natural-language query and return exactly one outcome.

    Reads the live members + attendance, projects them into the
    :class:`CriteriaMemberRow` list the interpreter consumes (the same derivation
    the dashboard uses), and calls ``interpret_query`` with the injected clock's
    ``current_date`` and the (optionally overridden) translator. The single
    :data:`QueryOutcome` is mapped to the JSON response (Req 7.7):

    * :class:`ResultSet` -> ``{outcome: "result", interpreted_criteria, members,
      source}`` — ``members`` may be empty (Req 7.4).
    * :class:`Clarification` -> ``{outcome: "clarification", message,
      members: []}`` (Req 7.5, 7.6).
    * :class:`Timeout` -> ``{outcome: "timeout", message, members: []}`` (Req 7.8).

    A query longer than 1000 characters is rejected with ``422`` at the schema
    layer; an empty/whitespace query yields a ``clarification`` (handled inside
    ``interpret_query``, Req 7.6).
    """
    members = _load_criteria_member_rows(repo, clock)

    outcome = interpret_query(
        body.query,
        members,
        current_date=clock.current_date(),
        translator=translator,
    )

    if isinstance(outcome, ResultSet):
        return QueryResponse(
            outcome="result",
            interpreted_criteria=_criteria_to_response(outcome.criteria),
            members=[_member_to_response(m) for m in outcome.members],
            source=outcome.source,
        )

    if isinstance(outcome, Clarification):
        return QueryResponse(
            outcome="clarification",
            message=outcome.message,
            members=[],
        )

    if isinstance(outcome, Timeout):
        return QueryResponse(
            outcome="timeout",
            message=outcome.message,
            members=[],
        )

    # Defensive: QueryOutcome is a closed union of the three cases above.
    raise TypeError(f"unexpected QueryOutcome variant: {outcome!r}")  # pragma: no cover


__all__ = ["router"]
