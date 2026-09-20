"""
models/camera.py
----------------
Factory camera sensors (RTSP / IP / Industrial WebRTC).
"""

import uuid
from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base, TimestampMixin


class Camera(Base, TimestampMixin):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    site_id: Mapped[str] = mapped_column(String(36), ForeignKey("sites.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    stream_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    protocol: Mapped[str] = mapped_column(String(50), default="RTSP", nullable=False)  # RTSP, WEBCAM, WEBRTC
    location_description: Mapped[str] = mapped_column(String(255), nullable=True)
    fps_target: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="ONLINE", nullable=False)  # ONLINE, OFFLINE, DEGRADED

    site = relationship("Site", back_populates="cameras")
    zones = relationship("SafetyZone", back_populates="camera", cascade="all, delete-orphan")
    decision_logs = relationship("DecisionLog", back_populates="camera")
