"""routes_member_auth.py — ImpactQuest member authentication.

Endpoints:
    POST /api/member/register   — LP provisions a login for an existing member
    POST /api/member/login      — member email + password → JWT access token
    GET  /api/member/me         — return the authenticated member's Basecamp profile
    POST /api/member/logout     — client-side; returns 200 (token is stateless)

Design:
- Credentials are stored in MEMBER_CREDENTIAL (bcrypt hash, never plaintext).
- Auth uses a stateless signed JWT (HS256) with a 24-hour expiry. The secret is
  read from the MEMBER_JWT_SECRET env var; a hard-coded dev fallback is used
  when the var is absent so the app starts without configuration.
- The ``require_member_token`` dependency validates the Bearer token and injects
  the ``member_id`` string into any protected route.
- Registration is guarded by the admin API key so only the LP can provision
  member accounts (members cannot self-register without LP approval).
- The /me response bundles everything the Basecamp UI needs: rank, points,
  badges, streak, and health score — one fetch on login.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field

from member_tracker.api.app import get_clock, get_repository, require_api_key
from member_tracker.core import activity_points
from member_tracker.core.clock import Clock
from member_tracker.io.repository import Repository

router = APIRouter(prefix="/api/member", tags=["member-auth"])

# ---------------------------------------------------------------------------
# JWT config
# ---------------------------------------------------------------------------

_JWT_SECRET = os.getenv("MEMBER_JWT_SECRET", "impactquest-dev-secret-change-in-prod")
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_HOURS = 24


def _make_token(member_id: str) -> str:
    """Issue a signed JWT for a member."""
    payload = {
        "sub": member_id,
        "role": "member",
        "exp": datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def _decode_token(token: str) -> str:
    """Validate a JWT and return the member_id (sub claim). Raises 401 on failure."""
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        member_id: Optional[str] = payload.get("sub")
        if not member_id or payload.get("role") != "member":
            raise HTTPException(status_code=401, detail="Invalid token.")
        return member_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Token expired or invalid.")


def require_member_token(
    authorization: str = Header(default="", alias="Authorization"),
) -> str:
    """FastAPI dependency: extract and validate the Bearer member JWT.

    Returns the authenticated ``member_id``. Routes that need the member
    identity declare ``member_id: str = Depends(require_member_token)``.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header.",
        )
    return _decode_token(authorization[len("Bearer "):])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    member_id: str = Field(..., min_length=1, max_length=20)
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=8, max_length=200)


class RegisterResponse(BaseModel):
    ok: bool
    member_id: str
    email: str


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=1, max_length=200)


class LoginResponse(BaseModel):
    ok: bool
    access_token: str
    token_type: str = "bearer"
    member_id: str
    name: str
    rank_name: str
    total_points: int


class BadgeOut(BaseModel):
    badge_id: str
    badge_name: str
    unlocked_at: str


class PointsTierOut(BaseModel):
    key: str
    label: str
    icon: str
    threshold: int
    unlocked: bool
    current: bool


class BasecampResponse(BaseModel):
    member_id: str
    name: str
    stage: str
    email: str
    total_points: int
    rank_name: str
    rank_icon: str
    tier_label: str
    points_to_next: Optional[int]
    next_tier_label: Optional[str]
    points_nodes: list[PointsTierOut]
    health_score: Optional[int]
    at_risk: bool
    attended_event_count: int
    earned_badges: list[BadgeOut]
    current_streak: int
    longest_streak: int
    last_played_date: Optional[str]


# ---------------------------------------------------------------------------
# Rank name helper (maps activity_points tier → Owl Trail rank)
# ---------------------------------------------------------------------------

_RANK_MAP = {
    "rookie":      ("Owlet",       "🐣"),
    "contributor": ("Scout",       "🦉"),
    "active":      ("Glider",      "🌲"),
    "leader":      ("Ranger",      "🏔️"),
    "champion":    ("Wise Owl",    "👑"),
}


