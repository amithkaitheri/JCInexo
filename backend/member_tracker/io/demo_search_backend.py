"""demo_search_backend.py — an offline, deterministic SearchBackend for demos.

This module exists ONLY to make the Wellington Retention Agent demonstrable in
an environment that has no live web-search API or LLM configured. It is not used
in production or tests unless explicitly opted in (see below).

Why it is safe with respect to the design's anti-fabrication guarantee
----------------------------------------------------------------------
The correctness-critical guarantee (Req 8.6) is that every recommended event is
bound to a real ``Web_Search_Result`` returned by the search tool, enforced by
the pure ``core/recommendation.verify_recommendation``. That guarantee is about
*consistency between the search results and the recommendation* — it does not
change based on where the search results come from. This backend simply plays
the role of "the search tool", returning a fixed catalog of plausible upcoming
local events, each dated relative to the injected ``request_date`` so they fall
inside the agent's ``[request_date, request_date + 30d]`` window. The agent's
deterministic template fallback (reached because no LLM is configured) then
selects up to three of these, runs them through the SAME
``verify_recommendation``, and assembles a ``mode = "template"`` recommendation.
No event is invented relative to what this backend returned.

Opt-in
------
Wire it in by overriding the ``get_search_backend`` dependency in
``api/routes_recommendations.py`` (done in ``api/app.py`` when the
``MEMBER_TRACKER_DEMO_SEARCH`` env var is truthy). It is never the default.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable, List

from member_tracker.io.web_search_tool import SearchQuery


# A small catalog of plausible local events, expressed as day-offsets from the
# request date so they always land inside the next-30-day search window. Each
# entry carries the fields web_search_tool._normalize_hit expects: title,
# event_date, url, and an optional snippet.
_DEMO_EVENT_TEMPLATES = [
    {
        "offset_days": 5,
        "title": "Downtown Young Professionals Networking Mixer",
        "url": "https://events.example.org/yp-networking-mixer",
        "snippet": (
            "An informal evening mixer for early-career professionals to build "
            "connections over coffee and short lightning intros."
        ),
    },
    {
        "offset_days": 9,
        "title": "Public Speaking & Confidence Skills Workshop",
        "url": "https://events.example.org/public-speaking-workshop",
        "snippet": (
            "A hands-on workshop covering storytelling, stage presence, and "
            "handling Q&A — great for members building leadership skills."
        ),
    },
    {
        "offset_days": 14,
        "title": "Community Leadership Seminar: Running Great Projects",
        "url": "https://events.example.org/leadership-seminar",
        "snippet": (
            "A half-day seminar on planning and delivering community impact "
            "projects, with a panel of experienced chapter alumni."
        ),
    },
    {
        "offset_days": 21,
        "title": "Chapter Social: Volunteering & Games Night",
        "url": "https://events.example.org/volunteering-games-night",
        "snippet": (
            "A relaxed social evening combining a short volunteering activity "
            "with board games and networking."
        ),
    },
]


class DemoSearchBackend:
    """A deterministic, offline :class:`SearchBackend` for demos.

    Returns a fixed catalog of upcoming local events dated relative to the
    query's ``window_start`` (the agent's ``request_date``) so they fall inside
    the next-30-day window. Never raises, always returns the same list for a
    given request date. See the module docstring for why this does not weaken
    the anti-fabrication guarantee.
    """

    def search(self, params: "SearchQuery") -> Iterable[Any]:
        request_date = params.window_start
        hits: List[dict] = []
        for tpl in _DEMO_EVENT_TEMPLATES:
            hits.append(
                {
                    "title": tpl["title"],
                    "event_date": request_date + timedelta(days=tpl["offset_days"]),
                    "url": tpl["url"],
                    "snippet": tpl["snippet"],
                }
            )
        return hits


__all__ = ["DemoSearchBackend"]
