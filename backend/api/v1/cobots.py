"""
api/v1/cobots.py
----------------
Collaborative robot fleet monitoring, state machine management, and supervisor reset.

State machine endpoints:
  GET  /cobots                    — list all cobots with live status
  GET  /cobots/{id}/status        — single cobot status + task description
  POST /cobots/{id}/reset         — supervisor override back to IDLE (any state)
  POST /cobots/{id}/activate      — ASSIGNED → ACTIVE  (simulates OPC-UA/MQTT ack in dev)
  POST /cobots/{id}/complete      — ACTIVE → RETURNING (task finished)
  POST /cobots/{id}/dock          — RETURNING → IDLE   (home position confirmed)
"""

import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import RoleChecker, UserRole, TokenPayload
from models.cobot import Cobot, CobotStatus
from engine.cobot_scheduler import cobot_scheduler, InvalidTransition
from api.v1.audit import record_audit_log
from schemas.safety import CobotResponse

router = APIRouter(prefix="/cobots", tags=["Cobot Fleet & Automation"])


def _sync_db_cobot(db: Session, cobot_id: str, new_status: str,
                   task_desc: str | None = None) -> Cobot:
    """Updates the DB cobot record and returns it. Raises 404 if not found."""
    cobot = db.query(Cobot).filter(Cobot.id == cobot_id).first()
    if not cobot:
        raise HTTPException(status_code=404, detail="Cobot station not found")
    cobot.status = new_status
    cobot.current_task_description = task_desc
    return cobot


@router.get("", response_model=List[CobotResponse])
def list_cobots(db: Session = Depends(get_db)):
    """List all deployed cobots with live status from the state machine."""
    cobots = db.query(Cobot).all()
    # Mirror in-memory scheduler state into DB objects for the response
    for c in cobots:
        rec = cobot_scheduler.get_status(c.id)
        if rec:
            c.status = rec.status
            c.current_task_description = rec.active_task
    return cobots


@router.get("/{cobot_id}/status")
def get_cobot_status(cobot_id: str, db: Session = Depends(get_db)):
    """Return current state machine status and task description for a single cobot."""
    cobot = db.query(Cobot).filter(Cobot.id == cobot_id).first()
    if not cobot:
        raise HTTPException(status_code=404, detail="Cobot station not found")
    rec = cobot_scheduler.get_status(cobot_id)
    return {
        "id": cobot_id,
        "name": cobot.name,
        "status": rec.status if rec else cobot.status,
        "current_task_description": rec.active_task if rec else cobot.current_task_description,
        "worker_id": rec.worker_id if rec else None,
        "zone_name": rec.zone_name if rec else None,
        "assigned_at": rec.assigned_at.isoformat() if (rec and rec.assigned_at) else None,
    }


@router.post("/{cobot_id}/reset")
def reset_cobot_status(
    cobot_id: str,
    token: TokenPayload = Depends(RoleChecker([UserRole.ADMIN, UserRole.SUPERVISOR])),
    db: Session = Depends(get_db)
):
    """
    Supervisor override: forcibly return cobot to IDLE from any state.
    This is a deliberate escape hatch — it bypasses the normal arc and is
    always permitted to authorised supervisors.  All such overrides are
    logged to the immutable audit trail.
    """
    cobot = db.query(Cobot).filter(Cobot.id == cobot_id).first()
    if not cobot:
        raise HTTPException(status_code=404, detail="Cobot station not found")

    prev_status = cobot.status
    rec = cobot_scheduler.reset_to_idle(cobot_id)
    cobot.status = CobotStatus.IDLE.value
    cobot.current_task_description = None

    who = token.email
    record_audit_log(
        db=db,
        action="COBOT_RESET",
        message=(
            f"Supervisor manual override: reset {cobot.name} from "
            f"{prev_status} → IDLE by {who}."
        ),
        severity="info",
        entity_type="Cobot",
        entity_id=cobot.id,
        user_email=who,
        changes_json=json.dumps({
            "previous_status": prev_status,
            "new_status": CobotStatus.IDLE.value,
            "reset_by": who
        })
    )

    db.commit()
    return {
        "message": f"Cobot {cobot.name} successfully reset from {prev_status} to IDLE.",
        "cobot_id": cobot_id,
        "new_status": CobotStatus.IDLE.value
    }


