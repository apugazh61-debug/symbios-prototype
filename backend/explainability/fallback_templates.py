"""
explainability/fallback_templates.py
-----------------------------------
Deterministic, parameterized safety explanation templates.
Guarantees <10ms explanation generation with zero external network dependencies.
"""

from typing import Dict, Any


def generate_deterministic_explanation(decision: Dict[str, Any]) -> str:
    """
    Generates a clear, professional explanation of an automated safety decision.
    Compliant with industrial auditability and worker-transparency standards.
    """
    action = decision.get("action", "NORMAL")
    fatigue = decision.get("fatigue_score", 0.0)
    in_zone = decision.get("in_zone", False)
    zone_label = decision.get("zone_label") or decision.get("zone_name") or "Restricted Zone"
    cobot_name = decision.get("cobot_name") or "Universal Robot Cobot"

    if action == "EMERGENCY_STOP":
        return (
            f"CRITICAL SAFETY INTERVENTION: An immediate Emergency Stop (E-Stop) was triggered. "
            f"The worker breached '{zone_label}', which is classified as a Critical Hazard Zone. "
            f"All automated actuators in Cell 4 were immediately commanded to safe-halt. "
            f"Supervisor manual reset required after confirming zone clearance."
        )

    if action == "REASSIGN_TO_COBOT":
        return (
            f"AUTOMATED WORKLOAD REALLOCATION: The system detected that the operator reached a fatigue index of {fatigue}/100 "
            f"while operating inside the '{zone_label}' collaborative workspace. "
            f"To prevent repetitive-strain injury and pinch-point accidents, primary heavy payload handling "
            f"was dynamically transferred to {cobot_name}. "
            f"The operator has been transitioned to supervisory/light assembly mode."
        )

    if action == "ZONE_ALERT":
        return (
            f"SAFETY ZONE PROXIMITY ALERT: The worker entered the active safety envelope '{zone_label}'. "
            f"While current worker fatigue is within acceptable operating limits ({fatigue}/100), "
            f"this zone contains moving machinery. Visual caution indicators have been illuminated on the cell beacon."
        )

    if action == "MONITOR":
        return (
            f"ERGONOMIC FATIGUE ADVISORY: The worker's movement kinematics indicate elevated physical fatigue "
            f"(score: {fatigue}/100), characterized by trunk slump and reduced cycle velocity. "
            f"The worker remains in a safe zone clear of machinery. Continuous monitoring active; "
            f"a scheduled hydration/stretch break is recommended before the next high-rate cycle."
        )

    return (
        f"NOMINAL OPERATION: All biometric-kinematic parameters are within optimal safety bounds "
        f"(fatigue score: {fatigue}/100). The worker is operating outside all active hazard envelopes. "
        f"No automated interventions are required at this time."
    )
