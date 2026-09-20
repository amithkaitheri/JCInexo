"""routes_events.py — nearby events + AI outreach drafting.

Two endpoints power the "Events & Outreach" tab:

    GET  /api/events/nearby
        A curated list of upcoming JCI-relevant events near the chapter. In this
        build the list is a deterministic, seeded catalogue (a production
        deployment would call a real events/Eventbrite/Meetup API here). Each
        event carries ``tags`` used to match members by their area of interest.

    POST /api/events/outreach
        Given an event, find the members whose ``area_of_interest`` matches the
        event's tags/title, then draft an outreach email inviting them. The email
        is written by Gemini when configured (warmer, personalized tone) and
        falls back to a deterministic template when the LLM is unavailable — so
        the feature always works offline / without an API key.

Design notes (mirrors the rest of the API): the route is a thin translator over
the Repository and a pure matching helper; the LLM is optional and isolated in
``io/gemini_client`` with a guaranteed fallback, so no request can hang or fail
because of the model.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from member_tracker.api.app import get_repository
from member_tracker.io.repository import MemberRecord, Repository

logger = logging.getLogger("member_tracker.events")

router = APIRouter(prefix="/api/events", tags=["events"])


# ---------------------------------------------------------------------------
# Seeded "nearby events" catalogue
# ---------------------------------------------------------------------------
#
# Deterministic sample events tagged with interest categories. A production
# build would replace this with a live events-API adapter; the shape (and the
# `tags` used for member matching) stays the same.
_NEARBY_EVENTS: List[dict] = [
    {
        "id": "evt-leadership-summit",
        "title": "JCI Ottawa Leadership Summit",
        "date": "2026-10-04",
        "location": "Ottawa City Hall, ON",
        "category": "Leadership",
        "tags": ["leadership", "management", "public speaking", "training"],
        "description": (
            "A half-day summit with keynote speakers on servant leadership, "
            "breakout workshops, and a panel of past JCI presidents."
        ),
    },
    {
        "id": "evt-community-cleanup",
        "title": "Rideau Canal Community Clean-Up",
        "date": "2026-10-11",
        "location": "Rideau Canal, Ottawa, ON",
        "category": "Community",
        "tags": ["community", "environment", "volunteering", "sustainability"],
        "description": (
            "A hands-on community service morning restoring the canal pathways, "
            "followed by a networking brunch."
        ),
    },
    {
        "id": "evt-public-speaking",
        "title": "Public Speaking & Debate Workshop",
        "date": "2026-10-18",
        "location": "Ottawa Public Library, Main Branch",
        "category": "Skills",
        "tags": ["public speaking", "communication", "debate", "training"],
        "description": (
            "An interactive workshop to sharpen presentation skills, with live "
            "practice rounds and peer feedback."
        ),
    },
    {
        "id": "evt-business-networking",
        "title": "Young Entrepreneurs Networking Night",
        "date": "2026-10-24",
        "location": "Bayview Yards, Ottawa, ON",
        "category": "Business",
        "tags": ["business", "entrepreneurship", "networking", "startups"],
        "description": (
            "Meet local founders and investors, pitch your ideas, and grow your "
            "professional network over the evening."
        ),
    },
    {
        "id": "evt-wellness-run",
        "title": "JCI Wellness 5K Charity Run",
        "date": "2026-11-01",
        "location": "Gatineau Park Trails",
        "category": "Wellness",
        "tags": ["wellness", "sports", "health", "community"],
        "description": (
            "A friendly 5K run raising funds for local youth programs — all "
            "fitness levels welcome."
        ),
    },
]


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


class EventModel(BaseModel):
    """A nearby event the admin can promote to members."""

    id: str
    title: str
    date: str
    location: str
    category: str
    tags: List[str]
    description: str


class NearbyEventsResponse(BaseModel):
    events: List[EventModel]


class OutreachRequest(BaseModel):
    """Body for ``POST /api/events/outreach``.

    Either reference a seeded event by ``event_id`` or pass a fully custom event
    (title/date/etc.) the admin typed in. Custom fields override the looked-up
    event when both are given.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None, max_length=200)
    date: Optional[str] = Field(default=None, max_length=40)
    location: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, max_length=1000)
    tags: Optional[List[str]] = Field(default=None)


class MatchedMember(BaseModel):
    id: str
    name: str
    stage: str
    area_of_interest: Optional[str] = None
    match_reason: str


