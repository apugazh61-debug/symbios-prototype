"""
api/v1/audit.py
---------------
Audit log retrieval and creation API for permanent audit trail persistence.
"""

from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from core.database import get_db
from models.audit import AuditLog

router = APIRouter(prefix="/audit", tags=["Audit Trail & Compliance"])


class AuditLogEntryCreate(BaseModel):
    action: str = Field(default="EVENT", example="SUPERVISOR_ACTION")
    entity_type: str = Field(default="System", example="System")
    entity_id: Optional[str] = None
    severity: str = Field(default="info", example="info")
    message: str = Field(..., example="Camera stream activated by supervisor.")
    changes_json: Optional[str] = None
    user_email: Optional[str] = None


class AuditLogEntryResponse(BaseModel):
    id: str
    action: str
    entity_type: str
    entity_id: Optional[str]
    severity: str
    message: Optional[str]
    user_email: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


def record_audit_log(
    db: Session,
    action: str,
    message: str,
    severity: str = "info",
    entity_type: str = "System",
    entity_id: Optional[str] = None,
    changes_json: Optional[str] = None,
    user_email: Optional[str] = None
) -> AuditLog:
    """Helper to persist an audit log entry in the relational database."""
    log = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        severity=severity,
        message=message,
        changes_json=changes_json,
        user_email=user_email
    )
    db.add(log)
    return log


@router.get("/logs", response_model=List[AuditLogEntryResponse])
def get_audit_logs(limit: int = 50, db: Session = Depends(get_db)):
    """Fetches persistent audit log records ordered newest first."""
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return logs


@router.post("/logs", response_model=AuditLogEntryResponse)
def create_audit_log(entry: AuditLogEntryCreate, db: Session = Depends(get_db)):
    """Persists an audit log event into the relational database."""
    log = record_audit_log(
        db=db,
        action=entry.action,
        message=entry.message,
        severity=entry.severity,
        entity_type=entry.entity_type,
        entity_id=entry.entity_id,
        changes_json=entry.changes_json,
        user_email=entry.user_email
    )
    db.commit()
    db.refresh(log)
    return log
