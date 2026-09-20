"""
api/v1/__init__.py
------------------
Assembles all v1 sub-routers into a unified API router.
"""

from fastapi import APIRouter
from api.v1.auth import router as auth_router
from api.v1.zones import router as zones_router
from api.v1.cameras import router as cameras_router
from api.v1.cobots import router as cobots_router
from api.v1.decisions import router as decisions_router
from api.v1.explain import router as explain_router
from api.v1.alerts import router as alerts_router
from api.v1.analytics import router as analytics_router
from api.v1.audit import router as audit_router
from api.v1.ws import router as ws_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)
api_v1_router.include_router(zones_router)
api_v1_router.include_router(cameras_router)
api_v1_router.include_router(cobots_router)
api_v1_router.include_router(decisions_router)
api_v1_router.include_router(explain_router)
api_v1_router.include_router(alerts_router)
api_v1_router.include_router(analytics_router)
api_v1_router.include_router(audit_router)
api_v1_router.include_router(ws_router)
