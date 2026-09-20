"""
models/worker.py
----------------
Factory floor worker entities with pseudonymized privacy tokens.
Compliant with GDPR Art. 9 and Works Council labor regulations.
"""

import uuid
from sqlalchemy import String, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base, TimestampMixin


class Worker(Base, TimestampMixin):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    site_id: Mapped[str] = mapped_column(String(36), ForeignKey("sites.id"), nullable=False)
    
    # Pseudonymized token used in video telemetry to avoid storing plain biometric PII
    anonymized_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    badge_reference: Mapped[str] = mapped_column(String(100), nullable=True)
    display_alias: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "Worker-A14"
    shift_code: Mapped[str] = mapped_column(String(50), default="SHIFT-MORNING", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    site = relationship("Site", back_populates="workers")
    decision_logs = relationship("DecisionLog", back_populates="worker")
