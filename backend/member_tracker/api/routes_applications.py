"""routes_applications.py — membership applications ("Messages") + retention.

The president's Messages tab is backed by inbound membership applications. Flow:

    POST /api/applications                — a prospective member "pays" & applies
                                            (creates a pending application/message)
    GET  /api/applications                — list applications (the Messages inbox)
    POST /api/applications/{id}/decision  — approve or decline; drafts a decision
                                            email (Gemini w/ template fallback);
                                            on approval also creates a MEMBER
    POST /api/applications/{id}/meeting   — draft & attach a sync-up meeting invite
    GET  /api/retention                   — chapter retention rate (for Chapter Pulse)
    POST /api/retention/renewal           — log a member renewal (feeds retention)

Emails/invites are drafted by Gemini when configured and fall back to a
deterministic template otherwise, so the feature always works offline.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from member_tracker.api.app import get_clock, get_repository, require_api_key
from member_tracker.core.clock import Clock
from member_tracker.io.repository import ApplicationRecord, Repository

logger = logging.getLogger("member_tracker.applications")

router = APIRouter(prefix="/api", tags=["applications"])


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


class CreateApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=3, max_length=200)
    area_of_interest: Optional[str] = Field(default=None, max_length=200)
    amount_paid: float = Field(default=0, ge=0)


class ApplicationModel(BaseModel):
    id: int
    applicant_name: str
    email: str
    area_of_interest: Optional[str] = None
    amount_paid: float
    status: str
    created_at: str
    decided_at: Optional[str] = None
    decision_email: Optional[str] = None
    meeting_invite: Optional[str] = None
    meeting_at: Optional[str] = None
    created_member_id: Optional[str] = None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(..., pattern="^(approve|decline)$")
    reason: Optional[str] = Field(default=None, max_length=500)


class DecisionResponse(BaseModel):
    application: ApplicationModel
    email_source: str  # "gemini" | "fallback"


class MeetingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_at: Optional[str] = Field(default=None, max_length=60)
    location: Optional[str] = Field(default=None, max_length=200)


class MeetingResponse(BaseModel):
    application: ApplicationModel
    email_source: str


class RenewalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str
    renewed: bool = True
    period: Optional[str] = Field(default=None, max_length=40)


class RetentionResponse(BaseModel):
    eligible: int
    renewed: int
    retention_rate: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_model(rec: ApplicationRecord) -> ApplicationModel:
    return ApplicationModel(
        id=rec.id,
        applicant_name=rec.applicant_name,
        email=rec.email,
        area_of_interest=rec.area_of_interest,
        amount_paid=rec.amount_paid,
        status=rec.status,
        created_at=rec.created_at,
        decided_at=rec.decided_at,
        decision_email=rec.decision_email,
        meeting_invite=rec.meeting_invite,
        meeting_at=rec.meeting_at,
        created_member_id=rec.created_member_id,
    )


def _chapter(repo: Repository) -> str:
    try:
        return repo.get_identity().get("chapter_name", "JCI Ottawa")
    except Exception:  # pragma: no cover
        return "JCI Ottawa"


def _fallback_decision_email(
    app: ApplicationRecord, approved: bool, chapter: str, reason: Optional[str]
) -> str:
    first = app.applicant_name.split()[0] if app.applicant_name else "there"
    if approved:
        return (
            f"Hi {first},\n\n"
            f"Great news — your application to join {chapter} has been "
            f"approved! 🎉 We're thrilled to welcome you to our chapter.\n\n"
            f"A board member will reach out shortly to help you get started and "
            f"invite you to your first events. Welcome aboard!\n\n"
            f"Warm regards,\n{chapter} Membership Team"
        )
    return (
        f"Hi {first},\n\n"
        f"Thank you for your interest in joining {chapter} and for taking the "
        f"time to apply.\n\n"
        f"After careful review, we're unable to move forward with your "
        f"membership at this time"
        + (f" — {reason}" if reason else "")
        + ".\n\nWe truly appreciate your interest and encourage you to stay "
        f"connected with our public events. Your payment will be refunded.\n\n"
        f"Warm regards,\n{chapter} Membership Team"
    )


def _draft_decision_email(
    app: ApplicationRecord, approved: bool, chapter: str, reason: Optional[str]
) -> tuple:
    """Return ``(email_text, source)`` — Gemini when available, else template."""
    try:
        from member_tracker.io import gemini_client

        if not gemini_client.is_configured():
            raise gemini_client.GeminiError("not configured")
        system = (
            f"You are the membership coordinator for {chapter}, a Junior Chamber "
            "International chapter. Write a warm, concise, professional email "
            "(under 150 words) to an applicant about our membership decision. "
            "Do not invent facts."
        )
        verdict = "APPROVED" if approved else "DECLINED"
        prompt = (
            f"Write the {verdict} decision email.\n"
            f"Applicant: {app.applicant_name}\n"
            f"Interests: {app.area_of_interest or 'not specified'}\n"
            f"Decision: {verdict}\n"
            + (f"Reason/context: {reason}\n" if reason else "")
            + (
                "Be welcoming and excited; invite them to their first event.\n"
                if approved
                else "Be kind and encouraging; note their payment will be refunded.\n"
            )
            + f"Sign off as '{chapter} Membership Team'."
        )
        text = gemini_client.generate_text(prompt, system=system)
        return text, "gemini"
    except Exception as exc:
        logger.info("Decision email LLM unavailable, using fallback: %s", exc)
        return _fallback_decision_email(app, approved, chapter, reason), "fallback"


def _fallback_meeting_invite(
    app: ApplicationRecord, chapter: str, when: Optional[str], location: Optional[str]
) -> str:
    first = app.applicant_name.split()[0] if app.applicant_name else "there"
    when_txt = when or "a time that works for you next week"
    where_txt = location or "a video call (link to follow)"
    return (
        f"Hi {first},\n\n"
        f"We'd love to set up a quick sync-up to welcome you and answer any "
        f"questions about {chapter}.\n\n"
        f"Proposed time: {when_txt}\n"
        f"Where: {where_txt}\n\n"
        f"Does this work for you? Reply to confirm and we'll send a calendar "
        f"invite.\n\nLooking forward to connecting!\n{chapter} Membership Team"
    )


def _draft_meeting_invite(
    app: ApplicationRecord, chapter: str, when: Optional[str], location: Optional[str]
) -> tuple:
    try:
        from member_tracker.io import gemini_client

        if not gemini_client.is_configured():
            raise gemini_client.GeminiError("not configured")
        system = (
            f"You are the membership coordinator for {chapter}. Write a short, "
            "friendly meeting-invite email (under 120 words) proposing a sync-up "
            "call with a new/prospective member. Do not invent facts."
        )
        prompt = (
            f"Write a sync-up meeting invitation email.\n"
            f"Recipient: {app.applicant_name}\n"
            f"Proposed time: {when or 'suggest next week'}\n"
            f"Location: {location or 'a video call'}\n"
            f"Ask them to confirm. Sign off as '{chapter} Membership Team'."
        )
        text = gemini_client.generate_text(prompt, system=system)
        return text, "gemini"
    except Exception as exc:
        logger.info("Meeting invite LLM unavailable, using fallback: %s", exc)
        return _fallback_meeting_invite(app, chapter, when, location), "fallback"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/applications", response_model=ApplicationModel, status_code=201)
def create_application(
    body: CreateApplicationRequest,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> ApplicationModel:
    """A prospective member pays & applies — creates a pending application."""
    rec = repo.create_application(
        applicant_name=body.applicant_name,
        email=body.email,
        area_of_interest=body.area_of_interest,
        amount_paid=body.amount_paid,
        created_at=clock.current_time(),
    )
    return _to_model(rec)


@router.get("/applications", response_model=List[ApplicationModel])
def list_applications(
    status_filter: Optional[str] = None,
    repo: Repository = Depends(get_repository),
) -> List[ApplicationModel]:
    """List membership applications (the president's Messages inbox)."""
    return [_to_model(r) for r in repo.list_applications(status=status_filter)]


@router.post(
    "/applications/{application_id}/decision",
    response_model=DecisionResponse,
    dependencies=[Depends(require_api_key)],
)
def decide_application(
    application_id: int,
    body: DecisionRequest,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> DecisionResponse:
    """Approve or decline an application; draft the decision email; create member."""
    app = repo.get_application(application_id)
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "application_not_found", "message": "Application not found."},
        )

    approved = body.decision == "approve"
    chapter = _chapter(repo)
    email_text, source = _draft_decision_email(app, approved, chapter, body.reason)

    created_member_id: Optional[str] = None
    if approved:
        # Create the actual member as a Prospective member on approval.
        result = repo.create_member(
            name=app.applicant_name,
            stage="Prospective",
            age_out_date=None,
            area_of_interest=app.area_of_interest,
        )
        if result.is_ok:
            created_member_id = result.value.id
            # Log their membership payment as an activity? No — just record a
            # renewal entry so they count toward retention going forward.
            repo.record_renewal(
                created_member_id, renewed=True, period=None, recorded_at=clock.current_time()
            )

    updated = repo.decide_application(
        application_id=application_id,
        status="approved" if approved else "declined",
        decision_email=email_text,
        decided_at=clock.current_time(),
        created_member_id=created_member_id,
    )
    return DecisionResponse(application=_to_model(updated), email_source=source)


