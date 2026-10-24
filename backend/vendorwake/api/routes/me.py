"""GET /api/me: the authenticated user, as seen in their access token."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from vendorwake.core.security import CurrentUser

router = APIRouter(tags=["identity"])


class MeResponse(BaseModel):
    subject: str
    email: str | None
    scopes: list[str]


@router.get("/me", response_model=MeResponse, summary="Current user")
def me(principal: CurrentUser) -> MeResponse:
    return MeResponse(subject=principal.subject, email=principal.email, scopes=sorted(principal.scopes))
