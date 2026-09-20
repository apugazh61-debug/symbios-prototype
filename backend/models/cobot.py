"""
models/cobot.py
---------------
Collaborative Robot (Cobot) stations, state monitoring, and dynamic task assignments.
"""

from enum import Enum
import uuid
from sqlalchemy import String, Text, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base, TimestampMixin


class CobotStatus(str, Enum):
    """
    Real 4-phase cobot state machine:
      IDLE       → Cobot is available and ready to accept a task assignment.
      ASSIGNED   → Decision engine has reserved this cobot for a specific task;
                   it is no longer available for new assignments, but has not
                   yet reached the workstation.
      ACTIVE     → Cobot is physically at the workstation performing the task.
      RETURNING  → Cobot has finished and is travelling back to its home position.
                   Returns automatically to IDLE on dock-home acknowledgment.
      FAULT      → Hardware or software fault; requires operator clearance.
      ESTOP      → Emergency stop engaged; requires physical E-stop reset.
    """
    IDLE = "IDLE"
    ASSIGNED = "ASSIGNED"
    ACTIVE = "ACTIVE"
    RETURNING = "RETURNING"
    FAULT = "FAULT"
    ESTOP = "ESTOP"


class Cobot(Base, TimestampMixin):
    __tablename__ = "cobots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    site_id: Mapped[str] = mapped_column(String(36), ForeignKey("sites.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str] = mapped_column(String(100), default="Universal Robots UR10e", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default=CobotStatus.IDLE.value, nullable=False)
    ip_address: Mapped[str] = mapped_column(String(50), nullable=True)
    payload_capacity_kg: Mapped[float] = mapped_column(default=12.5, nullable=False)
    # Human-readable description of the currently assigned task (NULL when IDLE)
    current_task_description: Mapped[str] = mapped_column(Text, nullable=True)

    site = relationship("Site", back_populates="cobots")
    tasks = relationship("CobotTask", back_populates="cobot", cascade="all, delete-orphan")


class CobotTask(Base, TimestampMixin):
    __tablename__ = "cobot_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cobot_id: Mapped[str] = mapped_column(String(36), ForeignKey("cobots.id"), nullable=False)
    decision_log_id: Mapped[str] = mapped_column(String(36), nullable=True)
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="IN_PROGRESS", nullable=False)
    execution_notes: Mapped[str] = mapped_column(Text, nullable=True)

    cobot = relationship("Cobot", back_populates="tasks")
