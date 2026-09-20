"""gemini_synthesizer.py — a Gemini-backed Wellington recommendation synthesizer.

Implements the ``RecommendationSynthesizer`` seam from ``io/wellington_agent`` by
prompting Gemini under the Wellington-the-Wise persona and parsing its JSON
response into a :class:`Retention_Recommendation` (``mode="llm"``).

Key safety properties (mirroring the rest of the codebase):

* If Gemini is not configured, ``synthesize`` raises
  :class:`LLMSynthesisNotConfigured` so ``generate_recommendation`` cleanly
  falls back to the deterministic template assembler.
* The returned events are *not* trusted — they are copied from the model output
  and then re-verified downstream by ``verify_recommendation`` against the real
  ``search_results`` (title/date/url must match). Any fabricated or out-of-window
  event is pruned there, so a hallucinating model cannot inject fake events.
* Dates in the model's JSON are parsed leniently (ISO ``YYYY-MM-DD``); an event
  whose date can't be parsed is simply skipped (the verifier would drop it
  anyway).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import List

from member_tracker.core.types import (
    MemberContext,
    Recommended_Event,
    Retention_Recommendation,
    Web_Search_Result,
)
from member_tracker.io.wellington_agent import (
    LLMSynthesisNotConfigured,
    WELLINGTON_SYSTEM_PROMPT,
)

logger = logging.getLogger("member_tracker.gemini_synthesizer")


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d",):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from the model's text response.

    Gemini sometimes wraps JSON in ``` fences or adds a stray sentence; grab the
    substring from the first ``{`` to the last ``}`` and parse that.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model response")
    return json.loads(text[start : end + 1])


class GeminiRecommendationSynthesizer:
    """Wellington retention synthesizer backed by Gemini.

    Constructed cheaply (no network); the network call happens in
    :meth:`synthesize`, guarded by ``generate_recommendation``'s deadline.
    """

    def synthesize(
        self,
        member: MemberContext,
        search_results: List[Web_Search_Result],
        request_date: date,
    ) -> Retention_Recommendation:
        from member_tracker.io import gemini_client

        if not gemini_client.is_configured():
            raise LLMSynthesisNotConfigured(
                "GEMINI_API_KEY not configured; using template fallback."
            )

        events_block = "\n".join(
            f"- title: {r.title} | date: {r.event_date.isoformat()} | url: {r.url}"
            + (f" | note: {r.snippet}" if r.snippet else "")
            for r in search_results
        ) or "(no events returned by search)"

        prompt = (
            f"MEMBER CONTEXT:\n"
            f"- name: {member.name}\n"
            f"- stage: {member.stage.value}\n"
            f"- interests: {', '.join(member.interests) or 'none on file'}\n"
            f"- health_score: {member.health_score}/100\n"
            f"- location: {member.location or 'Ottawa, ON'}\n\n"
            f"PROVIDED EVENTS (the ONLY events you may recommend, copy "
            f"title/date/url verbatim):\n{events_block}\n\n"
            f"Today's date is {request_date.isoformat()}.\n"
            f"Respond with the single JSON object described in your instructions."
        )

        text = gemini_client.generate_text(
            prompt, system=WELLINGTON_SYSTEM_PROMPT
        )
        data = _extract_json(text)

        diagnosis = str(data.get("diagnosis", "")).strip()
        outreach = str(data.get("outreach_template", "")).strip()

        events: List[Recommended_Event] = []
        for ev in data.get("recommended_events", []) or []:
            ev_date = _parse_date(str(ev.get("date", "")))
            if ev_date is None:
                continue
            events.append(
                Recommended_Event(
                    title=str(ev.get("title", "")).strip(),
                    event_date=ev_date,
                    url=str(ev.get("url", "")).strip(),
                    reason=str(ev.get("reason", "")).strip(),
                )
            )

        if not diagnosis or not outreach or not events:
            # Incomplete synthesis — let the agent fall back deterministically.
            raise LLMSynthesisNotConfigured(
                "Gemini returned an incomplete recommendation."
            )

        return Retention_Recommendation(
            diagnosis=diagnosis,
            events=events,
            outreach_template=outreach,
            mode="llm",
        )


__all__ = ["GeminiRecommendationSynthesizer"]
