"""
api/v1/decisions.py
-------------------
Core perception, safety evaluation, and orchestration pipeline.
Ingests camera frames, tracks workers, tests polygon zones, dispatches cobots, and records immutable audit trails.
"""

import base64
import time
import json
from typing import List, Optional
import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from core.logging import logger
from models.zone import SafetyZone
from models.camera import Camera
from models.decision import DecisionLog, SafetyAction
from models.alert import SafetyAlert
from models.cobot import Cobot, CobotStatus, CobotTask
from models.audit import AuditLog
from schemas.safety import (
    FrameAnalysisRequest,
    FrameAnalysisResponse,
    TrackedWorkerState,
    DecisionPayload,
    ZoneResponse
)
from perception.tracker import MultiWorkerPipeline
from engine.rules_v1 import RuleBasedDecisionEngine
from engine.interface import WorkerStateContext
from engine.cobot_scheduler import cobot_scheduler
from alerting.dispatcher import alert_dispatcher

router = APIRouter(prefix="/decisions", tags=["Perception & Decision Pipeline"])

# Singleton perception pipeline and decision engine instances
pipeline = MultiWorkerPipeline()
decision_engine = RuleBasedDecisionEngine()

# Cache most recent decision in memory for instant copilot access
last_global_decision = {
    "action": "NORMAL",
    "severity": "info",
    "rule_fired": "RULE_NORMAL",
    "fatigue_score": 0.0,
    "in_zone": False,
    "zone_label": None,
    "cobot_reassigned": False,
    "cobot_name": None,
    "decision_log_id": None
}


def decode_image_frame(b64_string: str) -> Optional[np.ndarray]:
    """Decodes data URI or raw base64 JPEG into OpenCV BGR numpy array."""
    try:
        if "," in b64_string:
            b64_string = b64_string.split(",", 1)[1]
        img_bytes = base64.b64decode(b64_string)
        np_arr = np.frombuffer(img_bytes, np.uint8)
        return cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    except Exception as e:
        logger.error(f"Error decoding image frame: {e}")
        return None


