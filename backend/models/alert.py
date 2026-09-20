"""
models/alert.py
---------------
Real-time supervisor safety alerts and multi-tier escalation tracking.
"""

from enum import Enum
import uuid
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base, TimestampMixin


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SafetyAlert(Base, TimestampMixin):
    __tablename__ = "safety_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    decision_log_id: Mapped[str] = mapped_column(String(36), ForeignKey("decision_logs.id"), nullable=False)
    site_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(String(1024), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default=AlertSeverity.WARNING.value, nullable=False)
    
    # Acknowledgment lifecycle
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    acknowledged_by_user_id: Mapped[str] = mapped_column(String(36), nullable=True)
    acknowledged_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=True)

    escalation_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    decision = relationship("DecisionLog", back_populates="alerts")
    escalations = relationship("AlertEscalation", back_populates="alert", cascade="all, delete-orphan")


class AlertEscalation(Base, TimestampMixin):
    """Tracks automatic escalation events when critical alerts remain unacknowledged."""
    __tablename__ = "alert_escalations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    alert_id: Mapped[str] = mapped_column(String(36), ForeignKey("safety_alerts.id"), nullable=False)
    escalated_to_channel: Mapped[str] = mapped_column(String(50), nullable=False)  # SMS, EMAIL, SLACK, TEAMS
    recipient_target: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="DISPATCHED", nullable=False)

    alert = relationship("SafetyAlert", back_populates="escalations")
