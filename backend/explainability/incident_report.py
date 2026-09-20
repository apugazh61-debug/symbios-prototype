"""
explainability/incident_report.py
---------------------------------
Formats industrial near-miss and automated intervention records into OSHA/ISO-45001 compliant packages.
"""

from datetime import datetime, timezone
import uuid
from typing import Dict, Any


def generate_compliance_incident_report(
    decision: Dict[str, Any],
    explanation_text: str,
    site_name: str = "Plant 01 - Advanced Manufacturing Facility",
    camera_name: str = "Cam-01 Overhead Cell 4"
) -> Dict[str, Any]:
    """
    Builds a structured audit report for EHS (Environmental Health & Safety) records.
    """
    now = datetime.now(timezone.utc)
    report_id = f"IR-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    return {
        "report_metadata": {
            "report_id": report_id,
            "generated_at": now.isoformat(),
            "standard_compliance": "ISO 45001:2018 (Section 8.1.2 - Eliminating hazards)",
            "jurisdiction_classification": "Automated Near-Miss Preemption Log"
        },
        "location_context": {
            "site": site_name,
            "camera_sensor": camera_name,
            "workcell": "Workstation C4 - Robotic Palletizing Cell"
        },
        "subject_privacy": {
            "worker_pseudonym": decision.get("worker_id", "OPERATOR_TOKEN_ANON_88"),
            "privacy_notice": "Identity pseudonymized in compliance with GDPR Art. 9 & Works Council Agreement."
        },
        "telemetry_metrics": {
            "fatigue_score": decision.get("fatigue_score", 0.0),
            "slump_angle_degrees": decision.get("slump_angle", 0.0),
            "stillness_duration_seconds": decision.get("stillness_seconds", 0.0),
            "safety_zone_breached": decision.get("zone_label") or "N/A"
        },
        "orchestration_action": {
            "action_enforced": decision.get("action", "NORMAL"),
            "severity_level": decision.get("severity", "info"),
            "rule_fired": decision.get("rule_fired", "RULE_NORMAL"),
            "cobot_dispatched": decision.get("cobot_name") or "None"
        },
        "audit_explanation": {
            "summary": explanation_text,
            "supervisor_review_status": "PENDING_ACKNOWLEDGMENT",
            "corrective_action_recommendation": (
                "Workload transferred to cobot. Operator scheduled for 15-minute ergonomic break. "
                "Inspect cell boundary sensors."
            )
        }
    }