@router.post("/analyze_frame", response_model=FrameAnalysisResponse)
async def analyze_frame(req: FrameAnalysisRequest, db: Session = Depends(get_db)):
    """
    Primary real-time processing endpoint:
    1. Synchronizes active polygon zones
    2. Runs multi-person skeletal tracking (zero-raw-video retention)
    3. Evaluates safety rules / cobot allocation
    4. Logs decision audit record and broadcasts alerts
    """
    global last_global_decision
    t_start = time.time()

    # 1. Decode frame
    frame_bgr = decode_image_frame(req.image_base64)
    if frame_bgr is None:
        return FrameAnalysisResponse(person_detected=False, error="Invalid base64 image data")

    # 2. Sync active safety zones for camera/site
    db_zones = db.query(SafetyZone).filter(SafetyZone.is_active == True).all()
    zone_dicts = []
    zone_responses = []
    for z in db_zones:
        coords = json.loads(z.polygon_geojson) if z.polygon_geojson else []
        zone_dicts.append({
            "id": z.id,
            "name": z.name,
            "risk_tier": z.risk_tier,
            "polygon_coordinates": coords,
            "reassignment_eligible": z.reassignment_eligible
        })
        zone_responses.append(
            ZoneResponse(
                id=z.id,
                camera_id=z.camera_id,
                site_id=z.site_id,
                name=z.name,
                risk_tier=z.risk_tier,
                polygon_coordinates=coords,
                color_hex=z.color_hex,
                reassignment_eligible=z.reassignment_eligible,
                version=z.version,
                is_active=z.is_active,
                created_at=z.created_at
            )
        )
    pipeline.update_zones(zone_dicts)

    # 3. Process frame via perception pipeline
    tracked_results = pipeline.process_frame(frame_bgr)
    # Memory safety: immediately drop reference to frame
    del frame_bgr

    if not tracked_results:
        return FrameAnalysisResponse(
            person_detected=False,
            tracked_workers=[],
            zones=zone_responses
        )

    # 4. Evaluate highest risk decision across all tracked workers
    worker_states: List[TrackedWorkerState] = []
    highest_priority_decision: Optional[DecisionPayload] = None
    triggering_worker_state: Optional[TrackedWorkerState] = None
    evaluated_workers: List[dict] = []
    priority_map = {"critical": 3, "warning": 2, "info": 1}

    # Retrieve current cobot context
    cobot_ctx = cobot_scheduler.get_context()

    # Carries rejection metadata to step 5 so the AuditLog/SafetyAlert can be
    # linked to the DecisionLog FK that is created there (after db.flush()).
    # All rejected dispatches across all workers in the frame are collected.
    failed_dispatches: List[dict] = []

    for w in tracked_results:
        ws = TrackedWorkerState(
            worker_id=w["worker_id"],
            center_norm=w["center_norm"],
            fatigue_score=w["fatigue_score"],
            slump_angle=w["slump_angle"],
            stillness_seconds=w["stillness_seconds"],
            in_zone=w["in_zone"],
            zone_name=w["zone_name"],
            zone_risk=w["zone_risk"]
        )
        worker_states.append(ws)

        # Build engine context
        worker_ctx = WorkerStateContext(
            worker_id=w["worker_id"],
            fatigue_score=w["fatigue_score"],
            slump_angle=w["slump_angle"],
            stillness_seconds=w["stillness_seconds"],
            in_zone=w["in_zone"],
            zone_label=w["zone_name"],
            zone_risk_tier=w["zone_risk"],
            reassignment_eligible=w["reassignment_eligible"],
            center_norm=w["center_norm"]
        )

        # Evaluate decision
        result = decision_engine.evaluate(worker_ctx, cobot_ctx)
        latency_ms = round((time.time() - t_start) * 1000.0, 2)

        # Handle cobot task dispatch if reassignment fired
        cobot_assigned_name = None
        if result.reassign_cobot and result.assigned_cobot_id:
            dispatch_res = cobot_scheduler.trigger_reassignment(
                result.assigned_cobot_id,
                w["worker_id"],
                w["zone_name"] or "Restricted Zone"
            )

            if dispatch_res.get("success"):
                # Only claim the cobot was assigned when the state machine
                # accepted the transition.  If it returned success=False (e.g. the
                # cobot was already ACTIVE when a second fatigue event fired), we
                # must NOT report cobot_assigned_name to the UI — that would be a
                # false safety signal.
                cobot_assigned_name = result.assigned_cobot_name

                # Update cobot in DB to ASSIGNED (first phase of state machine)
                db_cobot = db.query(Cobot).filter(Cobot.id == result.assigned_cobot_id).first()
                if db_cobot:
                    db_cobot.status = CobotStatus.ASSIGNED.value
                    db_cobot.current_task_description = dispatch_res.get("task")
                    task = CobotTask(
                        cobot_id=db_cobot.id,
                        task_name=f"Takeover workload from {w['worker_id']}",
                        trigger_reason=result.reason,
                        status="IN_PROGRESS"
                    )
                    db.add(task)
            else:
                # Assignment rejected — cobot not IDLE (e.g. already ACTIVE/RETURNING).
                # This is a SAFETY-CRITICAL gap: the decision engine ordered a task
                # handover, but automation could not execute it.  The worker remains
                # in the hazard zone with no executed mitigation.
                #
                # We log immediately (ephemeral) and append the rejection metadata
                # so it is persisted to the immutable AuditLog and a supervisor
                # SafetyAlert is created and broadcast for every affected worker.
                rejection_error = dispatch_res.get("error", "cobot unavailable")
                logger.warning(
                    f"[Decisions] COBOT DISPATCH FAILED for worker {w['worker_id']} "
                    f"in zone '{w['zone_name']}': {rejection_error}. "
                    "Supervisor alert will be raised."
                )
                failed_dispatches.append({
                    "worker_id": w["worker_id"],
                    "zone_name": w["zone_name"] or "Unknown Zone",
                    "fatigue_score": w["fatigue_score"],
                    "cobot_id": result.assigned_cobot_id,
                    "cobot_name": result.assigned_cobot_name,
                    "error": rejection_error,
                })

        payload = DecisionPayload(
            action=result.action,
            severity=result.severity,
            rule_fired=result.rule_fired,
            fatigue_score=w["fatigue_score"],
            in_zone=w["in_zone"],
            zone_label=w["zone_name"],
            cobot_reassigned=result.reassign_cobot,
            cobot_name=cobot_assigned_name,
            latency_ms=latency_ms
        )

        evaluated_workers.append({
            "payload": payload,
            "worker_state": ws,
            "cobot_assigned_name": cobot_assigned_name
        })

        if (highest_priority_decision is None or
                priority_map.get(payload.severity, 0) > priority_map.get(highest_priority_decision.severity, 0)):
            highest_priority_decision = payload
            triggering_worker_state = ws

    # 5. Persist decision log to database
    if highest_priority_decision and triggering_worker_state:
        cam_id = req.camera_id or (db_zones[0].camera_id if db_zones else "default-cam-01")
        site_id = db_zones[0].site_id if db_zones else "default-site-01"

        log_entry = DecisionLog(
            site_id=site_id,
            camera_id=cam_id,
            worker_id=triggering_worker_state.worker_id,
            zone_id=None,
            fatigue_score=highest_priority_decision.fatigue_score,
            slump_angle=triggering_worker_state.slump_angle,
            stillness_seconds=triggering_worker_state.stillness_seconds,
            in_zone=highest_priority_decision.in_zone,
            action_taken=highest_priority_decision.action.value,
            severity=highest_priority_decision.severity,
            rule_fired=highest_priority_decision.rule_fired,
            engine_version="rule_v1",
            execution_latency_ms=highest_priority_decision.latency_ms,
            raw_metrics_json=json.dumps({
                "worker_count": len(worker_states),
                "triggering_worker_id": triggering_worker_state.worker_id
            })
        )
        db.add(log_entry)
        db.flush()  # Generates log_entry.id for FK references below

        # Generate alert and audit log if warning or critical
        if highest_priority_decision.severity in ["warning", "critical"]:
            action_desc = highest_priority_decision.action.value.replace('_', ' ')
            msg = (
                f"{action_desc} enforced: Fatigue {highest_priority_decision.fatigue_score:.1f}/100, "
                f"Zone: {highest_priority_decision.zone_label or 'None'}"
            )
            if highest_priority_decision.cobot_reassigned:
                msg += f" -> Task reallocated to {highest_priority_decision.cobot_name or 'Cobot'}"

            alert = SafetyAlert(
                decision_log_id=log_entry.id,
                site_id=site_id,
                title=f"{action_desc} Detected",
                message=msg,
                severity=highest_priority_decision.severity,
                acknowledged=False
            )
            db.add(alert)

            # Persist to permanent relational AuditLog
            audit_entry = AuditLog(
                action=f"DECISION_{highest_priority_decision.action.value}",
                entity_type="DecisionLog",
                entity_id=log_entry.id,
                severity=highest_priority_decision.severity,
                message=msg,
                changes_json=json.dumps({
                    "action": highest_priority_decision.action.value,
                    "rule": highest_priority_decision.rule_fired,
                    "fatigue": highest_priority_decision.fatigue_score,
                    "in_zone": highest_priority_decision.in_zone,
                    "cobot_reassigned": highest_priority_decision.cobot_reassigned
                })
            )
            db.add(audit_entry)

            # Broadcast via WebSocket dispatcher
            await alert_dispatcher.dispatch_critical_alert({
                "alert_id": alert.id,
                "title": alert.title,
                "message": alert.message,
                "severity": alert.severity,
                "timestamp": time.time()
            })

        # ── Cobot dispatch failure: persist audit trail + supervisor alert ────────
        # This block handles any cobot dispatch that was rejected by the state machine
        # (e.g. cobot already busy/ACTIVE). Every affected worker receives an immutable
        # AuditLog entry, a SafetyAlert in the supervisor queue, and an immediate WebSocket
        # broadcast so no safety hazard is silently dropped.
        for fi in failed_dispatches:
            failure_msg = (
                f"⚠ AUTOMATED COBOT REASSIGNMENT FAILED — MANUAL INTERVENTION REQUIRED. "
                f"Worker {fi['worker_id']} (fatigue {fi['fatigue_score']:.1f}/100) "
                f"is in zone '{fi['zone_name']}'. "
                f"Target cobot {fi['cobot_name'] or fi['cobot_id']} was unavailable "
                f"({fi['error']}). "
                "No automated mitigation has been executed. Supervisor must intervene immediately."
            )

            # 1. Immutable audit entry — CRITICAL severity, action distinct from the
            #    decision action so it is queryable independently in compliance reports.
            failure_audit = AuditLog(
                action="COBOT_DISPATCH_FAILED",
                entity_type="DecisionLog",
                entity_id=log_entry.id,
                severity="critical",
                message=failure_msg,
                changes_json=json.dumps({
                    "worker_id": fi["worker_id"],
                    "zone_name": fi["zone_name"],
                    "fatigue_score": fi["fatigue_score"],
                    "attempted_cobot_id": fi["cobot_id"],
                    "attempted_cobot_name": fi["cobot_name"],
                    "rejection_reason": fi["error"],
                    "decision_log_id": log_entry.id,
                })
            )
            db.add(failure_audit)

            # 2. SafetyAlert — surfaces in the supervisor dashboard alert queue
            #    with the same FK as the triggering decision log.
            failure_alert = SafetyAlert(
                decision_log_id=log_entry.id,
                site_id=site_id,
                title="Cobot Reassignment Failed — Manual Intervention Required",
                message=failure_msg,
                severity="critical",
                acknowledged=False
            )
            db.add(failure_alert)

            # 3. Broadcast immediately via WebSocket + Slack/Teams so on-call
            #    supervisors are notified even if the dashboard is not open.
            await alert_dispatcher.dispatch_critical_alert({
                "alert_id": failure_alert.id,
                "title": failure_alert.title,
                "message": failure_msg,
                "severity": "critical",
                "timestamp": time.time(),
                "worker_id": fi["worker_id"],
                "zone_name": fi["zone_name"],
                "action_required": "IMMEDIATE_SUPERVISOR_INTERVENTION",
            })
        # ── End dispatch failure handling ─────────────────────────────────────────

        # ── Concurrent worker hazard logging: log all workers with warning/critical ───
        for ev in evaluated_workers:
            other_ws = ev["worker_state"]
            other_payload = ev["payload"]
            # Skip the primary worker already persisted as highest_priority_decision
            if other_ws.worker_id == triggering_worker_state.worker_id:
                continue
            if other_payload.severity in ["warning", "critical"]:
                other_log = DecisionLog(
                    site_id=site_id,
                    camera_id=cam_id,
                    worker_id=other_ws.worker_id,
                    zone_id=None,
                    fatigue_score=other_payload.fatigue_score,
                    slump_angle=other_ws.slump_angle,
                    stillness_seconds=other_ws.stillness_seconds,
                    in_zone=other_payload.in_zone,
                    action_taken=other_payload.action.value,
                    severity=other_payload.severity,
                    rule_fired=other_payload.rule_fired,
                    engine_version="rule_v1",
                    execution_latency_ms=other_payload.latency_ms,
                    raw_metrics_json=json.dumps({
                        "worker_count": len(worker_states),
                        "worker_id": other_ws.worker_id,
                        "concurrent_hazard": True
                    })
                )
                db.add(other_log)
                db.flush()

                other_action_desc = other_payload.action.value.replace('_', ' ')
                other_msg = (
                    f"{other_action_desc} enforced: Worker {other_ws.worker_id}, "
                    f"Fatigue {other_payload.fatigue_score:.1f}/100, "
                    f"Zone: {other_payload.zone_label or 'None'}"
                )
                other_alert = SafetyAlert(
                    decision_log_id=other_log.id,
                    site_id=site_id,
                    title=f"{other_action_desc} Detected ({other_ws.worker_id})",
                    message=other_msg,
                    severity=other_payload.severity,
                    acknowledged=False
                )
                db.add(other_alert)

                other_audit = AuditLog(
                    action=f"DECISION_{other_payload.action.value}",
                    entity_type="DecisionLog",
                    entity_id=other_log.id,
                    severity=other_payload.severity,
                    message=other_msg,
                    changes_json=json.dumps({
                        "worker_id": other_ws.worker_id,
                        "action": other_payload.action.value,
                        "rule": other_payload.rule_fired,
                        "fatigue": other_payload.fatigue_score,
                        "in_zone": other_payload.in_zone
                    })
                )
                db.add(other_audit)

        try:
            db.commit()
        except Exception as commit_err:
            db.rollback()
            logger.error(f"[Decisions] Database commit failed: {commit_err}. Reverting cobot assignment.")
            if highest_priority_decision.cobot_reassigned and cobot_ctx.available_cobot_id:
                cobot_scheduler.reset_to_idle(cobot_ctx.available_cobot_id)
            raise

        highest_priority_decision.decision_log_id = log_entry.id

        # Update cached last decision
        last_global_decision = highest_priority_decision.dict()
        last_global_decision["decision_log_id"] = log_entry.id

    return FrameAnalysisResponse(
        person_detected=True,
        tracked_workers=worker_states,
        active_decision=highest_priority_decision,
        zones=zone_responses
    )



@router.get("/latest")
def get_latest_decision():
    """Returns the most recent automated decision."""
    return last_global_decision


@router.get("/history")
def get_decision_history(limit: int = 50, db: Session = Depends(get_db)):
    """Audit endpoint: queries immutable historical decision records."""
    logs = db.query(DecisionLog).order_by(DecisionLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": l.id,
            "timestamp": l.created_at.isoformat(),
            "worker_id": l.worker_id,
            "fatigue_score": l.fatigue_score,
            "in_zone": l.in_zone,
            "action": l.action_taken,
            "severity": l.severity,
            "rule_fired": l.rule_fired,
            "latency_ms": l.execution_latency_ms
        }
        for l in logs
    ]
