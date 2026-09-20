"""
models/__init__.py
------------------
Re-export all SQLAlchemy models for clean imports and Alembic autogenerate discovery.
"""

from core.database import Base
from models.org import Company, Site
from models.user import User
from models.worker import Worker
from models.camera import Camera
from models.zone import SafetyZone, SafetyZoneHistory, RiskTier
from models.cobot import Cobot, CobotTask, CobotStatus
from models.decision import DecisionLog, IncidentExplanation, SafetyAction
from models.alert import SafetyAlert, AlertEscalation, AlertSeverity
from models.audit import AuditLog

__all__ = [
    "Base",
    "Company",
    "Site",
    "User",
    "Worker",
    "Camera",
    "SafetyZone",
    "SafetyZoneHistory",
    "RiskTier",
    "Cobot",
    "CobotTask",
    "CobotStatus",
    "DecisionLog",
    "IncidentExplanation",
    "SafetyAction",
    "SafetyAlert",
    "AlertEscalation",
    "AlertSeverity",
    "AuditLog"
]
