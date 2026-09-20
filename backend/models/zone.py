"""
models/zone.py
--------------
Admin-configurable Polygon Safety Zones with risk tiers and immutable version audit history.
"""

from enum import Enum
import uuid
from sqlalchemy import String, Integer, Boolean, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base, TimestampMixin


class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SafetyZone(Base, TimestampMixin):
    __tablename__ = "safety_zones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    site_id: Mapped[str] = mapped_column(String(36), ForeignKey("sites.id"), nullable=False)
    camera_id: Mapped[str] = mapped_column(String(36), ForeignKey("cameras.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(50), default=RiskTier.HIGH.value, nullable=False)
    
    # Polygon coordinates normalized between 0.0 and 1.0: "[[0.65, 0.0], [1.0, 0.0], [1.0, 1.0], [0.65, 1.0]]"
    polygon_geojson: Mapped[str] = mapped_column(Text, nullable=False)
    
    color_hex: Mapped[str] = mapped_column(String(20), default="#E85C4A", nullable=False)
    reassignment_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    site = relationship("Site", back_populates="zones")
    camera = relationship("Camera", back_populates="zones")
    history = relationship("SafetyZoneHistory", back_populates="zone", cascade="all, delete-orphan")
    decision_logs = relationship("DecisionLog", back_populates="zone")


class SafetyZoneHistory(Base, TimestampMixin):
    """Immutable audit trail for compliance — tracks every change to industrial safety boundaries."""
    __tablename__ = "safety_zone_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    zone_id: Mapped[str] = mapped_column(String(36), ForeignKey("safety_zones.id"), nullable=False)
    modified_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_polygon: Mapped[str] = mapped_column(Text, nullable=True)
    new_polygon: Mapped[str] = mapped_column(Text, nullable=False)
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)

    zone = relationship("SafetyZone", back_populates="history")
