"""wellington_agent.py — the Wellington Retention Agent (I/O orchestrator, Req 8).

Component 9 of design.md ("Wellington Retention Agent, Web Search Tool, and
Recommendation Verifier"). This module is the *non-deterministic* orchestrator
of the AI-powered member-retention capability ("Wellington the Wise"). It ties
together three collaborators, two of which are non-deterministic I/O and one of
which is the pure correctness core:

* the **Web Search Tool** adapter (``io/web_search_tool.search_events``) — the
  only source of real, live event facts (non-deterministic I/O);
* the **LLM synthesizer** (injected behind the :class:`RecommendationSynthesizer`
  seam) — proposes a candidate :class:`Retention_Recommendation` under the
  "Wellington the Wise" persona (non-deterministic I/O); and
* the **pure verifier** (``core/recommendation.verify_recommendation``) — the
  anti-fabrication trust boundary that can only bless events drawn from the real
  search-result set (pure, deterministic).

The whole loop runs under a 30-second monotonic deadline and returns **exactly
one** :data:`~member_tracker.core.types.RetentionOutcome` per request (Req 8.4,
8.14). Mutual exclusivity is structural: a single sum-typed value is returned.

Where the correctness lives (design.md Component 9)
---------------------------------------------------
Exactly as the Query Interpreter (Req 7) confines the non-fabrication guarantee
to the pure ``evaluate_criteria``, this agent confines the retention
non-fabrication guarantee to the pure ``verify_recommendation``. Whatever the
LLM returns — or whatever the deterministic template fallback selects — is
funnelled through ``verify_recommendation`` against the *real* search results
before it can become a :class:`Recommendation`. The agent itself constructs no
event facts; it only orchestrates.

Outcome map (mirrors the design's Error-Handling table)
-------------------------------------------------------
* **Not at risk** → ``Error(reason="not_eligible")`` *before any search* (Req
  8.2, 8.12). The eligibility guard is a cost control and a testable invariant:
  for a non-at-risk member the search tool is never invoked.
* **Search fails** (:class:`~member_tracker.io.web_search_tool.SearchError`) →
  ``Error(reason="search_failed")``; no recommendation, no events (Req 8.8,
  8.13).
* **Search returns zero results** → ``NoEvents()`` (Req 8.7, 8.13). This holds
  *even when the LLM is unavailable* — the template fallback is NEVER reached on
  a zero-result search.
* **Search returns ≥1 result**:
    - **LLM available and synthesizes in time, verification succeeds** →
      ``Recommendation`` tagged ``mode = "llm"`` (Req 8.5, 8.6).
    - **LLM unavailable / times out / crashes / returns nothing / verification
      of its candidate fails** → deterministic **template fallback**: select up
      to 3 distinct in-window events from the *real* results, run them through
      the SAME ``verify_recommendation``, assemble via
      ``build_templated_recommendation`` → ``Recommendation`` tagged
      ``mode = "template"`` (Req 8.11, 8.14). If, after a ≥1-result search, no
      event is actually in-window (so nothing can be verified without
      fabricating), the fallback yields ``Error(reason="synthesis_failed")`` —
      never a fabricated or empty recommendation (Req 8.6, 8.11).

Dependency injection — the two non-deterministic seams
------------------------------------------------------
Mirroring ``io/query_interpreter.py``'s translator seam, both non-deterministic
collaborators are injected so this module hardcodes **no** provider call:

* ``search_backend`` — a :class:`~member_tracker.io.web_search_tool.SearchBackend`
  (defaults to :class:`~member_tracker.io.web_search_tool.NotConfiguredSearchBackend`,
  which raises, surfacing as ``Error(reason="search_failed")``).
* ``synthesizer`` — a :class:`RecommendationSynthesizer` (defaults to
  :class:`NotConfiguredSynthesizer`, which raises :class:`LLMSynthesisNotConfigured`
  and therefore routes to the deterministic template fallback).

Requirements: 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.11, 8.12, 8.13, 8.14.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import replace
from datetime import date, timedelta
from typing import List, Optional

from member_tracker.core.recommendation import (
    MAX_EVENTS,
    WINDOW_DAYS,
    build_templated_recommendation,
    verify_recommendation,
)
from member_tracker.core.types import (
    Error,
    MemberContext,
    NoEvents,
    Recommendation,
    Recommended_Event,
    Retention_Recommendation,
    RetentionOutcome,
    Web_Search_Result,
)
from member_tracker.io.web_search_tool import (
    NotConfiguredSearchBackend,
    SearchBackend,
    SearchError,
    search_events,
)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Overall monotonic deadline for producing an outcome (Req 8.4/8.14).
DEFAULT_DEADLINE_SECONDS = 30

# Provenance tags for a Retention_Recommendation's `mode` field (Req 8.11).
_MODE_LLM = "llm"
_MODE_TEMPLATE = "template"

# Machine-readable Error.reason tags (design.md Error-Handling table).
_REASON_NOT_ELIGIBLE = "not_eligible"
_REASON_SEARCH_FAILED = "search_failed"
_REASON_TIMEOUT = "timeout"
_REASON_SYNTHESIS_FAILED = "synthesis_failed"

# Human-readable Error messages.
_MSG_NOT_ELIGIBLE = (
    "This member is not currently classified as at risk, so a retention "
    "intervention is not applicable. No web search was performed."
)
_MSG_SEARCH_FAILED = (
    "The event search could not be performed. No recommendation was produced."
)
_MSG_NO_EVENTS = (
    "The event search returned no upcoming local events for this member in the "
    "next 30 days."
)
_MSG_SYNTHESIS_FAILED = (
    "A recommendation could not be produced from the events returned by the "
    "search. No recommendation was produced."
)


# ---------------------------------------------------------------------------
# The LLM-synthesis seam (dependency injection)
# ---------------------------------------------------------------------------

# `typing.Protocol` is only available on 3.8+. Fall back to a plain base class on
# older runtimes so the module imports cleanly everywhere.
try:  # pragma: no cover - import-time capability probe
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class RecommendationSynthesizer(Protocol):
        """The injectable LLM synthesis seam (design.md Component 9).

        An implementation prompts the ``LLM_Backend`` under the "Wellington the
        Wise" persona (see :data:`WELLINGTON_SYSTEM_PROMPT`) and returns a
        candidate :class:`Retention_Recommendation` — a diagnosis, 1..3 chosen
        events (each of which *should* be drawn from ``search_results``), and an
        outreach template. It is *not* trusted directly: whatever it returns is
        passed to the pure ``verify_recommendation`` downstream, which prunes any
        fabricated / out-of-window / non-distinct event. To signal that no LLM is
        configured it may raise :class:`LLMSynthesisNotConfigured`; any failure
        (including that) routes the agent to the deterministic template fallback.
        It must not read the wall clock — ``request_date`` is passed in.
        """

        def synthesize(
            self,
            member: MemberContext,
            search_results: List[Web_Search_Result],
            request_date: date,
        ) -> "Retention_Recommendation":  # pragma: no cover - protocol stub
            ...

except ImportError:  # pragma: no cover - very old Python
    class RecommendationSynthesizer:  # type: ignore[no-redef]
        """Fallback base for the synthesis seam when ``typing.Protocol`` is
        unavailable. See the Protocol version above for the contract."""

        def synthesize(
            self,
            member: MemberContext,
            search_results: List[Web_Search_Result],
            request_date: date,
        ) -> "Retention_Recommendation":
            raise NotImplementedError


class LLMSynthesisNotConfigured(RuntimeError):
    """Raised by :class:`NotConfiguredSynthesizer` when no real LLM backend is
    wired in. Treated by :func:`generate_recommendation` as "LLM unavailable",
    routing the request to the deterministic template fallback (Req 8.11)."""


class NotConfiguredSynthesizer:
    """Default synthesizer used when no LLM backend is injected.

    This environment has no configured LLM. Rather than hardcode a network call
    to a specific provider, the default makes the absence explicit: every
    ``synthesize`` call raises :class:`LLMSynthesisNotConfigured`.
    :func:`generate_recommendation` treats that as "LLM unavailable" and takes
    the deterministic template fallback branch (Req 8.11, 8.14). Wire in a real
    adapter (or a mock, in tests) via the ``synthesizer`` parameter to enable
    LLM synthesis.
    """

    def synthesize(
        self,
        member: MemberContext,
        search_results: List[Web_Search_Result],
        request_date: date,
    ) -> "Retention_Recommendation":
        raise LLMSynthesisNotConfigured(
            "No LLM backend is configured for the Wellington Retention Agent; "
            "inject a RecommendationSynthesizer to enable LLM synthesis."
        )


# ---------------------------------------------------------------------------
# Wellington the Wise — system prompt specification (design.md Component 9)
# ---------------------------------------------------------------------------

WELLINGTON_SYSTEM_PROMPT = """\
You are Wellington the Wise, a seasoned, warm, and encouraging JCI chapter \
mentor. You help re-engage members who are at risk of drifting away.

