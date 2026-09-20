"""Recommendation Verifier and template assembler (pure).

Component 9 of design.md ("Wellington Retention Agent, Web Search Tool, and
Recommendation Verifier", Requirement 8). This module implements the *pure,
deterministic* correctness core of the AI-powered member retention capability:
the anti-fabrication ``verify_recommendation`` (task 16.1) and — added later —
the template fallback assembler ``build_templated_recommendation`` (task 16.4).

Just like ``evaluate_criteria`` for Requirement 7, this module isolates the
correctness-critical guarantee from the non-deterministic parts. The live web
search and the LLM synthesis live in I/O adapters (the Web Search Tool and the
Wellington Retention Agent); the guarantee that *every recommended event is
real, distinct, in-window, and never fabricated* lives here, in a pure function
that can only ever bless events drawn from the search-result set it is handed.
Because it is pure and deterministic, that guarantee is directly provable by
property-based testing (Properties 27–28, tasks 16.2–16.3).

The verifier is the trust boundary (design.md Component 9)
----------------------------------------------------------
``verify_recommendation`` receives the ``Web_Search_Result`` set as an explicit
argument. It constructs no events: an event is blessed only if its
``(title, event_date, url)`` triple matches one of the returned results, that
result lies within ``[request_date, request_date + 30d]``, and no two accepted
events map to the same returned result. Fabricated, out-of-window, and
non-distinct candidate events are pruned. It performs no I/O and reads no
wall clock — ``request_date`` is passed in — so it is fully deterministic.

Value-object shapes (defined in :mod:`member_tracker.core.types`)
-----------------------------------------------------------------
* ``Web_Search_Result`` — ``{title: str, event_date: date, url: str,
  snippet: Optional[str]}``. The only source of real event facts. Identity for
  matching is the ``(title, event_date, url)`` triple; ``snippet`` is never an
  identity key.
* ``Recommended_Event`` — ``{title: str, event_date: date, url: str,
  reason: str}``. A proposed event; verified by matching its
  ``(title, event_date, url)`` against a ``Web_Search_Result``.
* ``Retention_Recommendation`` — ``{diagnosis: str,
  events: List[Recommended_Event], outreach_template: str, mode: str = "llm"}``.
  Exactly three parts: non-empty ``diagnosis``, 1..3 verified ``events``, and a
  non-empty ``outreach_template``. ``mode`` is ``"llm"`` (synthesis path) or
  ``"template"`` (fallback path, task 16.4).
* ``MemberContext`` — ``{member_id: int, name: str, stage: MembershipStage,
  interests: List[str], health_score: int, at_risk: bool,
  location: Optional[str]}``. Safe stored context (used by 16.4's assembler).
* ``VerificationError`` — ``{code: str, message: str}``. Structured failure.
* ``RetentionOutcome`` — ``Recommendation | NoEvents | Error{message, reason}``.
  The agent's single sum-typed outcome (used by the I/O orchestrator, task 18).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Set, Tuple

from member_tracker.core.types import (
    Err,
    MemberContext,
    Ok,
    Recommended_Event,
    Result,
    Retention_Recommendation,
    VerificationError,
    Web_Search_Result,
    err,
    ok,
)

# The recommendation window: an accepted event's date must lie within
# ``[request_date, request_date + WINDOW_DAYS]`` inclusive (Req 8.6, design.md).
WINDOW_DAYS = 30

# Minimum / maximum number of events a verified recommendation may carry
# (Req 8.5). Fewer than the minimum after pruning is a verification failure;
# more than the maximum in the candidate is rejected outright.
MIN_EVENTS = 1
MAX_EVENTS = 3


# ---------------------------------------------------------------------------
# verify_recommendation (task 16.1)
# ---------------------------------------------------------------------------


def _event_key(title: str, event_date: date, url: str) -> Tuple[str, date, str]:
    """The identity triple used to bind a recommended event to a search result.

    ``snippet`` is intentionally excluded — it is supporting text, not identity.
    """
    return (title, event_date, url)


def verify_recommendation(
    candidate: Retention_Recommendation,
    search_results: List[Web_Search_Result],
    request_date: date,
) -> Result[Retention_Recommendation, VerificationError]:
    """Verify a candidate recommendation against the real search-result set.

    PURE and deterministic — no I/O, no wall-clock read (``request_date`` is
    supplied). This is the anti-fabrication trust boundary (design.md
    Component 9, Req 8.5–8.6).

    Verification rules:

    (a) **Non-fabrication.** Each :class:`Recommended_Event`'s
        ``(title, event_date, url)`` must match some entry in ``search_results``.
        Events with no match are *fabricated* and are pruned.
    (b) **In-window.** Each accepted event's date must lie within
        ``[request_date, request_date + 30d]`` inclusive. Out-of-window events
        are pruned. (A window guard is also applied to the backing search
        result, so a result outside the window can never bless an event.)
    (c) **Distinctness.** Each accepted event must map to a *distinct*
        :class:`Web_Search_Result`. If two candidate events would bind to the
        same result, only the first is accepted; the later duplicate is pruned.
    (d) **Count.** The candidate must carry at most :data:`MAX_EVENTS` (3)
        events; a candidate exceeding that is rejected outright. After pruning,
        at least :data:`MIN_EVENTS` (1) valid event must remain.
    (e) **Exactly three parts.** ``diagnosis`` and ``outreach_template`` must be
        non-empty (after trimming) and there must be a non-empty ``events`` list.

    On success, returns ``Ok`` carrying a verified :class:`Retention_Recommendation`
    whose ``events`` are exactly the accepted (pruned) subset, in candidate
    order, preserving the candidate's ``diagnosis``, ``outreach_template`` and
    ``mode``. On any failure that cannot be repaired down to a valid subset,
    returns ``Err`` with a :class:`VerificationError`.
    """
    # (e) Structural parts must be present and non-empty. Missing prose cannot
    # be repaired, so fail fast (Req 8.5).
    if not candidate.diagnosis or not candidate.diagnosis.strip():
        return err(
            VerificationError(
                code="diagnosis_missing",
                message="Recommendation diagnosis must be non-empty.",
            )
        )
    if not candidate.outreach_template or not candidate.outreach_template.strip():
        return err(
            VerificationError(
                code="outreach_missing",
                message="Recommendation outreach_template must be non-empty.",
            )
        )

    candidate_events = candidate.events or []

    # (d) Count upper bound is on the *candidate*: a candidate that proposes
    # more than three events is rejected outright rather than silently trimmed
    # (Req 8.5).
    if len(candidate_events) > MAX_EVENTS:
        return err(
            VerificationError(
                code="too_many_events",
                message=(
                    f"Recommendation carries {len(candidate_events)} events; "
                    f"at most {MAX_EVENTS} are allowed."
                ),
            )
        )

    # Build an index from event identity triple -> indices of matching search
    # results. A result is only eligible to back an event if it is itself
    # in-window, enforcing rule (b) at the source (a result outside the window
    # can never bless an event).
    lower = request_date
    upper = request_date + timedelta(days=WINDOW_DAYS)

    result_key_indices: dict[Tuple[str, date, str], List[int]] = {}
    for idx, res in enumerate(search_results):
        if lower <= res.event_date <= upper:
            key = _event_key(res.title, res.event_date, res.url)
            result_key_indices.setdefault(key, []).append(idx)

    accepted: List[Recommended_Event] = []
    used_result_indices: Set[int] = set()

    for event in candidate_events:
        # (b) The event's own date must be in-window. (Its backing result is
        # also guaranteed in-window by construction of the index above.)
        if not (lower <= event.event_date <= upper):
            continue  # out-of-window -> prune

        key = _event_key(event.title, event.event_date, event.url)
        matching_indices = result_key_indices.get(key)
        if not matching_indices:
            continue  # (a) fabricated (no matching search result) -> prune

        # (c) Bind to a DISTINCT search result not already consumed by an
        # earlier accepted event. If every matching result is already used, this
        # event is a non-distinct duplicate -> prune.
        bound_index = next(
            (i for i in matching_indices if i not in used_result_indices), None
        )
        if bound_index is None:
            continue  # non-distinct -> prune

        used_result_indices.add(bound_index)
        accepted.append(event)

    # (d) At least one valid event must remain after pruning (Req 8.5, 8.6).
    if len(accepted) < MIN_EVENTS:
        return err(
            VerificationError(
                code="no_valid_events",
                message=(
                    "No recommended event survived verification "
                    "(fabricated, out-of-window, or non-distinct)."
                ),
            )
        )

    verified = Retention_Recommendation(
        diagnosis=candidate.diagnosis,
        events=accepted,
        outreach_template=candidate.outreach_template,
        mode=candidate.mode,
    )
    return ok(verified)


# ---------------------------------------------------------------------------
# build_templated_recommendation (task 16.4)
# ---------------------------------------------------------------------------


def build_templated_recommendation(
    member_context: MemberContext,
    verified_events: List[Recommended_Event],
    request_date: date,
) -> Retention_Recommendation:
    """Assemble a fixed-template retention recommendation (fallback path).

    PURE and deterministic — no I/O, no LLM, no wall-clock read (``request_date``
    is supplied). This is the template fallback assembler of design.md
    Component 9 (Req 8.11): the branch taken when a web search returned >=1
    result and the events were already blessed by :func:`verify_recommendation`,
    but the LLM was unavailable to synthesize the prose.

    It authors **no event facts** and reaches no backend. ``verified_events`` is
    the already-verified 1..3 :class:`Recommended_Event` list (distinct, real,
    in-window per :func:`verify_recommendation`); this function attaches those
    entries *unchanged* and wraps them in FIXED diagnosis and outreach text
    templates parameterized only by SAFE stored member context — the member's
    ``name`` and ``stage`` and the at-risk framing. It invents no member-specific
    facts beyond that safe framing.

    The resulting :class:`Retention_Recommendation` therefore carries the same
    exactly-three-parts, 1..3-real-in-window-events structure as an
    LLM-synthesized one (Req 8.11) — non-empty ``diagnosis``, the passed
    ``events``, and a non-empty ``outreach_template`` — so it would itself pass
    :func:`verify_recommendation`'s structural checks against the same search
    results the events came from. ``mode`` is set to ``"template"`` so the UI can
    surface a fallback-mode indicator (versus ``"llm"`` on the synthesis path).

    Determinism: the produced prose depends only on ``member_context.name``,
    ``member_context.stage`` and the fixed template text (``request_date`` is used
    only to phrase the standing "next 30 days" window, matching
    :data:`WINDOW_DAYS`). Same inputs -> identical output.
    """
    # Safe stored context only — never any invented member-specific fact.
    name = member_context.name
    stage = member_context.stage.value

    # FIXED diagnosis template, parameterized only by name + stage + the
    # at-risk framing. No event facts are authored here.
    diagnosis = (
        f"{name}'s health score has fallen to or below the at-risk threshold "
        f"for a member at the {stage} stage. To help re-engage {name}, we have "
        f"gathered a short list of upcoming local events that fit their "
        f"interests and stage. Reaching out with a personal invitation is a "
        f"strong first step toward renewing their involvement."
    )

    # FIXED outreach template — a friendly, ready-to-send invitation the admin
    # can use. It references the recommended events generically (the concrete
    # event facts live in ``events`` unchanged) and the standing 30-day window.
    outreach_template = (
        f"Hi {name},\n\n"
        f"We've missed seeing you at JCI and wanted to reach out. We think a few "
        f"upcoming events over the next {WINDOW_DAYS} days would be a great fit "
        f"for you, and we'd love for you to join us. Please take a look at the "
        f"events we've recommended below — we'd be delighted to have you there.\n\n"
        f"Warm regards,\nYour JCI Chapter"
    )

    return Retention_Recommendation(
        diagnosis=diagnosis,
        events=verified_events,
        outreach_template=outreach_template,
        mode="template",
    )


__all__ = [
    "WINDOW_DAYS",
    "MIN_EVENTS",
    "MAX_EVENTS",
    "verify_recommendation",
    "build_templated_recommendation",
]
