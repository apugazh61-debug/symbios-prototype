"""
models/audit.py
---------------
Enterprise security and compliance audit logging for every administrative action.
"""

import uuid
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from core.database import Base, TimestampMixin


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=True)
    user_email: Mapped[str] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # CREATE_ZONE, UPDATE_THRESHOLD, ACK_ALERT, COBOT_REALLOCATION
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)  # SafetyZone, Camera, Worker, CobotTask, Decision
    entity_id: Mapped[str] = mapped_column(String(36), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="info", nullable=False)  # info, warning, critical
    message: Mapped[str] = mapped_column(String(500), nullable=True)
    changes_json: Mapped[str] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str] = mapped_column(String(50), nullable=True)