@router.post("/{cobot_id}/activate")
def activate_cobot(
    cobot_id: str,
    token: TokenPayload = Depends(RoleChecker([UserRole.ADMIN, UserRole.SUPERVISOR])),
    db: Session = Depends(get_db)
):
    """
    ASSIGNED → ACTIVE: Cobot has arrived at workstation.
    In production this fires on OPC-UA/MQTT acknowledgment from the robot controller.
    In development it can be called manually to advance the simulation.
    """
    cobot = db.query(Cobot).filter(Cobot.id == cobot_id).first()
    if not cobot:
        raise HTTPException(status_code=404, detail="Cobot station not found")

    try:
        rec = cobot_scheduler.activate(cobot_id)
    except InvalidTransition as e:
        raise HTTPException(status_code=409, detail=str(e))

    cobot.status = CobotStatus.ACTIVE.value

    record_audit_log(
        db=db,
        action="COBOT_ACTIVATED",
        message=f"{cobot.name} arrived at workstation and is now ACTIVE.",
        severity="info",
        entity_type="Cobot",
        entity_id=cobot.id,
        user_email=token.email,
        changes_json=json.dumps({
            "previous_status": CobotStatus.ASSIGNED.value,
            "new_status": CobotStatus.ACTIVE.value,
            "task": rec.active_task
        })
    )

    db.commit()
    return {
        "message": f"Cobot {cobot.name} is now ACTIVE at the workstation.",
        "cobot_id": cobot_id,
        "new_status": CobotStatus.ACTIVE.value,
        "task": rec.active_task
    }


@router.post("/{cobot_id}/complete")
def complete_cobot_task(
    cobot_id: str,
    token: TokenPayload = Depends(RoleChecker([UserRole.ADMIN, UserRole.SUPERVISOR])),
    db: Session = Depends(get_db)
):
    """
    ACTIVE → RETURNING: Cobot completed the task and is heading back to home position.
    In production this fires on a task-completion signal from the robot controller.
    """
    cobot = db.query(Cobot).filter(Cobot.id == cobot_id).first()
    if not cobot:
        raise HTTPException(status_code=404, detail="Cobot station not found")

    try:
        rec = cobot_scheduler.complete(cobot_id)
    except InvalidTransition as e:
        raise HTTPException(status_code=409, detail=str(e))

    cobot.status = CobotStatus.RETURNING.value

    record_audit_log(
        db=db,
        action="COBOT_TASK_COMPLETE",
        message=f"{cobot.name} task completed. Now RETURNING to home position.",
        severity="info",
        entity_type="Cobot",
        entity_id=cobot.id,
        user_email=token.email,
        changes_json=json.dumps({
            "previous_status": CobotStatus.ACTIVE.value,
            "new_status": CobotStatus.RETURNING.value,
        })
    )

    db.commit()
    return {
        "message": f"Cobot {cobot.name} task complete. Returning to home position.",
        "cobot_id": cobot_id,
        "new_status": CobotStatus.RETURNING.value
    }


@router.post("/{cobot_id}/dock")
def dock_cobot(
    cobot_id: str,
    token: TokenPayload = Depends(RoleChecker([UserRole.ADMIN, UserRole.SUPERVISOR])),
    db: Session = Depends(get_db)
):
    """
    RETURNING → IDLE: Cobot has docked at its home position and is ready again.
    In production this fires on a dock-home acknowledgment from the robot controller.
    """
    cobot = db.query(Cobot).filter(Cobot.id == cobot_id).first()
    if not cobot:
        raise HTTPException(status_code=404, detail="Cobot station not found")

    try:
        cobot_scheduler.dock_home(cobot_id)
    except InvalidTransition as e:
        raise HTTPException(status_code=409, detail=str(e))

    cobot.status = CobotStatus.IDLE.value
    cobot.current_task_description = None

    record_audit_log(
        db=db,
        action="COBOT_DOCKED",
        message=f"{cobot.name} has docked at home position. Status: IDLE.",
        severity="info",
        entity_type="Cobot",
        entity_id=cobot.id,
        user_email=token.email,
        changes_json=json.dumps({
            "previous_status": CobotStatus.RETURNING.value,
            "new_status": CobotStatus.IDLE.value,
        })
    )

    db.commit()
    return {
        "message": f"Cobot {cobot.name} docked. Ready for next assignment.",
        "cobot_id": cobot_id,
        "new_status": CobotStatus.IDLE.value
    }
