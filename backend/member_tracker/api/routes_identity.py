"""routes_identity.py — role-based dashboard identity.

The dashboard is operated by the local chapter president. ``GET /api/whoami``
returns the president's name, chapter name, and role so the frontend can greet
them (e.g. "Welcome JCI Ottawa President Raj"). The values live in the CONFIG
store (seeded in io/database.py) so they are configurable per deployment.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from member_tracker.api.app import get_repository
from member_tracker.io.repository import Repository

router = APIRouter(prefix="/api", tags=["identity"])


class WhoAmIResponse(BaseModel):
    president_name: str
    chapter_name: str
    role: str
    greeting: str


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=200)


class LoginResponse(BaseModel):
    ok: bool
    president_name: str
    chapter_name: str
    role: str
    greeting: str


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    repo: Repository = Depends(get_repository),
) -> LoginResponse:
    """Authenticate the local chapter president.

    On success returns the identity payload the frontend uses to greet the
    president. On failure returns 401. This is a demo-grade credential check
    (see :meth:`Repository.verify_login`).
    """
    if not repo.verify_login(body.username, body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_credentials", "message": "Invalid username or password."},
        )
    identity = repo.get_identity()
    greeting = (
        f"Welcome {identity['chapter_name']} President {identity['president_name']}"
    )
    return LoginResponse(
        ok=True,
        president_name=identity["president_name"],
        chapter_name=identity["chapter_name"],
        role=identity["role"],
        greeting=greeting,
    )


@router.get("/whoami", response_model=WhoAmIResponse)
def whoami(repo: Repository = Depends(get_repository)) -> WhoAmIResponse:
    """Return the local president identity for the dashboard header."""
    identity = repo.get_identity()
    greeting = (
        f"Welcome {identity['chapter_name']} President {identity['president_name']}"
    )
    return WhoAmIResponse(
        president_name=identity["president_name"],
        chapter_name=identity["chapter_name"],
        role=identity["role"],
        greeting=greeting,
    )


__all__ = ["router"]