def _rank(total_points: int) -> tuple[str, str]:
    tier = activity_points.current_tier(total_points)
    return _RANK_MAP.get(tier.key, ("Owlet", "🐣"))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
    summary="LP provisions a member login (admin-guarded)",
)
def register_member(
    body: RegisterRequest,
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> RegisterResponse:
    """Provision an ImpactQuest login for an existing MEMBER record.

    Only the LP (admin API key required) can create member logins, ensuring
    accounts are tied to approved members. Returns 404 if the member_id doesn't
    exist, 409 if the email or member already has a credential.
    """
    # Member must already exist in the MEMBER table.
    if repo.get_member(body.member_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "member_not_found", "message": f"No member with id {body.member_id!r}."},
        )

    pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()

    try:
        repo.create_member_credential(body.member_id, body.email, pw_hash)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "credential_exists", "message": "Email or member already registered."},
        ) from exc

    return RegisterResponse(ok=True, member_id=body.member_id, email=body.email.lower().strip())


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Member email + password → JWT",
)
def member_login(
    body: LoginRequest,
    repo: Repository = Depends(get_repository),
) -> LoginResponse:
    """Authenticate a member and return a JWT access token."""
    cred = repo.get_credential_by_email(body.email)
    if cred is None or not bcrypt.checkpw(body.password.encode(), cred["password_hash"].encode()):
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_credentials", "message": "Invalid email or password."},
        )

    member = repo.get_member(cred["member_id"])
    if member is None:
        raise HTTPException(status_code=404, detail="Member record not found.")

    repo.touch_last_login(cred["member_id"])
    token = _make_token(cred["member_id"])
    total = repo.total_activity_points(cred["member_id"])
    rank_name, _ = _rank(total)

    return LoginResponse(
        ok=True,
        access_token=token,
        member_id=member.id,
        name=member.name,
        rank_name=rank_name,
        total_points=total,
    )


@router.get(
    "/me",
    response_model=BasecampResponse,
    summary="Authenticated member's Basecamp profile",
)
def get_me(
    member_id: str = Depends(require_member_token),
    repo: Repository = Depends(get_repository),
    clock: Clock = Depends(get_clock),
) -> BasecampResponse:
    """Return everything the Basecamp UI needs in one call.

    Bundles: rank, points tier progress, badges, health score, and streak.
    """
    from member_tracker.core.attendance import attended_count

    member = repo.get_member(member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found.")

    cred = None
    conn = repo._connect()
    try:
        row = conn.execute(
            "SELECT email FROM MEMBER_CREDENTIAL WHERE member_id = ?", (member_id,)
        ).fetchone()
        if row:
            cred_email = row["email"]
        else:
            cred_email = ""
    finally:
        conn.close()

    total_points = repo.total_activity_points(member_id)
    tier_now = activity_points.current_tier(total_points)
    tier_next = activity_points.next_tier(total_points)
    rank_name, rank_icon = _rank(total_points)

    config = repo.get_config()
    health = repo.get_health_score(member_id)
    health_score = health.score if health else None
    at_risk = health_score is not None and health_score <= config.at_risk_threshold

    count = attended_count(repo.list_attendance(member_id))
    earned_records = repo.list_earned_badges(member_id)
    streak = repo.get_streak(member_id)

    points_nodes = [
        PointsTierOut(
            key=t.key,
            label=t.label,
            icon=t.icon,
            threshold=t.threshold,
            unlocked=total_points >= t.threshold,
            current=(t.key == tier_now.key),
        )
        for t in activity_points.tiers()
    ]

    return BasecampResponse(
        member_id=member.id,
        name=member.name,
        stage=member.stage.value,
        email=cred_email,
        total_points=total_points,
        rank_name=rank_name,
        rank_icon=rank_icon,
        tier_label=tier_now.label,
        points_to_next=(tier_next.threshold - total_points) if tier_next else None,
        next_tier_label=tier_next.label if tier_next else None,
        points_nodes=points_nodes,
        health_score=health_score,
        at_risk=at_risk,
        attended_event_count=count,
        earned_badges=[
            BadgeOut(
                badge_id=b.badge_id,
                badge_name=b.badge_name,
                unlocked_at=b.unlocked_at,
            )
            for b in earned_records
        ],
        current_streak=streak["current_streak"],
        longest_streak=streak["longest_streak"],
        last_played_date=streak["last_played_date"],
    )


@router.post("/logout", summary="Invalidate member session (client-side)")
def member_logout() -> dict:
    """No-op on the server — the JWT is stateless. The client discards the token."""
    return {"ok": True}


__all__ = ["router", "require_member_token", "_rank"]
