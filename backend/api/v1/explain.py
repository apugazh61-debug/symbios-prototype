"""
api/v1/explain.py
-----------------
On-demand explainability and automated OSHA/ISO-45001 safety incident reports.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.database import get_db
from models.decision import DecisionLog, IncidentExplanation
from schemas.safety import ExplainRequest, ExplainResponse
from explainability.llm_copilot import explain_decision
from explainability.incident_report import generate_compliance_incident_report
from api.v1.decisions import last_global_decision

router = APIRouter(prefix="/explain", tags=["Explainability & Compliance"])


@router.post("", response_model=ExplainResponse)
def explain_automated_decision(req: ExplainRequest, db: Session = Depends(get_db)):
    """
    Generates plain-language explanation of why a safety action was executed.
    Taps into Anthropic Claude or seamlessly falls back to deterministic rules.
    """
    decision_payload = last_global_decision
    if req.decision_log_id:
        log = db.query(DecisionLog).filter(DecisionLog.id == req.decision_log_id).first()
        if log:
            decision_payload = {
                "action": log.action_taken,
                "severity": log.severity,
                "rule_fired": log.rule_fired,
                "fatigue_score": log.fatigue_score,
                "in_zone": log.in_zone,
                "zone_label": "Designated Safety Zone",
                "cobot_name": "Universal Robots UR10e"
            }

    explanation_text, source = explain_decision(decision_payload, use_llm=req.use_llm)

    # Save to database if log_id available
    if decision_payload.get("decision_log_id"):
        db_log_id = decision_payload["decision_log_id"]
        existing = db.query(IncidentExplanation).filter(IncidentExplanation.decision_log_id == db_log_id).first()
        if not existing:
            inc = IncidentExplanation(
                decision_log_id=db_log_id,
                explanation_text=explanation_text,
                source=source
            )
            db.add(inc)
            db.commit()

    return ExplainResponse(
        explanation=explanation_text,
        source=source,
        osha_ready=True
    )


@router.get("/incident-report")
def export_osha_incident_report(decision_log_id: str = None, db: Session = Depends(get_db)):
    """Exports structured ISO 45001 / OSHA Form 301 incident report package."""
    decision_payload = last_global_decision
    if decision_log_id:
        log = db.query(DecisionLog).filter(DecisionLog.id == decision_log_id).first()
        if not log:
            raise HTTPException(status_code=404, detail="Decision log not found")
        decision_payload = {
            "action": log.action_taken,
            "severity": log.severity,
            "rule_fired": log.rule_fired,
            "fatigue_score": log.fatigue_score,
            "in_zone": log.in_zone,
            "worker_id": log.worker_id,
            "zone_label": "High-Risk Robotic Arm Envelope"
        }

    explanation_text, _ = explain_decision(decision_payload, use_llm=False)
    report = generate_compliance_incident_report(decision_payload, explanation_text)
    return report
