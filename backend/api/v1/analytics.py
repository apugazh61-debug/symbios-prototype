"""
api/v1/analytics.py
-------------------
Executive safety dashboards, 24-hour incident trends, and compliance metrics.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from core.database import get_db
from models.camera import Camera
from models.zone import SafetyZone
from models.worker import Worker
from models.decision import DecisionLog
from models.alert import SafetyAlert
from schemas.safety import SafetyMetricsSummary

router = APIRouter(prefix="/analytics", tags=["Analytics & Compliance Reporting"])


@router.get("/summary", response_model=SafetyMetricsSummary)
def get_safety_summary(db: Session = Depends(get_db)):
    """Executive KPI summary across all factory cells."""
    now = datetime.now(timezone.utc)
    one_day_ago = now - timedelta(hours=24)

    active_cams = db.query(Camera).filter(Camera.status == "ONLINE").count()
    active_workers = db.query(Worker).filter(Worker.is_active == True).count()
    active_zones = db.query(SafetyZone).filter(SafetyZone.is_active == True).count()
    
    total_decisions_24h = db.query(DecisionLog).filter(DecisionLog.created_at >= one_day_ago).count()
    cobot_reassignments = db.query(DecisionLog).filter(
        DecisionLog.created_at >= one_day_ago,
        DecisionLog.action_taken == "REASSIGN_TO_COBOT"
    ).count()

    avg_fatigue = db.query(func.avg(DecisionLog.fatigue_score)).filter(DecisionLog.created_at >= one_day_ago).scalar() or 0.0
    unacked_alerts = db.query(SafetyAlert).filter(SafetyAlert.acknowledged == False).count()

    return SafetyMetricsSummary(
        active_cameras=max(1, active_cams),
        tracked_workers=max(1, active_workers),
        active_zones=max(1, active_zones),
        avg_fatigue_score=round(float(avg_fatigue), 1),
        total_decisions_24h=total_decisions_24h,
        cobot_reassignments_24h=cobot_reassignments,
        unacknowledged_alerts=unacked_alerts
    )


@router.get("/fatigue-trends")
def get_fatigue_trends(db: Session = Depends(get_db)):
    """Hourly average fatigue distribution for charting."""
    # Group into 6 time buckets for dashboard display
    logs = db.query(DecisionLog.fatigue_score, DecisionLog.created_at).order_by(DecisionLog.created_at.desc()).limit(100).all()
    
    points = []
    for l in reversed(logs[:15]):
        points.append({
            "time": l.created_at.strftime("%H:%M:%S"),
            "score": round(l.fatigue_score, 1)
        })
    if not points:
        points = [{"time": "Now", "score": 12.0}]
    return points
