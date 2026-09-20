"""
api/v1/alerts.py
----------------
Real-time supervisor safety alerts, acknowledgment, and escalation endpoints.
"""

from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import RoleChecker, UserRole, TokenPayload
from models.alert import SafetyAlert
from models.audit import AuditLog
from schemas.safety import AlertResponse, AlertAcknowledgeRequest

router = APIRouter(prefix="/alerts", tags=["Safety Alerts & Escalations"])


@router.get("", response_model=List[AlertResponse])
def list_alerts(
    unacknowledged_only: bool = True,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """List safety alerts for supervisor dashboard."""
    query = db.query(SafetyAlert)
    if unacknowledged_only:
        query = query.filter(SafetyAlert.acknowledged == False)
    alerts = query.order_by(SafetyAlert.created_at.desc()).limit(limit).all()
    return alerts


@router.post("/{alert_id}/acknowledge", response_model=AlertResponse)
def acknowledge_alert(
    alert_id: str,
    req: AlertAcknowledgeRequest,
    token: TokenPayload = Depends(RoleChecker([UserRole.ADMIN, UserRole.SUPERVISOR])),
    db: Session = Depends(get_db)
):
    """Supervisor marks an active alert as investigated and resolved."""
    alert = db.query(SafetyAlert).filter(SafetyAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Safety alert not found")

    alert.acknowledged = True
    alert.acknowledged_by_user_id = token.sub
    alert.acknowledged_at = datetime.now(timezone.utc)

    audit = AuditLog(
        user_id=token.sub,
        user_email=token.email,
        action="ACKNOWLEDGE_SAFETY_ALERT",
        entity_type="SafetyAlert",
        entity_id=alert.id,
        changes_json=f"Notes: {req.notes}"
    )
    db.add(audit)
    db.commit()
    db.refresh(alert)
    return alert