@router.post(
    "/applications/{application_id}/meeting",
    response_model=MeetingResponse,
    dependencies=[Depends(require_api_key)],
)
def send_meeting_invite(
    application_id: int,
    body: MeetingRequest,
    repo: Repository = Depends(get_repository),
) -> MeetingResponse:
    """Draft and attach a sync-up meeting invite to an application."""
    app = repo.get_application(application_id)
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "application_not_found", "message": "Application not found."},
        )
    chapter = _chapter(repo)
    invite_text, source = _draft_meeting_invite(app, chapter, body.meeting_at, body.location)
    updated = repo.set_application_meeting(
        application_id=application_id,
        meeting_invite=invite_text,
        meeting_at=body.meeting_at,
    )
    return MeetingResponse(application=_to_model(updated), email_source=source)


@router.get("/retention", response_model=RetentionResponse)
def retention(repo: Repository = Depends(get_repository)) -> RetentionResponse:
    """Return the chapter retention rate for Chapter Pulse."""
    stats = repo.retention_stats()
    return RetentionResponse(**stats)


@router.post(
    "/retention/renewal",
    response_model=RetentionResponse,
    dependencies=[Depends(require_api_key)],
)
def record_renewal(
    body: RenewalRequest,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> RetentionResponse:
    """Log a member renewal decision (feeds the retention rate)."""
    repo.record_renewal(
        member_id=body.member_id,
        renewed=body.renewed,
        period=body.period,
        recorded_at=clock.current_time(),
    )
    return RetentionResponse(**repo.retention_stats())


__all__ = ["router"]