class OutreachResponse(BaseModel):
    """The drafted outreach email + the members it targets."""

    event_title: str
    subject: str
    body: str
    source: str  # "gemini" | "fallback"
    recipients: List[MatchedMember]
    recipient_count: int


class SendOutreachRequest(BaseModel):
    """Body for ``POST /api/events/outreach/send`` — send a drafted email."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=6000)
    recipients: List[MatchedMember] = Field(default_factory=list)
    event_title: Optional[str] = Field(default=None, max_length=200)


class SentRecipient(BaseModel):
    id: str
    name: str
    status: str  # "sent"


class SendOutreachResponse(BaseModel):
    """Result of dispatching an outreach email to matched recipients."""

    ok: bool
    sent_count: int
    delivered: List[SentRecipient]
    message: str


# ---------------------------------------------------------------------------
# Matching (pure-ish helper)
# ---------------------------------------------------------------------------


def _tokenize(text: Optional[str]) -> set:
    """Lowercase word/token set from a free-text field."""
    if not text:
        return set()
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {tok for tok in cleaned.split() if len(tok) > 2}


def _match_members(
    members: List[MemberRecord], tags: List[str], title: str
) -> List[MatchedMember]:
    """Match members to an event by interest-token overlap.

    A member matches when any token of their ``area_of_interest`` overlaps the
    event's tags or title tokens. Members with no recorded interest are included
    as a general/open invitation so the admin can still reach everyone — but
    interest-matched members are surfaced first with a clear reason.
    """
    event_tokens = set()
    for tag in tags or []:
        event_tokens |= _tokenize(tag)
    event_tokens |= _tokenize(title)

    matched: List[MatchedMember] = []
    general: List[MatchedMember] = []
    for m in members:
        # Skip inactive members for outreach.
        if m.stage.value == "Inactive":
            continue
        interest_tokens = _tokenize(m.area_of_interest)
        overlap = interest_tokens & event_tokens
        if overlap:
            matched.append(
                MatchedMember(
                    id=m.id,
                    name=m.name,
                    stage=m.stage.value,
                    area_of_interest=m.area_of_interest,
                    match_reason=f"interest match: {', '.join(sorted(overlap))}",
                )
            )
        elif not m.area_of_interest:
            general.append(
                MatchedMember(
                    id=m.id,
                    name=m.name,
                    stage=m.stage.value,
                    area_of_interest=None,
                    match_reason="general invitation (no interest on file)",
                )
            )
    # Interest matches first, then general invitations.
    return matched + general


# ---------------------------------------------------------------------------
# Email drafting (LLM with deterministic fallback)
# ---------------------------------------------------------------------------


def _fallback_email(event: dict, recipients: List[MatchedMember]) -> tuple:
    """Deterministic outreach email used when the LLM is unavailable."""
    names = ", ".join(r.name.split()[0] for r in recipients[:8])
    more = "" if len(recipients) <= 8 else f" and {len(recipients) - 8} more"
    subject = f"You're invited: {event['title']} ({event['date']})"
    body = (
        f"Hi {names or 'JCI members'}{more},\n\n"
        f"We're excited to invite you to *{event['title']}* on {event['date']} "
        f"at {event['location']}.\n\n"
        f"{event['description']}\n\n"
        f"We thought of you because it aligns with your interests in "
        f"{event.get('category', 'our chapter activities').lower()}. It's a great "
        f"chance to grow, connect with fellow members, and make an impact in our "
        f"community.\n\n"
        f"Can we count you in? Reply to this email or reach out to a board member "
        f"to RSVP.\n\n"
        f"Warm regards,\n"
        f"Your JCI Ottawa Team 💓"
    )
    return subject, body


def _draft_email(event: dict, recipients: List[MatchedMember]) -> tuple:
    """Draft the outreach email via Gemini, falling back deterministically.

    Returns ``(subject, body, source)`` where source is ``"gemini"`` or
    ``"fallback"``.
    """
    try:
        from member_tracker.io import gemini_client

        if not gemini_client.is_configured():
            raise gemini_client.GeminiError("not configured")

        interests = sorted(
            {r.area_of_interest for r in recipients if r.area_of_interest}
        )
        system = (
            "You are the outreach coordinator for JCI Ottawa (a Junior Chamber "
            "International local organization). Write warm, concise, motivating "
            "invitation emails to members. Keep it under 180 words, friendly and "
            "professional, with a clear call to action to RSVP. Do NOT invent "
            "facts beyond what you're given."
        )
        prompt = (
            f"Draft an outreach email inviting chapter members to this event.\n\n"
            f"EVENT: {event['title']}\n"
            f"DATE: {event['date']}\n"
            f"LOCATION: {event['location']}\n"
            f"CATEGORY: {event.get('category', '')}\n"
            f"DESCRIPTION: {event['description']}\n"
            f"RECIPIENT COUNT: {len(recipients)}\n"
            f"RECIPIENT INTERESTS: {', '.join(interests) or 'general chapter members'}\n\n"
            f"Return the email as:\n"
            f"Subject: <subject line>\n"
            f"<blank line>\n"
            f"<email body>\n"
            f"Address recipients warmly (you may use 'Hi JCI members'). Sign off "
            f"as 'Your JCI Ottawa Team'."
        )
        text = gemini_client.generate_text(prompt, system=system)

        # Parse a leading "Subject:" line if present.
        subject = f"You're invited: {event['title']}"
        body = text
        for line in text.splitlines():
            if line.strip().lower().startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
                body = text.split(line, 1)[1].lstrip("\n")
                break
        return subject, body, "gemini"
    except Exception as exc:  # any LLM failure -> deterministic fallback
        logger.info("Outreach LLM unavailable, using fallback: %s", exc)
        subject, body = _fallback_email(event, recipients)
        return subject, body, "fallback"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/nearby", response_model=NearbyEventsResponse)
def nearby_events() -> NearbyEventsResponse:
    """Return the curated list of nearby JCI-relevant events."""
    return NearbyEventsResponse(events=[EventModel(**e) for e in _NEARBY_EVENTS])


@router.post("/outreach", response_model=OutreachResponse)
def draft_outreach(
    body: OutreachRequest,
    repo: Repository = Depends(get_repository),
) -> OutreachResponse:
    """Match relevant members and draft an outreach email for an event."""
    # Resolve the event: start from a seeded event if referenced, then apply any
    # custom overrides the admin supplied.
    event: dict = {}
    if body.event_id:
        event = next(
            (dict(e) for e in _NEARBY_EVENTS if e["id"] == body.event_id), {}
        )
    if body.title:
        event["title"] = body.title
    if body.date:
        event["date"] = body.date
    if body.location:
        event["location"] = body.location
    if body.category:
        event["category"] = body.category
    if body.description:
        event["description"] = body.description
    if body.tags is not None:
        event["tags"] = body.tags

    # Sensible defaults so the draft never has holes.
    event.setdefault("title", "JCI Ottawa Event")
    event.setdefault("date", "soon")
    event.setdefault("location", "Ottawa, ON")
    event.setdefault("category", "Community")
    event.setdefault("description", "A JCI Ottawa chapter event.")
    event.setdefault("tags", [event.get("category", "").lower()])

    members = repo.list_members()
    recipients = _match_members(members, event.get("tags", []), event["title"])

    subject, email_body, source = _draft_email(event, recipients)

    return OutreachResponse(
        event_title=event["title"],
        subject=subject,
        body=email_body,
        source=source,
        recipients=recipients,
        recipient_count=len(recipients),
    )


@router.post("/outreach/send", response_model=SendOutreachResponse)
def send_outreach(body: SendOutreachRequest) -> SendOutreachResponse:
    """Dispatch a drafted outreach email to all matched recipients.

    This build simulates delivery (no live SMTP/email provider is wired up): it
    records each recipient as "sent" and logs the dispatch, so the demo shows a
    single-click send to every matched member. Swapping in a real provider
    (SendGrid, SES, etc.) is a drop-in replacement for the loop below — the API
    contract and the UI stay the same.
    """
    delivered = [
        SentRecipient(id=r.id, name=r.name, status="sent") for r in body.recipients
    ]
    logger.info(
        "Outreach '%s' sent to %d recipient(s): %s",
        body.event_title or body.subject,
        len(delivered),
        ", ".join(r.name for r in delivered) or "(none)",
    )
    n = len(delivered)
    if n == 0:
        message = "No recipients to send to."
    else:
        message = (
            f"Sent to {n} matched recipient{'s' if n != 1 else ''}."
        )
    return SendOutreachResponse(
        ok=n > 0,
        sent_count=n,
        delivered=delivered,
        message=message,
    )


__all__ = ["router"]
