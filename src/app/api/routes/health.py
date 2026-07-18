"""Health/readiness endpoint (US2, FR-003)."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    """Payload returned by the health endpoint."""

    status: str
    service: str


@router.get("/health", response_model=HealthStatus)
async def health() -> HealthStatus:
    """Report service liveness."""

    return HealthStatus(status="ok", service=get_settings().app_name)
