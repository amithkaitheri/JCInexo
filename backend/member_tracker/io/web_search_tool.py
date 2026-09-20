"""web_search_tool.py — the Web Search Tool adapter (I/O, Requirement 8.3).

Component 9 of design.md ("Web Search Tool Adapter"). This module is the
*single choke point* for the "live event data" side effect, mirroring how the
Repository is the single choke point for persistence. It wraps an external
search API (e.g., a Google Search API) behind one narrow interface::

    search_events(interests, stage, location, request_date) -> List[Web_Search_Result]

Its job is deliberately narrow (design.md Component 9):

* Translate the agent's query — built from the member's ``interests`` /
  ``stage`` / optional ``location`` and the next-30-day window — into a call on
  the injected search backend.
* Restrict results to ``event_date in [request_date, request_date + 30d]``
  (the tool filters to the window; the pure ``verify_recommendation`` also
  enforces it later, so this is defense in depth).
* Normalize each returned hit into a :class:`~member_tracker.core.types.Web_Search_Result`
  carrying at minimum a ``title``, an ``event_date`` (:class:`datetime.date`),
  and a source ``url`` (plus an optional ``snippet``).

It performs **no synthesis and makes no correctness claims** — it returns what
the search returned (post window-filter). It may return an empty list. The
correctness-critical anti-fabrication guarantee lives downstream in the pure
``verify_recommendation`` (``core/recommendation.py``, task 16.1), which can only
bless events drawn from exactly this returned set.

``request_date`` is always passed in (Req 8.3): this adapter never reads the
wall clock for "today", so callers and tests stay reproducible.

Dependency injection — the search-backend seam
-----------------------------------------------
The non-deterministic live-web-search behaviour is isolated behind a small
:class:`SearchBackend` protocol so this module makes **no** hardcoded network
call to any specific provider. A backend is any object (or a bare callable)
exposing::

    search(params: SearchQuery) -> Iterable[<raw hit>]

where each raw hit is either a :class:`Web_Search_Result` already, or a mapping
(``dict``) / attribute-bearing object carrying at least ``title``,
``event_date``, and ``url`` (``snippet`` optional). ``search_events`` accepts the
backend as a parameter so the Wellington agent (task 18.3) and tests can pass a
mock or a real adapter via dependency injection.

Default / not-configured behaviour
-----------------------------------
This environment has **no configured search API**. Rather than hardcode a
provider call, the default backend :class:`NotConfiguredSearchBackend` makes the
absence explicit: every ``search`` call raises :class:`SearchNotConfigured`
(a subclass of :class:`SearchError`). We deliberately *raise* rather than
silently return ``[]`` because design semantics distinguish a **search failure**
(an ``Error`` outcome) from **zero results** (a ``NoEvents`` outcome). A silent
empty list would masquerade as "the search ran and legitimately found nothing",
losing that distinction. By raising, the Wellington agent (task 18.3) can map a
missing/failed backend to an ``Error`` outcome and a genuine empty result to
``NoEvents``. Wire in a real adapter (or a mock, in tests) via the ``backend``
parameter to enable actual searching.

Errors raised by a real backend (network failure, quota exhaustion, malformed
response, etc.) should be surfaced as :class:`SearchError` so the agent maps
them to an ``Error`` outcome; a well-behaved but result-free search returns an
empty list, which the agent maps to ``NoEvents``.

Requirements: 8.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Union

from member_tracker.core.types import MembershipStage, Web_Search_Result

# The event window looked ahead from the request date (Req 8.3): events must
# fall within [request_date, request_date + 30 days], inclusive on both ends.
SEARCH_WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# Errors — a failed search is distinct from a zero-result search
# ---------------------------------------------------------------------------


class SearchError(RuntimeError):
    """The search could not be performed or returned an unusable response.

    Raised for backend failures (network errors, quota exhaustion, malformed
    responses) and — via the :class:`SearchNotConfigured` subclass — when no
    backend is wired in. The Wellington agent (task 18.3) maps a
    :class:`SearchError` to an ``Error`` retention outcome, keeping it distinct
    from a genuine zero-result search (which returns ``[]`` and maps to
    ``NoEvents``).
    """


class SearchNotConfigured(SearchError):
    """Raised by :class:`NotConfiguredSearchBackend` when no real search backend
    is injected.

    This environment has no configured search API. Rather than silently return
    an empty list — which would be indistinguishable from a legitimate
    zero-result search — the default backend raises this so the agent can
    surface an ``Error`` (not a false ``NoEvents``)."""


# ---------------------------------------------------------------------------
# The query passed to the backend (built from the member's profile)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchQuery:
    """The structured query the adapter hands to the :class:`SearchBackend`.

    Built from the member's profile and the next-30-day window. A backend
    implementation may use ``text`` (a ready-made human-readable query string
    reflecting interests/stage/location and the window) or the individual
    structured fields — whichever suits the provider. The window bounds
    (``window_start`` / ``window_end``) are always populated so a backend can
    push the date restriction down to the provider if it supports it.
    """

    text: str
    interests: Sequence[str]
    stage: Optional[str]
    location: Optional[str]
    window_start: date
    window_end: date
    keywords: Sequence[str] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# The search-backend seam (dependency injection)
# ---------------------------------------------------------------------------

# `typing.Protocol` is only available on 3.8+. Fall back to a plain base class on
# older runtimes so the module imports cleanly everywhere.
try:  # pragma: no cover - import-time capability probe
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class SearchBackend(Protocol):
        """The injectable live-web-search seam (design.md Component 9).

        An implementation issues one search for the given :class:`SearchQuery`
        and returns an iterable of raw hits. Each hit may be a
        :class:`Web_Search_Result` already, a mapping with ``title`` /
        ``event_date`` / ``url`` (``snippet`` optional), or any object exposing
        those attributes. It must raise :class:`SearchError` (or a subclass) on
        failure so the caller can distinguish a failed search from a
        zero-result search; a successful but result-free search returns an empty
        iterable.
        """

        def search(self, params: "SearchQuery") -> Iterable[Any]:  # pragma: no cover - protocol stub
            ...

except ImportError:  # pragma: no cover - very old Python
    class SearchBackend:  # type: ignore[no-redef]
        """Fallback base for the search seam when ``typing.Protocol`` is
        unavailable. See the Protocol version above for the contract."""

        def search(self, params: "SearchQuery") -> Iterable[Any]:
            raise NotImplementedError


class NotConfiguredSearchBackend:
    """Default backend used when no search backend is injected.

    This environment has no configured search API. Rather than hardcode a
    network call to a specific provider — or silently return ``[]`` and lose the
    failed-search vs. zero-result distinction — every ``search`` call raises
    :class:`SearchNotConfigured`. :func:`search_events` lets that propagate so
    the Wellington agent (task 18.3) maps it to an ``Error`` outcome. Wire in a
    real adapter (or a mock, in tests) via the ``backend`` parameter to enable
    actual searching.
    """

    def search(self, params: "SearchQuery") -> Iterable[Any]:
        raise SearchNotConfigured(
            "No search backend is configured for the Web Search Tool; inject a "
            "SearchBackend to enable live event search."
        )


# A raw hit may be a Web_Search_Result, a mapping, or an attribute-bearing
# object. This alias documents the accepted shapes for normalization.
RawHit = Union[Web_Search_Result, Mapping[str, Any], Any]


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def _stage_to_str(stage: Union[str, MembershipStage, None]) -> Optional[str]:
    """Normalize a stage argument (enum or string) to its string value."""
    if stage is None:
        return None
    if isinstance(stage, MembershipStage):
        return stage.value
    return str(stage)


def _clean_terms(interests: Optional[Iterable[str]]) -> List[str]:
    """Trim, drop empties, and de-duplicate (order-preserving) interest terms."""
    if not interests:
        return []
    seen = set()
    cleaned: List[str] = []
    for raw in interests:
        if raw is None:
            continue
        term = str(raw).strip()
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(term)
    return cleaned


def build_search_query(
    interests: Optional[Iterable[str]],
    stage: Union[str, MembershipStage, None],
    location: Optional[str],
    request_date: date,
) -> SearchQuery:
    """Translate the member's profile + the next-30-day window into a
    :class:`SearchQuery` (design.md Component 9, Req 8.3).

    The human-readable ``text`` reflects the member's interests or stage, the
    event kinds Wellington looks for (seminars / networking events / skill
    workshops), the optional location, and the explicit date window so a backend
    that only accepts a query string still restricts appropriately.
    """
    terms = _clean_terms(interests)
    stage_str = _stage_to_str(stage)
    window_start = request_date
    window_end = request_date + timedelta(days=SEARCH_WINDOW_DAYS)

    # Event kinds Wellington searches for (design.md Component 9 / Req 8.3).
    event_kinds = "seminars, networking events, or skill workshops"

    # Prefer interests to describe *what* to look for; fall back to the stage
    # when the member has no recorded interests so the query is never empty.
    if terms:
        topic = ", ".join(terms)
    elif stage_str:
        topic = f"{stage_str} members"
    else:
        topic = "professional development"

    parts = [f"upcoming local {event_kinds} about {topic}"]
    if location:
        loc = str(location).strip()
        if loc:
            parts.append(f"in {loc}")
    parts.append(
        f"between {window_start.isoformat()} and {window_end.isoformat()}"
    )
    text = " ".join(parts)

    return SearchQuery(
        text=text,
        interests=tuple(terms),
        stage=stage_str,
        location=(str(location).strip() if location and str(location).strip() else None),
        window_start=window_start,
        window_end=window_end,
        keywords=tuple(terms) + ((stage_str,) if stage_str else tuple()),
    )


# ---------------------------------------------------------------------------
# Hit normalization
# ---------------------------------------------------------------------------


def _get_field(hit: RawHit, name: str) -> Any:
    """Read ``name`` from a raw hit, supporting mappings and attribute objects."""
    if isinstance(hit, Mapping):
        return hit.get(name)
    return getattr(hit, name, None)


def _coerce_event_date(value: Any) -> Optional[date]:
    """Best-effort coercion of a backend's date field to :class:`datetime.date`.

    Accepts a ``date`` (returned as-is; a ``datetime`` is reduced to its date),
    or an ISO 8601 date/date-time string. Returns ``None`` when the value is
    missing or cannot be parsed — the hit is then dropped rather than fabricating
    a date.
    """
    if value is None:
        return None
    # A datetime is also a date subclass; normalize to the date component.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Accept a trailing 'Z' (UTC) by normalizing to an offset fromisoformat
        # understands on all supported runtimes.
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            return date.fromisoformat(normalized)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            return None
    return None


def _normalize_hit(hit: RawHit) -> Optional[Web_Search_Result]:
    """Normalize one raw backend hit into a :class:`Web_Search_Result`.

    Returns ``None`` (dropping the hit) when the mandatory ``title`` /
    ``event_date`` / ``url`` cannot be extracted — the adapter never fabricates
    missing fields. A hit already of type :class:`Web_Search_Result` is returned
    unchanged.
    """
    if isinstance(hit, Web_Search_Result):
        return hit

    title = _get_field(hit, "title")
    url = _get_field(hit, "url")
    event_date = _coerce_event_date(_get_field(hit, "event_date"))
    snippet = _get_field(hit, "snippet")

    if not title or not url or event_date is None:
        return None

    title_str = str(title).strip()
    url_str = str(url).strip()
    if not title_str or not url_str:
        return None

    snippet_str = None
    if snippet is not None:
        s = str(snippet).strip()
        snippet_str = s or None

    return Web_Search_Result(
        title=title_str,
        event_date=event_date,
        url=url_str,
        snippet=snippet_str,
    )


def _in_window(event_date: date, window_start: date, window_end: date) -> bool:
    """True iff ``event_date`` lies within ``[window_start, window_end]``."""
    return window_start <= event_date <= window_end


# ---------------------------------------------------------------------------
# Public entry point (Req 8.3)
# ---------------------------------------------------------------------------


def search_events(
    interests: Optional[Iterable[str]],
    stage: Union[str, MembershipStage, None],
    location: Optional[str],
    request_date: date,
    backend: "SearchBackend | None" = None,
) -> List[Web_Search_Result]:
    """Search for upcoming local events matching a member's profile (Req 8.3).

    Translates ``interests`` / ``stage`` / ``location`` and the next-30-day
    window into a query, calls the injected search ``backend``, normalizes each
    returned hit into a :class:`Web_Search_Result` (``title``, ``event_date``,
    ``url``, optional ``snippet``), drops any hit outside
    ``[request_date, request_date + 30d]`` or missing a mandatory field, and
    returns the surviving results — possibly an empty list.

    This adapter performs **no synthesis and makes no correctness claims**; it
    returns exactly what the backend returned, post window-filter. The
    anti-fabrication guarantee is enforced downstream by the pure
    ``verify_recommendation``.

    Args:
        interests: The member's recorded interest terms (may be ``None`` /
            empty; the query then falls back to the stage).
        stage: The member's :class:`~member_tracker.core.types.MembershipStage`
            (or its string value); used to shape the query when interests are
            absent.
        location: Optional locality to bias the search toward "local" events.
        request_date: The injected "today"; the search window is
            ``[request_date, request_date + 30d]``. Never read from the wall
            clock (Req 8.3).
        backend: The injected search seam. Defaults to
            :class:`NotConfiguredSearchBackend`, which raises
            :class:`SearchNotConfigured` — this environment has no configured
            search API, and the default surfaces that as a :class:`SearchError`
            (mapped by the agent to an ``Error`` outcome) rather than a false
            zero-result.

    Returns:
        A list of in-window :class:`Web_Search_Result` (possibly empty).

    Raises:
        SearchError: the backend could not perform the search (including
            :class:`SearchNotConfigured` when no backend is wired in). A
            successful but result-free search returns ``[]`` instead.
    """
    if backend is None:
        backend = NotConfiguredSearchBackend()

    query = build_search_query(interests, stage, location, request_date)

    # Call the backend. Let SearchError (incl. SearchNotConfigured) propagate so
    # the agent maps it to an Error outcome; wrap any *other* backend exception
    # as a SearchError so a failure is never mistaken for a zero-result search.
    try:
        raw_hits = backend.search(query)
    except SearchError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize any backend failure
        raise SearchError(f"search backend failed: {exc}") from exc

    if raw_hits is None:
        return []

    results: List[Web_Search_Result] = []
    for hit in raw_hits:
        normalized = _normalize_hit(hit)
        if normalized is None:
            continue
        # Filter to the [request_date, request_date + 30d] window. The pure
        # verify_recommendation re-checks this later; filtering here keeps the
        # tool's output honest to its documented contract (Req 8.3).
        if not _in_window(
            normalized.event_date, query.window_start, query.window_end
        ):
            continue
        results.append(normalized)

    return results


__all__ = [
    "SEARCH_WINDOW_DAYS",
    "SearchError",
    "SearchNotConfigured",
    "SearchQuery",
    "SearchBackend",
    "NotConfiguredSearchBackend",
    "build_search_query",
    "search_events",
]