You will be given a member's context — their name, membership stage, recorded \
interests, and current health score — together with the EXACT list of events \
returned by a web search (each with a title, a date, and a source URL). These \
returned events are the ONLY events you may recommend. You must not invent \
events, dates, or URLs, and you must not alter the title, date, or URL of any \
provided event; choose only from the provided list.

Do the following:
1. Write a brief, empathetic DIAGNOSIS of why this member's health score is \
low, grounded in their stage and engagement context.
2. Select 1 to 3 of the provided events that best fit the member's interests \
or stage. For each selected event, give a one-sentence REASON explaining why it \
fits this member. Copy each event's title, date, and URL verbatim from the \
provided list.
3. Draft a short, personalized OUTREACH_TEMPLATE the administrator can copy and \
send to the member.

Respond with a SINGLE JSON object and no prose outside of it. The object must \
have exactly three keys:
  - "diagnosis": a string.
  - "recommended_events": an array of 1 to 3 objects, each with the keys \
"title", "date", "url", and "reason".
  - "outreach_template": a string.

Note: your output is checked by a downstream verifier. Any recommended event \
that does not exactly match one of the provided search results (by title, date, \
and URL), that falls outside the allowed date window, or that duplicates \
another selected event will be discarded. Recommending only real, provided, \
in-window events keeps your recommendation intact.\
"""


# ---------------------------------------------------------------------------
# LLM synthesis under a monotonic deadline (isolated helper)
# ---------------------------------------------------------------------------


class _DeadlineExceeded(Exception):
    """Internal signal that the synthesizer did not finish within the deadline."""


def _invoke_synthesizer(
    synthesizer,
    member: MemberContext,
    search_results: List[Web_Search_Result],
    request_date: date,
) -> "Retention_Recommendation":
    """Call the synthesizer, accepting either an object with ``.synthesize`` or a
    bare callable with the same signature. Any exception propagates unchanged."""
    if hasattr(synthesizer, "synthesize"):
        synth = synthesizer.synthesize
    elif callable(synthesizer):
        synth = synthesizer
    else:  # pragma: no cover - misuse guard
        raise TypeError(
            "synthesizer must expose a `synthesize(member, search_results, "
            "request_date)` method or be a callable with that signature."
        )
    return synth(member, search_results, request_date)


def _synthesize_with_deadline(
    synthesizer,
    member: MemberContext,
    search_results: List[Web_Search_Result],
    request_date: date,
    deadline_seconds: float,
) -> "Retention_Recommendation":
    """Run the synthesizer under a monotonic-clock deadline.

    The synthesis call is dispatched onto a worker thread and awaited for at
    most ``deadline_seconds`` measured on :func:`time.monotonic`. On breach we
    raise :class:`_DeadlineExceeded`; the caller maps that to the deterministic
    template fallback (Req 8.11, 8.14).

    Cancellation limitation (documented, dependency-light approach): Python
    cannot forcibly kill a running thread, so on a deadline breach we *abandon*
    the worker (attempt ``future.cancel()``, then stop waiting) rather than
    interrupting it. The abandoned synthesis call may keep running in the
    background until it finishes on its own, but its result is never observed or
    returned — the agent has already committed to the fallback branch,
    preserving the exactly-one-outcome guarantee (Req 8.14). The worker thread is
    a daemon so it never blocks process shutdown. This keeps the timeout
    dependency-light (stdlib ``concurrent.futures`` only) at the cost of not
    reclaiming the abandoned thread immediately; a production adapter that needs
    hard cancellation should also push the deadline into the transport (e.g. an
    HTTP client timeout).

    Raises:
        _DeadlineExceeded: the synthesizer did not finish within the deadline.
        Exception: any synthesizer failure (incl. :class:`LLMSynthesisNotConfigured`)
            propagates unchanged for the caller to map to the fallback.
    """
    start = time.monotonic()
    executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="wellington-synth"
    )
    try:
        future: Future = executor.submit(
            _invoke_synthesizer, synthesizer, member, search_results, request_date
        )
        remaining = deadline_seconds - (time.monotonic() - start)
        if remaining <= 0:
            future.cancel()
            raise _DeadlineExceeded()
        try:
            return future.result(timeout=remaining)
        except FutureTimeout:
            future.cancel()
            raise _DeadlineExceeded()
    finally:
        # Do not block on an abandoned worker thread (wait=False); the daemon
        # thread will not prevent process exit.
        executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Deterministic template-fallback event selection (PURE)
# ---------------------------------------------------------------------------

# FIXED, non-fabricating reason attached to a template-selected event. It
# authors NO event facts — it only frames the event generically for the member.
_TEMPLATE_EVENT_REASON = (
    "Selected from upcoming local events in the next 30 days as a good "
    "opportunity to re-engage with the chapter."
)


def _select_events_for_template(
    search_results: List[Web_Search_Result],
    request_date: date,
) -> List[Recommended_Event]:
    """Deterministically select up to :data:`MAX_EVENTS` distinct in-window
    events from the *real* search results (PURE, deterministic; Req 8.11).

    Walks ``search_results`` in returned order, keeping each result whose
    ``event_date`` lies within ``[request_date, request_date + WINDOW_DAYS]``,
    de-duplicating on the ``(title, event_date, url)`` identity triple, and stops
    once :data:`MAX_EVENTS` have been chosen. Each kept result becomes a
    :class:`Recommended_Event` carrying its title/date/url *verbatim* and a
    FIXED :data:`_TEMPLATE_EVENT_REASON` — it invents no event facts. May return
    an empty list when nothing is in-window; the caller then declines to
    fabricate a recommendation.
    """
    lower = request_date
    upper = request_date + timedelta(days=WINDOW_DAYS)

    selected: List[Recommended_Event] = []
    seen = set()
    for res in search_results:
        if not (lower <= res.event_date <= upper):
            continue
        key = (res.title, res.event_date, res.url)
        if key in seen:
            continue
        seen.add(key)
        selected.append(
            Recommended_Event(
                title=res.title,
                event_date=res.event_date,
                url=res.url,
                reason=_TEMPLATE_EVENT_REASON,
            )
        )
        if len(selected) >= MAX_EVENTS:
            break
    return selected


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _with_mode(
    recommendation: Retention_Recommendation, mode: str
) -> Retention_Recommendation:
    """Return a copy of ``recommendation`` with its (frozen) ``mode`` replaced."""
    return replace(recommendation, mode=mode)


# Placeholder prose used only to construct a candidate for the *structural*
# verification pass on the template path. The verified events are then handed to
# the pure `build_templated_recommendation`, which supplies the real fixed
# template diagnosis/outreach text — these placeholders never reach the caller.
_TEMPLATE_CANDIDATE_DIAGNOSIS = "pending template diagnosis"
_TEMPLATE_CANDIDATE_OUTREACH = "pending template outreach"


def _template_fallback(
    member: MemberContext,
    results: List[Web_Search_Result],
    request_date: date,
) -> RetentionOutcome:
    """Assemble a template-mode recommendation from the *real* search results.

    PURE and deterministic (Req 8.11, 8.14). Reached only *after* a successful
    search returned ≥1 result but the LLM was unavailable / timed out / crashed /
    returned nothing, or its candidate failed verification. Because it is only
    reachable after a ≥1-result search, the never-invent-events rule is
    structural: it can only draw on the real returned events.

    Steps:
      1. :func:`_select_events_for_template` — up to 3 distinct in-window events.
         If none are in-window, decline rather than fabricate ->
         ``Error(reason="synthesis_failed")``.
      2. Run the selected events through the SAME pure ``verify_recommendation``
         against the real ``results`` (defense in depth; the events are already
         real+in-window+distinct by construction). On the unexpected chance it
         fails -> ``Error(reason="synthesis_failed")``.
      3. Assemble the fixed-template recommendation via the pure
         ``build_templated_recommendation`` (mode ``"template"``) and return
         ``Recommendation``.
    """
    selected = _select_events_for_template(results, request_date)
    if not selected:
        # A ≥1-result search whose events are all out-of-window: we will not
        # fabricate an in-window event, so this is a synthesis failure (Req 8.6).
        return Error(message=_MSG_SYNTHESIS_FAILED, reason=_REASON_SYNTHESIS_FAILED)

    # Build a structurally-complete candidate purely so it can pass the SAME
    # verifier the LLM path uses; the placeholder prose is discarded below.
    candidate = Retention_Recommendation(
        diagnosis=_TEMPLATE_CANDIDATE_DIAGNOSIS,
        events=selected,
        outreach_template=_TEMPLATE_CANDIDATE_OUTREACH,
        mode=_MODE_TEMPLATE,
    )
    verified = verify_recommendation(candidate, results, request_date)
    if verified.is_err:
        return Error(message=_MSG_SYNTHESIS_FAILED, reason=_REASON_SYNTHESIS_FAILED)

    # Hand the verified real, in-window, distinct events to the pure assembler,
    # which supplies the fixed diagnosis/outreach text and tags mode="template".
    rec = build_templated_recommendation(member, verified.value.events, request_date)
    return Recommendation(recommendation=rec)


# ---------------------------------------------------------------------------
# Public entry point (Req 8.2–8.8, 8.11–8.14)
# ---------------------------------------------------------------------------


def generate_recommendation(
    member: MemberContext,
    request_date: date,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    search_backend: "SearchBackend | None" = None,
    synthesizer: "RecommendationSynthesizer | None" = None,
) -> RetentionOutcome:
    """Produce exactly one retention outcome for a member (Req 8.2–8.8, 8.11–8.14).

    Pipeline (design.md Component 9), bounded by a 30-second monotonic deadline:

    1. **Eligibility guard (Req 8.2, 8.12).** If ``member`` is not at risk,
       return ``Error(reason="not_eligible")`` *without calling the search tool*
       and without producing a recommendation. This is both a cost control and a
       testable invariant.
    2. **Web search (Req 8.3).** Otherwise call ``search_events`` for upcoming
       local events matching the member's interests/stage in the next 30 days. A
       :class:`SearchError` (including the not-configured default) yields
       ``Error(reason="search_failed")`` — no recommendation, no events (Req 8.8,
       8.13).
    3. **Zero results (Req 8.7, 8.13).** An empty result set yields ``NoEvents``.
       This holds even when the LLM is unavailable — the template fallback is
       never reached on a zero-result search.
    4. **LLM synthesis + verification (Req 8.5, 8.6).** With ≥1 result and
       remaining time budget, prompt the injected ``synthesizer`` under the
       "Wellington the Wise" persona within the remaining deadline. On a
       candidate, stamp ``mode = "llm"`` and run the pure ``verify_recommendation``
       against the real results; on success return ``Recommendation`` tagged
       ``mode = "llm"``.
    4b. **Deterministic template fallback (Req 8.11, 8.14).** If the LLM is
       unavailable, breaches the remaining deadline, crashes, returns nothing, or
       its candidate fails verification, take the deterministic fallback: select
       up to 3 distinct in-window events from the *real* results, verify them
       through the SAME ``verify_recommendation``, and assemble via
       ``build_templated_recommendation`` → ``Recommendation`` tagged
       ``mode = "template"``. If no event is in-window after a ≥1-result search,
       decline rather than fabricate → ``Error(reason="synthesis_failed")``.

    Exactly one :data:`RetentionOutcome` is returned within the deadline (Req
    8.4, 8.14).

    Args:
        member: The member's safe stored context. ``member.at_risk`` gates
            eligibility; ``interests`` / ``stage`` / ``location`` steer the
            search.
        request_date: The injected "today"; the search/verification window is
            ``[request_date, request_date + 30d]``. Never read from the wall
            clock (Req 8.3).
        deadline_seconds: Overall monotonic deadline (default 30, Req 8.4/8.14).
        search_backend: The injected Web Search Tool seam. Defaults to
            :class:`NotConfiguredSearchBackend` (raises → ``search_failed``).
        synthesizer: The injected LLM synthesis seam. Defaults to
            :class:`NotConfiguredSynthesizer` (raises → template fallback).

    Returns:
        Exactly one of :class:`Recommendation`, :class:`NoEvents`, or
        :class:`Error`.
    """
    start = time.monotonic()

    if search_backend is None:
        search_backend = NotConfiguredSearchBackend()
    if synthesizer is None:
        synthesizer = NotConfiguredSynthesizer()

    # STEP 1 — Eligibility guard (Req 8.2, 8.12). No search is issued.
    if not member.at_risk:
        return Error(message=_MSG_NOT_ELIGIBLE, reason=_REASON_NOT_ELIGIBLE)

    # STEP 2 — Web search (Req 8.3). A failed search is distinct from zero
    # results: it yields Error, not NoEvents (Req 8.8, 8.13).
    try:
        results = search_events(
            interests=member.interests,
            stage=member.stage,
            location=member.location,
            request_date=request_date,
            backend=search_backend,
        )
    except SearchError:
        return Error(message=_MSG_SEARCH_FAILED, reason=_REASON_SEARCH_FAILED)

    # STEP 3 — Zero results (Req 8.7, 8.13). Template fallback is NEVER reached
    # on a zero-result search.
    if not results:
        return NoEvents()

    # Remaining time budget for the LLM synthesis step. If we have already spent
    # the whole deadline, skip straight to the (instantaneous, pure) fallback.
    remaining = deadline_seconds - (time.monotonic() - start)
    if remaining <= 0:
        return _template_fallback(member, results, request_date)

    # STEP 4 — LLM synthesis under the remaining deadline (Req 8.5, 8.6).
    try:
        candidate: Optional[Retention_Recommendation] = _synthesize_with_deadline(
            synthesizer, member, results, request_date, remaining
        )
    except _DeadlineExceeded:
        # LLM did not respond in time → deterministic template fallback (Req 8.11).
        return _template_fallback(member, results, request_date)
    except LLMSynthesisNotConfigured:
        # LLM unavailable / not configured → template fallback (Req 8.11).
        return _template_fallback(member, results, request_date)
    except Exception:
        # Any other synthesis crash is treated as LLM unavailability → fallback.
        return _template_fallback(member, results, request_date)

    # A synthesizer that returns None (rather than raising) is treated as "no
    # usable synthesis" → template fallback.
    if candidate is None:
        return _template_fallback(member, results, request_date)

    # Stamp the LLM provenance, then verify against the REAL results (Req 8.6).
    candidate = _with_mode(candidate, _MODE_LLM)
    verified = verify_recommendation(candidate, results, request_date)
    if verified.is_ok:
        return Recommendation(recommendation=verified.value)

    # The LLM's candidate failed verification irreparably. Rather than surface an
    # error, fall back to a deterministic template recommendation over the same
    # real results — a usable recommendation still exists when real in-window
    # events are present (Req 8.11).
    return _template_fallback(member, results, request_date)


__all__ = [
    "DEFAULT_DEADLINE_SECONDS",
    "WELLINGTON_SYSTEM_PROMPT",
    "RecommendationSynthesizer",
    "LLMSynthesisNotConfigured",
    "NotConfiguredSynthesizer",
    "generate_recommendation",
]
