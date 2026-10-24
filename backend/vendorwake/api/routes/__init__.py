"""API routers, mounted under /api by `create_app`."""

from fastapi import APIRouter

from vendorwake.api.routes import health, me

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(me.router)

__all__ = ["api_router"]
