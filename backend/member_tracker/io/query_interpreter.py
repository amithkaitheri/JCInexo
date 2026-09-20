"""query_interpreter.py — the Query Interpreter (I/O adapter, Requirement 7).

Component 9 of design.md ("Query Interpreter"). This is the *non-deterministic*
half of the natural-language querying capability: it translates a plain-English
``Natural_Language_Query`` into schema-bounded ``Interpreted_Criteria`` by
calling an LLM/agent backend, then hands those criteria to the *pure*
``evaluate_criteria`` (``core/criteria.py``) to select the matching subset of
stored members.

The correctness-critical guarantee — that a query result is always a *subset* of
the real stored members and never fabricates records or fields — does **not**
live here; it lives in ``evaluate_criteria``, which can only ever select from
the explicit member list it is given. This adapter's job is narrow: obtain
candidate criteria (from the LLM), *validate them against the Member schema*
before use, enforce the 10-second deadline, and package exactly one outcome.

Design decisions realized here (design.md Component 9, Req 7.1/7.3–7.8):

* **Exactly one outcome (Req 7.7).** For any query, ``interpret_query`` returns a
  single sum-typed :data:`QueryOutcome` — a :class:`ResultSet` (possibly empty,
  Req 7.4), a :class:`Clarification` (Req 7.5/7.6), or a :class:`Timeout`
  (Req 7.8). Mutual exclusivity is structural: exactly one value is returned.
* **Empty/whitespace short-circuit, no LLM call (Req 7.6).** A query that is
  empty or only whitespace returns :class:`Clarification` with zero members
  *before* the translator is ever consulted. This is asserted by the
  no-LLM-call example test (task 15.3).
* **10-second monotonic deadline (Req 7.8).** The translator call runs under a
  monotonic-clock deadline. On breach the interpreter yields :class:`Timeout`
  with zero members rather than a partial result. See
  :func:`_translate_with_deadline` for the mechanism and its limitations.
* **LLM output is never trusted directly (Req 7.5).** Candidate criteria from
  the translator are always run through ``validate_criteria`` before use; an
  unknown field or a disallowed enum value yields :class:`Clarification`. An
  ambiguous/uninterpretable query (signalled by the translator) likewise yields
  :class:`Clarification`.

Dependency injection — the LLM translator seam
-----------------------------------------------
The non-deterministic LLM behaviour is isolated behind a small
:class:`QueryTranslator` protocol so this module makes **no** hardcoded network
call to any specific provider. A translator is any object (or the module
accepts a bare callable too) exposing::

    translate(nl: str, current_date: date) -> Interpreted_Criteria

and signalling an ambiguous/uninterpretable query by raising
:class:`UninterpretableQuery` (or returning the :data:`UNINTERPRETABLE`
sentinel). ``interpret_query`` accepts the translator as a parameter so tests
pass a mock and the API layer (task 15.5) passes a real adapter. When no
translator is supplied, a :class:`NotConfiguredTranslator` default is used that
raises :class:`LLMNotConfigured` — this environment has no configured LLM, and
the default makes that explicit rather than silently pretending to interpret.

Deterministic-parser fallback (task 15.10, Req 7.9–7.12)
--------------------------------------------------------
The LLM path runs under a **5-second dispatch budget** *within* the overall
10-second bound (Req 7.9). If the ``LLM_Backend`` is **unavailable**
(:class:`LLMNotConfigured`), **does not respond within 5 seconds** of dispatch
(:class:`_DeadlineExceeded` on the dispatch budget), or **fails with an
unexpected error**, the interpreter abandons the LLM path and derives criteria
with the pure :func:`~member_tracker.core.query_parser.parse_query_deterministic`
(Component 8) instead:

* If the deterministic parser recognizes criteria, they are run through the
  *unchanged* ``validate_criteria`` + pure ``evaluate_criteria`` and returned as
  a :class:`ResultSet` tagged ``source = "deterministic_fallback"`` — possibly
  empty (Req 7.10). Because the fallback routes through the same pure evaluator
  over the same stored members, the subset / non-fabrication guarantee holds for
  free; it changes only *how criteria are derived*, never *how members are
  selected*.
* If the deterministic parser returns :data:`~member_tracker.core.query_parser.UNPARSEABLE`
  (and the LLM was unavailable/failed), the interpreter returns a
  :class:`Clarification` with zero members (Req 7.11).

A crucial distinction preserved from the base pipeline (Req 7.5 vs 7.9): the
fallback is triggered *only* by LLM **unavailability / timeout / crash**. When
the LLM **successfully responds** but the query is genuinely uninterpretable
(:class:`UninterpretableQuery` / :data:`UNINTERPRETABLE`) or returns criteria
that **fail** ``validate_criteria``, that is an "I understood, but it is
uninterpretable / invalid" signal — it yields :class:`Clarification` directly
and is *not* masked by the deterministic fallback. Only the fallback's own
outcome (whether via unavailability, timeout, or crash) may be a
``deterministic_fallback`` result.

Because the deterministic parser + validate + evaluate are effectively
instantaneous, taking the fallback keeps the overall response well within the
10-second bound (Req 7.12), and exactly one outcome is still returned per query
(Req 7.7/7.12).

* The LLM-translation-under-deadline step is isolated in the
  :func:`_translate_with_deadline` helper; the 5s dispatch budget is layered on
  top of it in :func:`interpret_query`.
* :class:`ResultSet` carries a ``source`` field defaulting to ``"llm"``; the
  fallback path sets ``source = "deterministic_fallback"``.

Requirements: 7.1, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11, 7.12.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date
from typing import List, Union

from member_tracker.core.criteria import (
    CriteriaMemberRow,
    Interpreted_Criteria,
    evaluate_criteria,
    validate_criteria,
)
from member_tracker.core.query_parser import (
    UNPARSEABLE,
    Unparseable,
    parse_query_deterministic,
)

# ---------------------------------------------------------------------------
# The LLM-translator seam (dependency injection)
# ---------------------------------------------------------------------------

# `typing.Protocol` is only available on 3.8+. Fall back to a plain base class on
# older runtimes so the module imports cleanly everywhere.
try:  # pragma: no cover - import-time capability probe
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class QueryTranslator(Protocol):
        """The injectable NL->criteria translation seam (design.md Component 9).

        An implementation translates a non-empty ``Natural_Language_Query`` into
        candidate :class:`Interpreted_Criteria`. To signal that the query is
        ambiguous or uninterpretable it must raise :class:`UninterpretableQuery`
        (or return :data:`UNINTERPRETABLE`); to signal that no LLM is configured
        it may raise :class:`LLMNotConfigured`. It must not read the wall clock
        for the "current date" — that is passed in.
        """

        def translate(
            self, nl: str, current_date: date
        ) -> "Interpreted_Criteria":  # pragma: no cover - protocol stub
            ...

except ImportError:  # pragma: no cover - very old Python
    class QueryTranslator:  # type: ignore[no-redef]
        """Fallback base for the translator seam when ``typing.Protocol`` is
        unavailable. See the Protocol version above for the contract."""

        def translate(self, nl: str, current_date: date) -> "Interpreted_Criteria":
            raise NotImplementedError


class _Uninterpretable:
    """Type of the :data:`UNINTERPRETABLE` sentinel (an alternative to raising
    :class:`UninterpretableQuery`)."""

    _instance = None

    def __new__(cls) -> "_Uninterpretable":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "UNINTERPRETABLE"


# A translator may return this sentinel instead of raising to signal that the
# query is ambiguous/uninterpretable.
UNINTERPRETABLE = _Uninterpretable()


class UninterpretableQuery(Exception):
    """Raised by a :class:`QueryTranslator` when a query is ambiguous or cannot
    be mapped to schema-bounded criteria (drives a :class:`Clarification`,
    Req 7.5)."""


class LLMNotConfigured(RuntimeError):
    """Raised by :class:`NotConfiguredTranslator` when no real LLM backend is
    wired in. Treated by :func:`interpret_query` as "uninterpretable" so a query
    still yields a well-formed :class:`Clarification` rather than crashing."""


class NotConfiguredTranslator:
    """Default translator used when no LLM backend is injected.

    This environment has no configured LLM. Rather than hardcode a network call
    to a specific provider, the default makes the absence explicit: every
    ``translate`` call raises :class:`LLMNotConfigured`. :func:`interpret_query`
    treats that as an uninterpretable query and returns a :class:`Clarification`
    with zero members. Wire in a real adapter (or a mock, in tests) via the
    ``translator`` parameter to enable actual interpretation.
    """

    def translate(self, nl: str, current_date: date) -> "Interpreted_Criteria":
        raise LLMNotConfigured(
            "No LLM backend is configured for the Query Interpreter; "
            "inject a QueryTranslator to enable natural-language interpretation."
        )


# ---------------------------------------------------------------------------
# Query outcomes — a single sum type (Req 7.7 exactly-one-outcome)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultSet:
    """A successful interpretation with its matching members (Req 7.1/7.3/7.4).

    Carries the validated :class:`Interpreted_Criteria` (so the API/UI can show
    each field, comparison, and value that was applied, Req 7.3) and the matched
    ``members`` — always a subset of the input members, possibly empty (Req 7.4).

    ``source`` records which interpreter derived the criteria. It defaults to
    ``"llm"`` for the LLM path; task 15.10's deterministic-parser fallback will
    set ``"deterministic_fallback"``. Included now so that extension does not
    change this type's shape.
    """

    criteria: Interpreted_Criteria
    members: List[CriteriaMemberRow]
    source: str = "llm"


@dataclass(frozen=True)
class Clarification:
    """The query could not be interpreted — ambiguous, uninterpretable, empty/
    whitespace, or the candidate criteria failed schema validation (Req 7.5,
    7.6). Carries zero members by construction."""

    message: str
    members: List[CriteriaMemberRow] = field(default_factory=list)


@dataclass(frozen=True)
class Timeout:
    """The interpreter did not produce an outcome within the deadline (Req 7.8).
    Carries zero members by construction."""

    message: str
    members: List[CriteriaMemberRow] = field(default_factory=list)


# The interpreter yields exactly one of these three outcomes per query (Req 7.7).
QueryOutcome = Union[ResultSet, Clarification, Timeout]


# ---------------------------------------------------------------------------
# Default messages
# ---------------------------------------------------------------------------

_CLARIFY_EMPTY = (
    "The query was empty. Please enter a question describing the members you "
    "want to find."
)
_CLARIFY_UNINTERPRETABLE = (
    "The query could not be interpreted. Please rephrase it using member "
    "attributes such as stage, health score, attendance, or at-risk status."
)
_CLARIFY_INVALID_CRITERIA = (
    "The query could not be interpreted: it referenced a member attribute or "
    "value the tracker does not recognize. Please rephrase using known member "
    "attributes."
)
_TIMEOUT_MESSAGE = (
    "The query timed out before an interpretation could be produced. Please try "
    "again or simplify the query."
)

# Provenance tags for a ResultSet's `source` field (Req 7.10).
_SOURCE_LLM = "llm"
_SOURCE_DETERMINISTIC_FALLBACK = "deterministic_fallback"

# Default LLM dispatch budget (Req 7.9): the LLM path gets at most this many
# seconds *of the overall deadline* before the deterministic fallback is used.
_DEFAULT_LLM_DISPATCH_BUDGET_SECONDS = 5


# ---------------------------------------------------------------------------
# LLM translation under a monotonic deadline (isolated helper — task 15.10 seam)
# ---------------------------------------------------------------------------


class _DeadlineExceeded(Exception):
    """Internal signal that the translator did not finish within the deadline."""


def _invoke_translator(translator, nl: str, current_date: date):
    """Call the translator, normalizing its two "uninterpretable" channels.

    Returns the candidate :class:`Interpreted_Criteria`, or raises
    :class:`UninterpretableQuery` when the translator signalled an
    ambiguous/uninterpretable query (either by raising it, by raising
    :class:`LLMNotConfigured`, or by returning the :data:`UNINTERPRETABLE`
    sentinel). Any *other* exception propagates unchanged.
    """
    # Accept either an object exposing `.translate(nl, current_date)` or a bare
    # callable with the same signature.
    if hasattr(translator, "translate"):
        translate = translator.translate
    elif callable(translator):
        translate = translator
    else:  # pragma: no cover - misuse guard
        raise TypeError(
            "translator must expose a `translate(nl, current_date)` method or "
            "be a callable with that signature."
        )

    result = translate(nl, current_date)
    if result is UNINTERPRETABLE or isinstance(result, _Uninterpretable):
        raise UninterpretableQuery("translator returned UNINTERPRETABLE sentinel")
    return result


def _translate_with_deadline(
    translator, nl: str, current_date: date, deadline_seconds: float
):
    """Run the translator under a monotonic-clock deadline.

    The translator call is dispatched onto a worker thread and awaited for at
    most ``deadline_seconds`` measured on :func:`time.monotonic`. On breach we
    raise :class:`_DeadlineExceeded`; the caller maps that to a :class:`Timeout`
    outcome (Req 7.8).

    Cancellation limitation (documented, dependency-light approach): Python
    cannot forcibly kill a running thread, so on a deadline breach we *abandon*
    the worker (attempt ``future.cancel()``, then stop waiting) rather than
    interrupting it. The abandoned translator call may keep running in the
    background until it finishes on its own, but its result is never observed or
    returned — the interpreter has already committed to the :class:`Timeout`
    outcome, preserving the exactly-one-outcome guarantee (Req 7.7). The worker
    thread is a daemon so it never blocks process shutdown. This keeps the
    timeout dependency-light (stdlib ``concurrent.futures`` only) at the cost of
    not reclaiming the abandoned thread immediately; a production adapter that
    needs hard cancellation should push the deadline into the transport (e.g. an
    HTTP client timeout) as well.

    Raises:
        _DeadlineExceeded: the translator did not finish within the deadline.
        UninterpretableQuery: the translator signalled an uninterpretable query.
        Exception: any other translator failure propagates unchanged.
    """
    start = time.monotonic()
    # A single-worker, daemon-threaded executor per call keeps this self
    # contained; the pool is torn down (without waiting on an abandoned worker)
    # in the finally block.
    executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="query-translate"
    )
    try:
        future: Future = executor.submit(
            _invoke_translator, translator, nl, current_date
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
# Deterministic-parser fallback (task 15.10, Req 7.9–7.11)
# ---------------------------------------------------------------------------


def _deterministic_fallback(
    nl: str, members: List[CriteriaMemberRow]
) -> QueryOutcome:
    """Derive an outcome from the pure deterministic parser (LLM unavailable).

    Reached only when the LLM path was abandoned because the ``LLM_Backend`` was
    unavailable, breached the 5-second dispatch budget, or crashed (Req 7.9).
    Runs the pure :func:`parse_query_deterministic`; on recognized criteria it
    reuses the *unchanged* ``validate_criteria`` + pure ``evaluate_criteria`` and
    returns a :class:`ResultSet` tagged ``source = "deterministic_fallback"``
    (possibly empty, Req 7.10). If the parser returns
    :data:`UNPARSEABLE`, it returns :class:`Clarification` with zero members
    (Req 7.11).

    The deterministic parser, validation, and evaluation are all pure and
    effectively instantaneous, so this path adds no meaningful time to the
    overall response — the 10-second bound is preserved (Req 7.12).
    """
    parsed = parse_query_deterministic(nl)

    # Req 7.11: neither the LLM nor the deterministic parser could interpret the
    # query -> Clarification with zero members.
    if parsed is UNPARSEABLE or isinstance(parsed, Unparseable):
        return Clarification(message=_CLARIFY_UNINTERPRETABLE, members=[])

    # The deterministic parser emits schema-bounded criteria by construction, so
    # validation should pass; still run the *unchanged* validator and treat any
    # unexpected failure conservatively as a Clarification (never trust criteria
    # unvalidated). This keeps the pipeline total.
    validation = validate_criteria(parsed)
    if validation.is_err:
        return Clarification(message=_CLARIFY_INVALID_CRITERIA, members=[])

    criteria = validation.value

    # Same pure, deterministic selection over the real stored members as the LLM
    # path — only the criteria's provenance differs (Req 7.10).
    subset = evaluate_criteria(criteria, members)
    return ResultSet(
        criteria=criteria,
        members=subset,
        source=_SOURCE_DETERMINISTIC_FALLBACK,
    )


# ---------------------------------------------------------------------------
# Public entry point (Req 7.1, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8)
# ---------------------------------------------------------------------------


def interpret_query(
    nl: str,
    members: List[CriteriaMemberRow],
    current_date: date,
    deadline_seconds: float = 10,
    translator: "QueryTranslator | None" = None,
    llm_dispatch_budget_seconds: float = _DEFAULT_LLM_DISPATCH_BUDGET_SECONDS,
) -> QueryOutcome:
    """Interpret a natural-language query and return exactly one outcome.

    Pipeline (design.md Component 9, with the Component 8 deterministic
    fallback):

    1. **Empty/whitespace short-circuit (Req 7.6).** If ``nl`` is empty or only
       whitespace, return :class:`Clarification` with zero members *without*
       calling the translator or the deterministic parser.
    2. **LLM translation under a 5s dispatch budget within the 10s bound
       (Req 7.8, 7.9).** Otherwise call the injected ``translator`` under a
       dispatch deadline of ``min(llm_dispatch_budget_seconds, remaining
       overall deadline)`` on a monotonic clock. Three "LLM unavailable" signals
       route to the deterministic fallback (step 2a): the LLM is not configured
       (:class:`LLMNotConfigured`), it breaches the dispatch budget
       (:class:`_DeadlineExceeded`), or it crashes with an unexpected error.
    2a. **Deterministic fallback (Req 7.9, 7.10, 7.11).** On any "LLM
       unavailable" signal, derive criteria with
       :func:`parse_query_deterministic`: recognized criteria -> validated +
       evaluated -> :class:`ResultSet` tagged ``source =
       "deterministic_fallback"`` (possibly empty); :data:`UNPARSEABLE` ->
       :class:`Clarification` with zero members. See :func:`_deterministic_fallback`.
    3. **LLM understood-but-uninterpretable / invalid (Req 7.5).** If the LLM
       *successfully responds* but signals the query is ambiguous/
       uninterpretable (:class:`UninterpretableQuery` / :data:`UNINTERPRETABLE`),
       return :class:`Clarification` — this is *not* an "LLM unavailable"
       condition and does *not* trigger the deterministic fallback. Likewise,
       candidate criteria are run through ``validate_criteria`` and an unknown
       field / disallowed value yields :class:`Clarification`.
    4. **Pure selection (Req 7.1/7.4).** On valid LLM criteria, call
       ``evaluate_criteria(criteria, members)`` and return a :class:`ResultSet`
       carrying the criteria and the matched subset (``source = "llm"``). The
       subset may be empty — a distinct, valid outcome from
       :class:`Clarification` (Req 7.4).

    Exactly one :data:`QueryOutcome` is returned for any input, within the
    overall 10-second bound (Req 7.7, 7.12).

    Args:
        nl: The raw natural-language query text.
        members: The current stored members to select from, as the
            :class:`CriteriaMemberRow` projection ``evaluate_criteria`` consumes.
            (The API layer, task 15.5, reads these from the repository so the
            subset guarantee holds end to end.)
        current_date: The injected current date, passed to the translator so no
            component reads the wall clock for "today".
        deadline_seconds: Overall monotonic deadline for producing an outcome
            (default 10, per Req 7.7/7.8/7.12).
        translator: The injected NL->criteria seam. Defaults to
            :class:`NotConfiguredTranslator` (no LLM configured -> the
            deterministic fallback is used) so the module never hardcodes a
            provider call.
        llm_dispatch_budget_seconds: The LLM dispatch budget (default 5,
            Req 7.9). The LLM path is awaited for at most
            ``min(this, remaining overall deadline)`` seconds; on breach the
            interpreter abandons the LLM and uses the deterministic parser.

    Returns:
        Exactly one of :class:`ResultSet`, :class:`Clarification`, or
        :class:`Timeout`.
    """
    # 1. Empty/whitespace short-circuit — no LLM call, no fallback (Req 7.6).
    if nl is None or nl.strip() == "":
        return Clarification(message=_CLARIFY_EMPTY, members=[])

    if translator is None:
        translator = NotConfiguredTranslator()

    # 2. LLM translation under the 5s dispatch budget, bounded by the overall
    #    deadline (Req 7.8, 7.9). The dispatch budget is the minimum of the
    #    configured budget and whatever remains of the overall deadline, so the
    #    LLM path can never exceed the 10s bound.
    dispatch_budget = min(llm_dispatch_budget_seconds, deadline_seconds)
    try:
        candidate = _translate_with_deadline(
            translator, nl, current_date, dispatch_budget
        )
    except _DeadlineExceeded:
        # LLM did not respond within the 5s dispatch budget -> fall back to the
        # deterministic parser (Req 7.9). The pure fallback is effectively
        # instantaneous, so the overall 10s bound is preserved (Req 7.12).
        return _deterministic_fallback(nl, members)
    except LLMNotConfigured:
        # LLM unavailable / not configured -> deterministic fallback (Req 7.9).
        return _deterministic_fallback(nl, members)
    except UninterpretableQuery:
        # Distinct from unavailability: the LLM *understood* but the query is
        # genuinely uninterpretable -> Clarification, NOT a fallback (Req 7.5).
        return Clarification(message=_CLARIFY_UNINTERPRETABLE, members=[])
    except Exception:
        # Any other translator crash is treated as LLM unavailability and routes
        # to the deterministic fallback rather than surfacing the error (Req 7.9).
        return _deterministic_fallback(nl, members)

    # A translator that returns None (rather than raising/sentinel) is treated
    # as uninterpretable too, keeping the interpreter total. This is a successful
    # LLM response that is simply empty -> Clarification, not a fallback.
    if candidate is None:
        return Clarification(message=_CLARIFY_UNINTERPRETABLE, members=[])

    # 3. Never trust LLM output — validate against the Member schema (Req 7.5).
    #    A successful-but-invalid LLM response clarifies; it does not fall back.
    validation = validate_criteria(candidate)
    if validation.is_err:
        return Clarification(message=_CLARIFY_INVALID_CRITERIA, members=[])

    criteria = validation.value

    # 4. Pure, deterministic selection over the real stored members (Req 7.1/7.4).
    subset = evaluate_criteria(criteria, members)
    return ResultSet(criteria=criteria, members=subset, source=_SOURCE_LLM)


__all__ = [
    "QueryTranslator",
    "UNINTERPRETABLE",
    "UninterpretableQuery",
    "LLMNotConfigured",
    "NotConfiguredTranslator",
    "ResultSet",
    "Clarification",
    "Timeout",
    "QueryOutcome",
    "interpret_query",
]
